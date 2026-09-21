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

# 本地优先；不可达时回退 APIYI。
# 2026-09-21：默认值由 LM Studio(1234, 已停用) + qwen/qwen3-vl-8b(模型已删)
#   改为 Muse Glimmer 8080。此前未设环境变量时会先打一个死端口再回退云端。
LOCAL_BASE = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "muse-glimmer-30b")
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


def is_available(*, local_only: bool = False) -> bool:
    # local LM Studio needs no key; fallback needs APIYI key
    if local_only:
        return _use_local_llm
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
    max_tokens: int = 4096,  # 2026-09-21: 本地模型是推理模型，先出 reasoning_content；
                             # 400 会被 reasoning 吃光导致 content 为空（且空串不抛异常、跳过云端兜底）
    local_only: bool = False,
) -> str:
    """Non-streaming chat completion. Raises OpenAICompatibleUnavailable on failure.

    When *local_only* is True, only the local LM Studio endpoint is tried;
    the external APIYI fallback is disabled (used by organize pipeline to
    prevent chat content from being sent externally).
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
    global _use_local_llm
    last = None
    tries = []
    if _use_local_llm:
        tries.append((LOCAL_BASE, LOCAL_MODEL, None, "local"))
    if LLM_KEY and not local_only:
        tries.append((FALLBACK_BASE, FALLBACK_MODEL, LLM_KEY, "apiyi"))
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
                _use_local_llm = False
                logger.warning("local LLM unreachable: %s", exc)
    logger.warning("LLM call failed (local_only=%s): %s", local_only, last)
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
    if not items:
        return items
        from ..rag_prompt import load_system_prompt
    system = load_system_prompt("todo-filter-system")
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
