r"""Lightweight non-streaming LLM chat completion + DB cache.

只用本地推理（llama-swap 9123 · muse-glimmer-30b；8080 独立实例已于 2026-09-21 甲-1 迁入 llama-swap）。
本地不可达时抛 OpenAICompatibleUnavailable，由调用方静默降级到规则式结果。
**没有外部 API 兜底**（2026-09-21 删除）。端点真相源：`C:\AI\memory\_canonical\LOCAL-MODELS.md`。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any

from ..openai_compatible import OpenAICompatibleUnavailable

logger = logging.getLogger(__name__)

# 只用本地推理；不可达即抛错，由调用方降级到规则式结果。
# 2026-09-21：外部 API 兜底已删除（目标为零外部额度、数据不出本地）。
# 2026-09-22：默认端点改为 llama-swap 9123（甲-1 迁移后 8080 已停用）。
LOCAL_BASE = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:9123/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "muse-glimmer-30b")
# 2026-10-14 实测：冷加载 30B（含 dflash 草稿模型）首次 chat completion 要 **约 44 秒**，
# 而 llama-swap 给该模型设了 ttl=900 —— 间隔超过 15 分钟就必然回到冷态。原来的 30s 会把
# 「模型正在加载」误判成「端点不可达」，于是每次冷启动都判失败。留 4 倍余量。
LLM_TIMEOUT = 180.0
# 失败后的**冷却**，不是永久关闭：到点自动恢复。一次瞬时报错不该把整个进程的能力锁死
# （旧实现是 `_use_local_llm = False`，进程级永久生效，直到网关重启 —— 那才是
# 「整理/语义图谱一直 degraded」的真正成因）。
_LLM_COOLDOWN_SECONDS = 120.0
# 2026-10-15 用户裁定：**把 30B 剔除出 ChatOS 的依赖**。ChatOS 是 agent 驱动的检索/管道层，
# 不该自带生成模型；组织整理退回规则式（任务抽取本就不依赖它），图像理解交给 agent 自己的
# 多模态能力。默认关闭 → complete() 直接抛 OpenAICompatibleUnavailable，调用方按既有契约
# 静默降级，不再产生任何对 9123 的 chat 请求。要临时启用就把这里改回 True。
_use_local_llm = False
_llm_retry_at = 0.0     # 冷却截止时刻（time.monotonic 基准）


def _cache_key(task_type: str, reference_id: str, content: str) -> str:
    h = hashlib.sha256(f"{task_type}\0{reference_id}\0{content}".encode("utf-8")).hexdigest()
    return f"{task_type}:{reference_id}:{h}"


def get_cached(connection: sqlite3.Connection, task_type: str, reference_id: str, content: str) -> str | None:
    key = _cache_key(task_type, reference_id, content)
    now = int(time.time() * 1000)
    try:
        # opportunistic GC of expired rows
        connection.execute(
            "DELETE FROM llm_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        )
        row = connection.execute(
            "SELECT result FROM llm_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return row["result"]


def put_cached(
    connection: sqlite3.Connection,
    task_type: str,
    reference_id: str,
    content: str,
    result: str,
    *,
    ttl_ms: int | None = None,
) -> None:
    key = _cache_key(task_type, reference_id, content)
    now = int(time.time() * 1000)
    expires = (now + ttl_ms) if ttl_ms else None
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO llm_cache
                (cache_key, task_type, reference_id, result, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, task_type, reference_id, result, now, expires),
        )
    except sqlite3.OperationalError:
        # table may not exist yet before migration runs; ignore
        pass


def is_available(*, local_only: bool = False) -> bool:  # local_only 已无意义，保留形参兼容调用方
    """当前是否可以尝试本地模型。冷却期内返回 False，到点自动恢复（不会永久关闭）。"""
    return _use_local_llm and time.monotonic() >= _llm_retry_at


def _try_call(base: str, model: str, key: str | None, payload: dict) -> dict:
    hdrs = {"Content-Type": "application/json"}
    if key:
        hdrs["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=hdrs,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _failure_hint(exc: BaseException | None) -> str:
    """把常见端点故障翻译成可操作的排查提示，避免 401 这类难排查的错误。"""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return "（HTTP 401：本地端点要求 API key，检查 llama-swap 9123 是否配置了 key）"
        if exc.code == 404:
            return "（HTTP 404：模型名不存在，检查 settings 的 model 字段）"
        return f"（HTTP {exc.code}：检查本地端点 {LOCAL_BASE}）"
    # 超时必须单独判，且要排在 OSError 之前：TimeoutError 是 OSError 的子类，
    # 而且 urlopen 超时可能抛 URLError 包着 socket.timeout。否则会把"模型在冷加载"
    # 误报成"端点不可达"，把人引向完全错误的方向（2026-10-14 就被这么误导过）。
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return (
            f"（请求超时 >{LLM_TIMEOUT:.0f}s：本地模型冷加载实测约 44s，"
            "端点本身很可能正常 —— 先确认 llama-swap 里该模型是否刚被卸载）"
        )
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return "（端点不可达：检查 llama-swap 9123 是否启动）"
    return ""


def complete(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,  # 2026-09-21: 本地模型是推理模型，先出 reasoning_content；
                             # 400 会被 reasoning 吃光导致 content 为空（且空串不抛异常、跳过云端兜底）
    local_only: bool = False,
) -> str:
    """Non-streaming chat completion. Raises OpenAICompatibleUnavailable on failure.

    *local_only* 已无意义（没有外部路径可关），保留形参以兼容既有调用方。
    """
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    global _llm_retry_at
    last = None
    tries = []
    if is_available():
        tries.append((LOCAL_BASE, LOCAL_MODEL, None, "local"))
    for base, model, key, tag in tries:
        try:
            p = dict(payload); p["model"] = model
            if tag == "local":
                # 本地是推理模型；low 已够（archlib 打标实测参数），并显著缩短 reasoning
                p["reasoning_effort"] = "low"
            data = _try_call(base, model, key, p)
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = exc
            if tag == "local":
                _llm_retry_at = time.monotonic() + _LLM_COOLDOWN_SECONDS
                logger.warning(
                    "local LLM call failed: %s；冷却 %.0fs 后自动重试（不永久关闭）",
                    exc,
                    _LLM_COOLDOWN_SECONDS,
                )
    logger.warning("LLM call failed: %s%s", last, _failure_hint(last))
    raise OpenAICompatibleUnavailable("LLM call failed") from last


def summarize(messages_text: str) -> str | None:
    """Return an LLM summary, or None on any failure."""
    from ..rag_prompt import load_system_prompt
    system = load_system_prompt("summarize-system")
    try:
        out = complete(system, messages_text, max_tokens=4096, temperature=0.2)
    except OpenAICompatibleUnavailable:
        return None
    out = out.strip().strip('"“”').strip()
    # strip accidental prefixes like "摘要："
    for pre in ("摘要：", "总结：", "摘要:", "总结:", "Summary:"):
        if out.startswith(pre):
            out = out[len(pre):].strip()
    return out or None


def filter_todos(
    items: list[dict[str, Any]],
    *,
    local_only: bool = True,
) -> list[dict[str, Any]]:
    """LLM secondary filter. items: [{"index","text"}]. Returns kept items with cleaned text.

    Raises OpenAICompatibleUnavailable when:
    - the model is unreachable
    - the response is not parseable as JSON (invalid format → degrade, don't cache)

    Returns an empty list when the model legitimately filters everything out
    (valid empty result).  Returns filtered items with cleaned text otherwise.
    """
    from ..rag_prompt import load_system_prompt
    if not items:
        return items
    system = load_system_prompt("todo-filter-system")
    try:
        from . import personal_profile as _pp
        _frag = _pp.prompt_fragment()
        if _frag:
            system = system + _frag
    except Exception:  # noqa: BLE001 - personalization is optional
        pass
    lines = "\n".join(f"{i}. {it['text']}" for i, it in enumerate(items))
    raw = complete(system, lines, max_tokens=4096, temperature=0.1, local_only=local_only)
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        # Invalid format — no JSON array at all.
        raise OpenAICompatibleUnavailable("filter_todos: no JSON array in response")
    try:
        arr = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OpenAICompatibleUnavailable(f"filter_todos: JSON parse error: {exc}") from exc
    keep: dict[int, str] = {}
    for row in arr:
        try:
            idx = int(row["index"])
            if row.get("is_todo") and 0 <= idx < len(items):
                cleaned = str(row.get("cleaned") or items[idx]["text"]).strip()
                if 10 <= len(cleaned) <= 60:
                    keep[idx] = cleaned
        except (KeyError, ValueError, TypeError):
            continue
    # Valid response: either some kept or everything filtered out.
    return [dict(items[i], text=keep[i]) for i in sorted(keep)]
