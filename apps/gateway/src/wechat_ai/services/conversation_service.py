from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal


ConnectionFactory = Callable[[], sqlite3.Connection]
CursorKind = Literal["conversation", "message", "search", "knowledge"]

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_CONVERSATION_COLUMNS = """
    conversations.id AS id,
    conversations.source_conversation_id AS source_conversation_id,
    conversations.title AS title,
    conversations.kind AS kind,
    conversations.last_message_at AS last_message_at,
    conversations.message_count AS message_count,
    COALESCE(o.is_favorite, conversations.is_favorite, 0) AS is_favorite,
    COALESCE(o.is_archived, conversations.is_archived, 0) AS is_archived,
    COALESCE(o.note, conversations.note) AS note,
    COALESCE(o.is_indexing_enabled, conversations.is_indexing_enabled, 1) AS is_indexing_enabled,
    COALESCE(o.domain, conversations.domain) AS domain,
    COALESCE(o.project_id, conversations.project_id) AS project_id,
    COALESCE(o.project_role, conversations.project_role) AS project_role,
    COALESCE(o.classification_status, conversations.classification_status, 'needs_review') AS classification_status,
    COALESCE(source_accounts.source_app_id, 'wechat') AS source_app_id,
    COALESCE(
        conversation_origins.source_account_id,
        'wechat:default'
    ) AS source_account_id
"""
_CONVERSATION_FROM = """
    FROM conversations
    LEFT JOIN conversation_origins
      ON conversation_origins.conversation_id = conversations.id
    LEFT JOIN source_accounts
      ON source_accounts.id = conversation_origins.source_account_id
    LEFT JOIN conversation_overrides o
      ON o.conversation_id = conversations.id
"""


def indexing_enabled_predicate(table: str = "conversations") -> str:
    """SQL boolean predicate for the effective indexing-enabled flag.

    The derived ``conversation_overrides`` row wins over the raw column; when
    neither is set the conversation is treated as enabled.  Returns an
    expression to embed in a WHERE clause, e.g. ``{...} = 1``.  Use this on
    every read path (search / graph / knowledge projection / index) so disabling
    a conversation in the UI immediately removes it from retrieval.
    """
    return (
        "COALESCE("
        f"(SELECT o.is_indexing_enabled FROM conversation_overrides o "
        f"WHERE o.conversation_id = {table}.id), "
        f"{table}.is_indexing_enabled, 1)"
    )


class ConversationNotFound(LookupError):
    pass


class MessageNotFound(LookupError):
    pass


class InvalidCursor(ValueError):
    pass


def create_connection_factory(database_path: Path) -> ConnectionFactory:
    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # Python sqlite3 默认 busy_timeout 仅 5s；导入/媒体物化 worker 分批持写锁时，
        # gateway 的 BEGIN IMMEDIATE 与 HTTP 写请求会 5s 抢锁失败抛 "database is
        # locked"。统一拉到 30s，让慢写有机会提交后放行（operations_service 的私有
        # _open 此前已自行补 20s，此处一处生效覆盖所有走工厂的服务）。
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    return open_connection


