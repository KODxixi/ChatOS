"""Shared prompt and evidence formatting for local WeChat RAG answers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone


_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

def load_system_prompt(name: str) -> str:
    """Load a system prompt from system/<name>.md. Cached at module load."""
    from pathlib import Path
    p = Path(__file__).parent / "system" / f"{name}.md"
    import hashlib
    import logging
    text = p.read_text(encoding="utf-8").strip()
    logging.getLogger(__name__).info("prompt=%s md5=%s chars=%d", name, hashlib.md5(text.encode("utf-8")).hexdigest()[:8], len(text))
    return text


LOCAL_WECHAT_RAG_SYSTEM_PROMPT = load_system_prompt("rag-system")


def build_rag_user_prompt(
    question: str,
    contexts: Sequence[object],
) -> str:
    """Render the query and ranked evidence as an explicit context block."""

    blocks: list[str] = []
    for index, item in enumerate(contexts, start=1):
        score = getattr(item, "relevance_score", None)
        if score is None:
            score = getattr(item, "score", None)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            score_text = "N/A"
        else:
            score_text = f"{float(score):.2f}"
        part_id = getattr(item, "part_id", None)
        part_text = (
            f" | part_id: {part_id} | part={part_id}"
            if part_id is not None
            else ""
        )
        blocks.append(
            f"[Block {index}] | 时间: {_format_sent_at(getattr(item, 'sent_at', None))}"
            f" | 发送者: {getattr(item, 'sender_name', '未知')}"
            f" | 相关度得分: {score_text}"
            f" | 会话: {getattr(item, 'conversation_id', '未知')}"
            f" | message_id: {getattr(item, 'message_id', '未知')}"
            f"{part_text}\n"
            f"内容: {getattr(item, 'excerpt', '')}"
        )
    retrieved_context = "\n\n".join(blocks) or "（无检索结果）"
    return (
        "------------------- [检索上下文开始] -------------------\n"
        f"{retrieved_context}\n"
        "------------------- [检索上下文结束] -------------------\n\n"
        f"User Query\n用户问题：{question}"
    )


def _format_sent_at(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "未知"
    timestamp = float(value)
    if abs(timestamp) > 10_000_000_000:
        timestamp /= 1_000
    try:
        return datetime.fromtimestamp(timestamp, tz=_SHANGHAI_TZ).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (OverflowError, OSError, ValueError):
        return "未知"
