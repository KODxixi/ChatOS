from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from .conversation_service import (
    InvalidCursor,
    decode_cursor,
    encode_cursor,
    indexing_enabled_predicate,
)


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class InvalidSearchQuery(ValueError):
    pass


def search_messages(
    connection: sqlite3.Connection,
    *,
    query: str,
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None = None,
    sender_name: str | None,
    sent_at_from: int | None,
    sent_at_to: int | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    if _CJK_PATTERN.search(query):
        terms = query.split()
        if not terms:
            raise InvalidSearchQuery
        where = [
            "messages.content LIKE ? ESCAPE '\\'"
            for _term in terms
        ]
        parameters: list[object] = [
            f"%{_escape_like(term)}%" for term in terms
        ]
    else:
        where = ["message_fts MATCH ?"]
        parameters = [_to_fts_query(query)]

    # Conversations whose effective indexing flag (override wins over base)
    # is 0 are excluded from all search/retrieval paths, matching the
    # retrieval_chunks projection policy.
    where.append(f"{indexing_enabled_predicate()} = 1")

    if conversation_id is not None:
        where.append("messages.conversation_id = ?")
        parameters.append(conversation_id)
    if conversation_ids is not None:
        if conversation_ids:
            placeholders = ", ".join("?" for _item in conversation_ids)
            where.append(
                f"messages.conversation_id IN ({placeholders})"
            )
            parameters.extend(conversation_ids)
        else:
            where.append("0")
    if sender_name is not None:
        where.append("messages.sender_name = ?")
        parameters.append(sender_name)
    if sent_at_from is not None:
        where.append("messages.sent_at >= ?")
        parameters.append(sent_at_from)
    if sent_at_to is not None:
        where.append("messages.sent_at <= ?")
        parameters.append(sent_at_to)
    if cursor is not None:
        cursor_sent_at, cursor_id = decode_cursor(
            cursor,
            expected_kind="search",
        )
        if not isinstance(cursor_id, int):
            raise InvalidCursor
        where.append(
            """
            (
                messages.sent_at < ?
                OR (messages.sent_at = ? AND messages.id < ?)
            )
            """
        )
        parameters.extend((cursor_sent_at, cursor_sent_at, cursor_id))

    parameters.append(limit + 1)
    try:
        rows = connection.execute(
            f"""
            SELECT
                messages.id,
                messages.conversation_id,
                messages.source_message_id,
                messages.sender_id,
                messages.sender_name,
                messages.sent_at,
                messages.type,
                messages.content,
                conversations.title AS conversation_title
            FROM message_fts
            JOIN messages ON messages.id = message_fts.rowid
            JOIN conversations ON conversations.id = messages.conversation_id
            WHERE {" AND ".join(where)}
            ORDER BY messages.sent_at DESC, messages.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise InvalidSearchQuery from exc

    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            "search",
            int(last_row["sent_at"]),
            int(last_row["id"]),
        )
    return {
        "items": [_search_item_from_row(row) for row in page_rows],
        "nextCursor": next_cursor,
    }


def _to_fts_query(query: str) -> str:
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        raise InvalidSearchQuery
    return " AND ".join(f'"{term}"' for term in terms)


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _search_item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "sourceMessageId": row["source_message_id"],
        "senderId": row["sender_id"],
        "senderName": row["sender_name"],
        "sentAt": row["sent_at"],
        "type": row["type"],
        "content": row["content"],
        "conversationTitle": row["conversation_title"],
    }