def list_conversations(
    connection: sqlite3.Connection,
    *,
    limit: int,
    cursor: str | None,
    source_app_id: str | None = None,
    source_account_id: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    cursor_values = (
        decode_cursor(cursor, expected_kind="conversation") if cursor else None
    )
    if cursor_values is None:
        rows = connection.execute(
            f"""
            SELECT {_CONVERSATION_COLUMNS},
                   COALESCE(conversations.last_message_at, -1)
                       AS cursor_last_message_at
            {_CONVERSATION_FROM}
            WHERE (? IS NULL OR COALESCE(source_accounts.source_app_id, 'wechat') = ?)
              AND (? IS NULL OR COALESCE(conversation_origins.source_account_id, 'wechat:default') = ?)
              AND (? IS NULL OR conversations.kind = ?)
              AND (? = 1 OR COALESCE(o.is_archived, conversations.is_archived, 0) = 0)
            ORDER BY cursor_last_message_at DESC, id DESC
            LIMIT ?
            """,
            (
                source_app_id,
                source_app_id,
                source_account_id,
                source_account_id,
                kind,
                kind,
                int(include_archived),
                limit + 1,
            ),
        ).fetchall()
    else:
        cursor_last_message_at, cursor_id = cursor_values
        if not isinstance(cursor_id, str):
            raise InvalidCursor
        rows = connection.execute(
            f"""
            SELECT {_CONVERSATION_COLUMNS},
                   COALESCE(conversations.last_message_at, -1)
                       AS cursor_last_message_at
            {_CONVERSATION_FROM}
            WHERE (? IS NULL OR COALESCE(source_accounts.source_app_id, 'wechat') = ?)
              AND (? IS NULL OR COALESCE(conversation_origins.source_account_id, 'wechat:default') = ?)
              AND (? IS NULL OR conversations.kind = ?)
              AND (? = 1 OR COALESCE(o.is_archived, conversations.is_archived, 0) = 0)
              AND (
                    COALESCE(conversations.last_message_at, -1) < ?
                    OR (
                        COALESCE(conversations.last_message_at, -1) = ?
                        AND conversations.id < ?
                    )
              )
            ORDER BY cursor_last_message_at DESC, id DESC
            LIMIT ?
            """,
            (
                source_app_id,
                source_app_id,
                source_account_id,
                source_account_id,
                kind,
                kind,
                int(include_archived),
                cursor_last_message_at,
                cursor_last_message_at,
                cursor_id,
                limit + 1,
            ),
        ).fetchall()

    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            "conversation",
            int(last_row["cursor_last_message_at"]),
            str(last_row["id"]),
        )
    return {
        "items": [_conversation_from_row(row) for row in page_rows],
        "nextCursor": next_cursor,
    }


