"""Lightweight non-streaming LLM chat completion + DB cache.

Uses an OpenAI-compatible relay (APIYI / claude-haiku) when a key is present,
and silently falls back to the caller's rule-based result on any failure.
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

# Prefer local LM Studio; fall back to APIYI if unreachable.
LOCAL_BASE = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen/qwen3-vl-8b")
FALLBACK_BASE = "https://api.apiyi.com/v1"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
LLM_KEY = os.environ.get("APIYI_API_KEY") or os.environ.get("CC_APIYI_KEY")
LLM_TIMEOUT = 30.0
_use_local_llm = True


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


def is_available() -> bool:
    # local LM Studio needs no key; fallback needs APIYI key
    return bool(_use_local_llm or LLM_KEY)


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


def complete(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> str:
    """Non-streaming chat completion. Raises OpenAICompatibleUnavailable on failure."""
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    global _use_local_llm
    last = None
    tries = []
    if _use_local_llm:
        tries.append((LOCAL_BASE, LOCAL_MODEL, None, "local"))
    if LLM_KEY:
        tries.append((FALLBACK_BASE, FALLBACK_MODEL, LLM_KEY, "apiyi"))
    for base, model, key, tag in tries:
        try:
            p = dict(payload); p["model"] = model
            data = _try_call(base, model, key, p)
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = exc
            if tag == "local":
                _use_local_llm = False
                logger.warning("local LLM unreachable, falling back to APIYI: %s", exc)
    logger.warning("LLM call failed: %s", last)
    raise OpenAICompatibleUnavailable("LLM call failed") from last
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("LLM response malformed: %s", exc)
        raise OpenAICompatibleUnavailable("LLM response malformed") from exc


def summarize(messages_text: str) -> str | None:
    """Return an LLM summary, or None on any failure."""
    system = (
        "你是一个微信对话摘要助手。用1-2句自然中文概括对话：包含主题、关键结论、下一步行动。"
        "50-80字，不要说话人前缀，不要语气词，不要重复，不要表情。直接输出摘要，不要解释。"
    )
    try:
        out = complete(system, messages_text, max_tokens=150, temperature=0.2)
    except OpenAICompatibleUnavailable:
        return None
    out = out.strip().strip('"“”').strip()
    # strip accidental prefixes like "摘要："
    for pre in ("摘要：", "总结：", "摘要:", "总结:", "Summary:"):
        if out.startswith(pre):
            out = out[len(pre):].strip()
    return out or None


def filter_todos(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM secondary filter. items: [{"index","text"}]. Returns kept items with cleaned text.

    Raises OpenAICompatibleUnavailable when the model is unreachable so the
    caller can mark degradation.  JSON parse failures and empty keep results
    return the original items (valid responses, not failures).
    """
    if not items:
        return items
    system = (
        "你是待办事项过滤器。判断每条是否为真正待办（需要执行的未来动作/承诺/请求）。"
        "不是待办：已完成（过去式）、疑问句、预测估计、情绪、状态汇报、闲聊、讽刺玩笑。"
        "对每条只输出JSON对象：{\"index\":N,\"is_todo\":true/false,\"cleaned\":\"清洗后文本(10-50字,去表情去@)\"}。"
        "只输出JSON数组，不要解释。"
    )
    lines = "\n".join(f"{i}. {it['text']}" for i, it in enumerate(items))
    # Let OpenAICompatibleUnavailable propagate so caller marks degraded.
    raw = complete(system, lines, max_tokens=800, temperature=0.1)
    # extract JSON array
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return items
    try:
        arr = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return items
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
    if not keep:
        return items  # don't over-filter on bad output
    return [dict(items[i], text=keep[i]) for i in sorted(keep)]
