from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .conversation_service import InvalidCursor, decode_cursor, encode_cursor


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class InvalidKnowledgeQuery(ValueError):
    pass


def list_sources(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            source_apps.id AS app_id,
            source_apps.display_name AS app_display_name,
            source_apps.connector_kind,
            source_accounts.id AS account_id,
            source_accounts.external_account_id,
            source_accounts.display_name AS account_display_name,
            source_accounts.status,
            source_accounts.projection_status,
            source_accounts.semantic_graph_status,
            source_accounts.semantic_graph_error_code,
            source_accounts.semantic_graph_updated_at,
            COUNT(DISTINCT conversation_origins.conversation_id) AS conversation_count,
            COUNT(DISTINCT messages.id) AS message_count
        FROM source_apps
        JOIN source_accounts
          ON source_accounts.source_app_id = source_apps.id
        LEFT JOIN conversation_origins
          ON conversation_origins.source_account_id = source_accounts.id
        LEFT JOIN messages
          ON messages.conversation_id = conversation_origins.conversation_id
        GROUP BY source_apps.id, source_accounts.id
        ORDER BY source_apps.display_name, source_apps.id,
                 source_accounts.display_name, source_accounts.id
        """
    ).fetchall()
    apps: dict[str, dict[str, Any]] = {}
    for row in rows:
        app_id = str(row["app_id"])
        app = apps.setdefault(
            app_id,
            {
                "id": app_id,
                "displayName": str(row["app_display_name"]),
                "connectorKind": str(row["connector_kind"]),
                "accounts": [],
            },
        )
        app["accounts"].append(
            {
                "id": str(row["account_id"]),
                "externalAccountId": str(row["external_account_id"]),
                "displayName": str(row["account_display_name"]),
                "status": str(row["status"]),
                "projectionStatus": str(row["projection_status"]),
                "semanticGraphStatus": str(row["semantic_graph_status"]),
                "semanticGraphErrorCode": (
                    str(row["semantic_graph_error_code"])
                    if row["semantic_graph_error_code"] is not None
                    else None
                ),
                "semanticGraphUpdatedAt": (
                    int(row["semantic_graph_updated_at"])
                    if row["semantic_graph_updated_at"] is not None
                    else None
                ),
                "conversationCount": int(row["conversation_count"]),
                "messageCount": int(row["message_count"]),
            }
        )
    return {"items": list(apps.values())}


def get_knowledge_facets(connection: sqlite3.Connection) -> dict[str, Any]:
    source_app_rows = connection.execute(
        """
        SELECT source_apps.id, source_apps.display_name,
               COUNT(DISTINCT message_parts.id) AS part_count
        FROM source_apps
        JOIN source_accounts
          ON source_accounts.source_app_id = source_apps.id
        JOIN conversation_origins
          ON conversation_origins.source_account_id = source_accounts.id
        JOIN messages
          ON messages.conversation_id = conversation_origins.conversation_id
        JOIN message_parts ON message_parts.message_id = messages.id
        WHERE message_parts.kind <> 'unsupported'
        GROUP BY source_apps.id, source_apps.display_name
        ORDER BY part_count DESC, source_apps.id
        """
    ).fetchall()
    source_account_rows = connection.execute(
        """
        SELECT source_accounts.id, source_accounts.source_app_id,
               source_accounts.display_name,
               COUNT(DISTINCT message_parts.id) AS part_count
        FROM source_accounts
        JOIN conversation_origins
          ON conversation_origins.source_account_id = source_accounts.id
        JOIN messages
          ON messages.conversation_id = conversation_origins.conversation_id
        JOIN message_parts ON message_parts.message_id = messages.id
        WHERE message_parts.kind <> 'unsupported'
        GROUP BY source_accounts.id, source_accounts.source_app_id,
                 source_accounts.display_name
        ORDER BY part_count DESC, source_accounts.id
        """
    ).fetchall()
    conversation_kind_rows = connection.execute(
        """
        SELECT conversations.kind,
               COUNT(DISTINCT message_parts.id) AS part_count
        FROM conversations
        JOIN messages ON messages.conversation_id = conversations.id
        JOIN message_parts ON message_parts.message_id = messages.id
        WHERE message_parts.kind <> 'unsupported'
        GROUP BY conversations.kind
        ORDER BY part_count DESC, conversations.kind
        """
    ).fetchall()
    content_kind_rows = connection.execute(
        """
        SELECT message_parts.kind, COUNT(*) AS part_count
        FROM message_parts
        WHERE message_parts.kind <> 'unsupported'
        GROUP BY message_parts.kind
        ORDER BY part_count DESC, message_parts.kind
        """
    ).fetchall()
    return {
        "sourceApps": [
            {
                "id": str(row[0]),
                "displayName": str(row[1]),
                "count": int(row[2]),
            }
            for row in source_app_rows
        ],
        "sourceAccounts": [
            {
                "id": str(row[0]),
                "sourceAppId": str(row[1]),
                "displayName": str(row[2]),
                "count": int(row[3]),
            }
            for row in source_account_rows
        ],
        "conversationKinds": [
            {"value": str(row[0]), "count": int(row[1])}
            for row in conversation_kind_rows
        ],
        "contentKinds": [
            {"value": str(row[0]), "count": int(row[1])}
            for row in content_kind_rows
        ],
    }


def list_knowledge_spaces(
    connection: sqlite3.Connection,
    *,
    source_app_id: str | None = None,
    source_account_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    parameters: list[object] = []
    if source_app_id is not None:
        where.append("source_accounts.source_app_id = ?")
        parameters.append(source_app_id)
    if source_account_id is not None:
        where.append("conversation_origins.source_account_id = ?")
        parameters.append(source_account_id)
    if kind is not None:
        where.append("conversations.kind = ?")
        parameters.append(kind)
    condition = " AND ".join(where) if where else "1 = 1"
    rows = connection.execute(
        f"""
        SELECT
            conversations.id AS conversation_id,
            conversations.title,
            conversations.kind,
            source_accounts.source_app_id,
            conversation_origins.source_account_id,
            COUNT(DISTINCT messages.id) AS message_count,
            COUNT(DISTINCT CASE
                WHEN message_parts.kind <> 'unsupported'
                    THEN message_parts.id
            END) AS part_count,
            COUNT(DISTINCT CASE
                WHEN message_parts.kind <> 'unsupported'
                    THEN assets.id
            END) AS asset_count,
            MAX(messages.sent_at) AS last_message_at
        FROM conversations
        JOIN conversation_origins
          ON conversation_origins.conversation_id = conversations.id
        JOIN source_accounts
          ON source_accounts.id = conversation_origins.source_account_id
        LEFT JOIN messages ON messages.conversation_id = conversations.id
        LEFT JOIN message_parts ON message_parts.message_id = messages.id
        LEFT JOIN assets ON assets.part_id = message_parts.id
        WHERE {condition}
        GROUP BY conversations.id,
                 source_accounts.source_app_id,
                 conversation_origins.source_account_id
        ORDER BY COALESCE(MAX(messages.sent_at), -1) DESC,
                 conversations.id DESC
        """,
        parameters,
    ).fetchall()
    return {
        "items": [
            {
                "conversationId": str(row["conversation_id"]),
                "title": str(row["title"]),
                "kind": str(row["kind"]),
                "sourceAppId": str(row["source_app_id"]),
                "sourceAccountId": str(row["source_account_id"]),
                "messageCount": int(row["message_count"]),
                "partCount": int(row["part_count"]),
                "assetCount": int(row["asset_count"]),
                "lastMessageAt": (
                    int(row["last_message_at"])
                    if row["last_message_at"] is not None
                    else None
                ),
            }
            for row in rows
        ]
    }


def list_knowledge_items(
    connection: sqlite3.Connection,
    *,
    conversation_id: str | None = None,
    source_app_id: str | None = None,
    source_account_id: str | None = None,
    kind: str | None = None,
    query: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    asset_root: Path | None = None,
) -> dict[str, Any]:
    retrieval_join = """
        LEFT JOIN retrieval_chunks
          ON retrieval_chunks.message_part_id = message_parts.id
    """
    where: list[str] = ["message_parts.kind <> 'unsupported'"]
    parameters: list[object] = []
    if conversation_id is not None:
        where.append("messages.conversation_id = ?")
        parameters.append(conversation_id)
    if source_app_id is not None:
        where.append("source_accounts.source_app_id = ?")
        parameters.append(source_app_id)
    if source_account_id is not None:
        where.append("conversation_origins.source_account_id = ?")
        parameters.append(source_account_id)
    if kind is not None:
        where.append("message_parts.kind = ?")
        parameters.append(kind)
    if query is not None:
        retrieval_join = """
            JOIN retrieval_chunks
              ON retrieval_chunks.message_part_id = message_parts.id
            JOIN retrieval_chunk_fts
              ON retrieval_chunk_fts.rowid = retrieval_chunks.id
        """
        if _CJK_PATTERN.search(query):
            terms = query.split()
            if not terms:
                raise InvalidKnowledgeQuery
            where.extend(
                "retrieval_chunks.text_content LIKE ? ESCAPE '\\'" for _term in terms
            )
            parameters.extend(f"%{_escape_like(term)}%" for term in terms)
        else:
            where.append("retrieval_chunk_fts MATCH ?")
            parameters.append(_to_fts_query(query))
    if cursor is not None:
        sent_at, part_id = decode_cursor(cursor, expected_kind="knowledge")
        if not isinstance(part_id, int):
            raise InvalidCursor
        where.append(
            """
            (
                messages.sent_at < ?
                OR (
                    messages.sent_at = ?
                    AND message_parts.id < ?
                )
            )
            """
        )
        parameters.extend((sent_at, sent_at, part_id))
    condition = " AND ".join(where) if where else "1 = 1"
    parameters.append(limit + 1)
    try:
        rows = connection.execute(
            f"""
            SELECT
                retrieval_chunks.id AS chunk_id,
                message_parts.id AS part_id,
                messages.id AS message_id,
                messages.conversation_id,
                conversations.title AS conversation_title,
                messages.sender_name,
                messages.sent_at,
                message_parts.kind,
                message_parts.status,
                CASE
                    WHEN message_parts.text_content <> ''
                        THEN message_parts.text_content
                    ELSE messages.content
                END AS text_content
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            JOIN conversations
              ON conversations.id = messages.conversation_id
            {retrieval_join}
            JOIN conversation_origins
              ON conversation_origins.conversation_id = conversations.id
            JOIN source_accounts
              ON source_accounts.id = conversation_origins.source_account_id
            WHERE {condition}
            ORDER BY messages.sent_at DESC,
                     message_parts.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise InvalidKnowledgeQuery from exc

    page_rows = rows[:limit]
    assets_by_part = _assets_by_part(
        connection,
        [int(row["part_id"]) for row in page_rows],
        asset_root=asset_root,
    )
    next_cursor = None
    if len(rows) > limit:
        last = page_rows[-1]
        next_cursor = encode_cursor(
            "knowledge",
            int(last["sent_at"]),
            int(last["part_id"]),
        )
    return {
        "items": [
            {
                "id": int(row["part_id"]),
                "chunkId": (
                    int(row["chunk_id"]) if row["chunk_id"] is not None else None
                ),
                "messageId": int(row["message_id"]),
                "conversationId": str(row["conversation_id"]),
                "conversationTitle": str(row["conversation_title"]),
                "senderName": str(row["sender_name"]),
                "sentAt": int(row["sent_at"]),
                "kind": str(row["kind"]),
                "status": str(row["status"]),
                "text": str(row["text_content"]),
                "assets": assets_by_part.get(int(row["part_id"]), []),
            }
            for row in page_rows
        ],
        "nextCursor": next_cursor,
    }


def _assets_by_part(
    connection: sqlite3.Connection,
    part_ids: list[int],
    *,
    asset_root: Path | None,
) -> dict[int, list[dict[str, Any]]]:
    if not part_ids:
        return {}
    placeholders = ", ".join("?" for _item in part_ids)
    rows = connection.execute(
        f"""
        SELECT id, part_id, kind, status, file_name, mime_type,
               byte_size, external_url, local_path
        FROM assets
        WHERE part_id IN ({placeholders})
        ORDER BY part_id, ordinal, id
        """,
        part_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["part_id"]), []).append(
            asset_from_row(row, asset_root=asset_root)
        )
    return grouped


def asset_from_row(
    row: sqlite3.Row,
    *,
    asset_root: Path | None,
) -> dict[str, Any]:
    asset_id = int(row["id"])
    safe_path = resolve_asset_path(
        asset_root,
        str(row["local_path"]) if row["local_path"] is not None else None,
    )
    return {
        "id": asset_id,
        "messagePartId": int(row["part_id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "externalUrl": None,
        "hasLocalFile": safe_path is not None,
        "fileName": str(row["file_name"]) if row["file_name"] is not None else None,
        "mimeType": str(row["mime_type"]) if row["mime_type"] is not None else None,
        "byteSize": int(row["byte_size"]) if row["byte_size"] is not None else None,
        "contentUrl": (
            f"/api/assets/{asset_id}"
            if row["status"] == "resolved" and safe_path is not None
            else None
        ),
    }


def resolve_asset_path(asset_root: Path | None, local_path: str | None) -> Path | None:
    if asset_root is None or not local_path:
        return None
    root = asset_root.resolve()
    candidate = (root / local_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _to_fts_query(query: str) -> str:
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        raise InvalidKnowledgeQuery
    return " AND ".join(f'"{term}"' for term in terms)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