def get_conversation(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT {_CONVERSATION_COLUMNS}
        {_CONVERSATION_FROM}
        WHERE conversations.id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ConversationNotFound(conversation_id)
    return _conversation_from_row(row)


def update_conversation(
    connection: sqlite3.Connection,
    conversation_id: str,
    updates: Mapping[str, object],
) -> dict[str, Any]:
    """Apply human PATCH state to the DERIVED override table only.

    The raw ``conversations`` table is never written here.  Favorite / archived /
    note / indexing-enabled all live in ``conversation_overrides``; every read
    path COALESCEs the override over the base column.
    """
    columns = {
        "isFavorite": "is_favorite",
        "isArchived": "is_archived",
        "note": "note",
        "isIndexingEnabled": "is_indexing_enabled",
    }
    exists = connection.execute(
        "SELECT 1 FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if exists is None:
        raise ConversationNotFound(conversation_id)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    override_cols: list[str] = []
    values: list[object] = []
    conflict_sets: list[str] = []
    for field, value in updates.items():
        col = columns[field]
        override_cols.append(col)
        values.append(int(value) if field != "note" else value)
        conflict_sets.append(f"{col} = excluded.{col}")

    col_list = ", ".join(override_cols)
    placeholder_list = ", ".join("?" for _ in override_cols)
    connection.execute(
        f"""
        INSERT INTO conversation_overrides
            (conversation_id, {col_list}, updated_at)
        VALUES (?, {placeholder_list}, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            {", ".join(conflict_sets)},
            updated_at = excluded.updated_at
        """,
        (conversation_id, *values, now_iso),
    )

    if "isIndexingEnabled" in updates:
        from .graph_service import project_graph
        from .knowledge_projection_service import rebuild_retrieval_indexes

        rebuild_retrieval_indexes(connection, conversation_ids=(conversation_id,))
        project_graph(connection, conversation_ids=(conversation_id,))
    connection.commit()
    return get_conversation(connection, conversation_id)


def list_messages(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    limit: int,
    cursor: str | None,
    target_message_id: int | None = None,
    asset_root: Path | None = None,
) -> dict[str, Any]:
    exists = connection.execute(
        "SELECT 1 FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if exists is None:
        raise ConversationNotFound(conversation_id)

    cursor_values = (
        decode_cursor(cursor, expected_kind="message") if cursor else None
    )
    if target_message_id is not None and cursor_values is None:
        target = connection.execute(
            """
            SELECT sent_at, id
            FROM messages
            WHERE conversation_id = ? AND id = ?
            """,
            (conversation_id, target_message_id),
        ).fetchone()
        if target is None:
            raise MessageNotFound(target_message_id)
        rows = connection.execute(
            """
            SELECT id, conversation_id, source_message_id, sender_id,
                   sender_name, sent_at, type, content
            FROM messages
            WHERE conversation_id = ?
              AND (
                    sent_at < ?
                    OR (sent_at = ? AND id <= ?)
              )
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
            """,
            (
                conversation_id,
                int(target["sent_at"]),
                int(target["sent_at"]),
                int(target["id"]),
                limit + 1,
            ),
        ).fetchall()
    elif cursor_values is None:
        rows = connection.execute(
            """
            SELECT id, conversation_id, source_message_id, sender_id,
                   sender_name, sent_at, type, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, limit + 1),
        ).fetchall()
    else:
        cursor_sent_at, cursor_id = cursor_values
        if not isinstance(cursor_id, int):
            raise InvalidCursor
        rows = connection.execute(
            """
            SELECT id, conversation_id, source_message_id, sender_id,
                   sender_name, sent_at, type, content
            FROM messages
            WHERE conversation_id = ?
              AND (
                    sent_at < ?
                    OR (sent_at = ? AND id < ?)
              )
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
            """,
            (
                conversation_id,
                cursor_sent_at,
                cursor_sent_at,
                cursor_id,
                limit + 1,
            ),
        ).fetchall()

    page_rows = rows[:limit]
    assets_by_message = _assets_by_message(
        connection,
        [int(row["id"]) for row in page_rows],
        asset_root=asset_root,
    )
    next_cursor = None
    if len(rows) > limit:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            "message",
            int(last_row["sent_at"]),
            int(last_row["id"]),
        )
    return {
        "items": [
            _message_from_row(
                row,
                assets=assets_by_message.get(int(row["id"]), []),
            )
            for row in page_rows
        ],
        "nextCursor": next_cursor,
    }


def encode_cursor(kind: CursorKind, sort_value: int, row_id: str | int) -> str:
    raw = json.dumps(
        {"v": 1, "kind": kind, "sort": sort_value, "id": row_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(
    cursor: str,
    *,
    expected_kind: CursorKind,
) -> tuple[int, str | int]:
    if not cursor or _CURSOR_PATTERN.fullmatch(cursor) is None:
        raise InvalidCursor
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursor from exc

    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or payload.get("kind") != expected_kind
        or not isinstance(payload.get("sort"), int)
        or isinstance(payload.get("sort"), bool)
        or not isinstance(payload.get("id"), (str, int))
        or isinstance(payload.get("id"), bool)
    ):
        raise InvalidCursor
    return int(payload["sort"]), payload["id"]


def _conversation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sourceConversationId": row["source_conversation_id"],
        "title": row["title"],
        "sourceAppId": row["source_app_id"],
        "sourceAccountId": row["source_account_id"],
        "kind": row["kind"],
        "lastMessageAt": row["last_message_at"],
        "messageCount": row["message_count"],
        "isFavorite": bool(row["is_favorite"]),
        "isArchived": bool(row["is_archived"]),
        "isIndexingEnabled": bool(row["is_indexing_enabled"]),
        "domain": row["domain"],
        "projectId": row["project_id"],
        "projectRole": row["project_role"],
        "classificationStatus": row["classification_status"],
        "note": row["note"],
    }


def _message_from_row(
    row: sqlite3.Row,
    *,
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from ..message_display import safe_message_display

    message_type, content = safe_message_display(row["type"], row["content"])
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "sourceMessageId": row["source_message_id"],
        "senderId": row["sender_id"],
        "senderName": row["sender_name"],
        "sentAt": row["sent_at"],
        "type": message_type,
        "content": content,
        "assets": assets or [],
    }


def _assets_by_message(
    connection: sqlite3.Connection,
    message_ids: list[int],
    *,
    asset_root: Path | None,
) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    placeholders = ", ".join("?" for _item in message_ids)
    rows = connection.execute(
        f"""
        SELECT
            message_parts.message_id,
            assets.id,
            assets.part_id,
            assets.kind,
            assets.status,
            assets.file_name,
            assets.mime_type,
            assets.byte_size,
            assets.external_url,
            assets.local_path
        FROM message_parts
        JOIN assets ON assets.part_id = message_parts.id
        WHERE message_parts.message_id IN ({placeholders})
        ORDER BY message_parts.message_id, assets.ordinal, assets.id
        """,
        message_ids,
    ).fetchall()
    from .knowledge_service import asset_from_row

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["message_id"]), []).append(
            asset_from_row(row, asset_root=asset_root)
        )
    return grouped
