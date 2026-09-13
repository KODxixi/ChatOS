from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit


DEFAULT_WECHAT_ACCOUNT_ID = "wechat:default"
RETRIEVAL_INDEX_VERSION = 1
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_LINK_METADATA_MARKER = "knowledge_projection"



@dataclass(frozen=True, slots=True)
class ProjectionResult:
    conversations: int
    message_parts: int
    assets: int
    retrieval_chunks: int


def project_knowledge(
    connection: sqlite3.Connection,
    *,
    conversation_ids: Iterable[str] | None = None,
    source_account_id: str = DEFAULT_WECHAT_ACCOUNT_ID,
    build_indexes: bool = True,
) -> ProjectionResult:
    """Project normalized messages into idempotent knowledge facts.

    ``conversation_ids=None`` deliberately means the global library. An empty
    iterable means no conversations and never falls back to a current thread.
    Transaction ownership remains with the caller.
    """

    scope = _normalize_scope(conversation_ids)
    conversation_condition, conversation_parameters = _condition(
        "conversations.id",
        scope,
    )
    message_condition, message_parameters = _condition(
        "messages.conversation_id",
        scope,
    )

    connection.execute(
        """
        UPDATE source_apps
        SET display_name = CAST(X'E5BEAEE4BFA1' AS TEXT)
        WHERE id = 'wechat' AND display_name = 'WeChat'
        """
    )
    connection.execute(
        """
        UPDATE source_accounts
        SET display_name = CAST(X'E5BEAEE4BFA1' AS TEXT)
        WHERE id = 'wechat:default' AND display_name = 'WeChat'
        """
    )

    connection.execute(
        f"""
        INSERT OR IGNORE INTO conversation_origins(
            conversation_id,
            source_account_id,
            external_conversation_id
        )
        SELECT id, ?, source_conversation_id
        FROM conversations
        WHERE {conversation_condition}
        """,
        (source_account_id, *conversation_parameters),
    )

    connection.execute(
        f"""
        INSERT INTO message_parts(
            message_id,
            ordinal,
            kind,
            status,
            text_content,
            metadata_json
        )
        SELECT
            messages.id,
            0,
            CASE messages.type
                WHEN 'text' THEN 'text'
                WHEN 'link' THEN 'link'
                WHEN 'image' THEN 'image'
                WHEN 'file' THEN 'file'
                WHEN 'voice' THEN 'audio'
                WHEN 'video' THEN 'video'
                WHEN 'sticker' THEN 'sticker'
                WHEN 'attachment' THEN 'attachment'
                ELSE 'unsupported'
            END,
            CASE
                WHEN messages.type IN ('text', 'link') THEN 'resolved'
                WHEN messages.type = 'file' AND messages.content <> ''
                    THEN 'resolved'
                ELSE 'unresolved'
            END,
            CASE
                WHEN messages.type IN ('text', 'link', 'file')
                    THEN messages.content
                ELSE ''
            END,
            '{{}}'
        FROM messages
        WHERE {message_condition}
        ON CONFLICT(message_id, ordinal) DO UPDATE SET
            kind = excluded.kind,
            status = CASE
                WHEN message_parts.kind = excluded.kind
                 AND message_parts.status IN ('resolved', 'missing')
                 AND excluded.kind <> 'unsupported'
                 AND excluded.status = 'unresolved'
                    THEN message_parts.status
                ELSE excluded.status
            END,
            text_content = CASE
                WHEN message_parts.kind = excluded.kind
                 AND message_parts.status IN ('resolved', 'missing')
                 AND excluded.kind <> 'unsupported'
                 AND excluded.status = 'unresolved'
                    THEN message_parts.text_content
                ELSE excluded.text_content
            END
        """,
        message_parameters,
    )

    _classify_link_parts(
        connection,
        message_condition=message_condition,
        message_parameters=message_parameters,
    )

    connection.execute(
        f"""
        DELETE FROM assets
        WHERE part_id IN (
            SELECT message_parts.id
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            WHERE {message_condition}
              AND message_parts.kind IN ('text', 'link', 'unsupported')
        )
        """,
        message_parameters,
    )
    connection.execute(
        f"""
        INSERT INTO assets(part_id, ordinal, kind, status)
        SELECT message_parts.id, 0, message_parts.kind, 'unresolved'
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {message_condition}
          AND message_parts.kind IN (
              'image', 'file', 'audio', 'video', 'sticker', 'attachment'
          )
        ON CONFLICT(part_id, ordinal) DO UPDATE SET
            kind = excluded.kind
        """,
        message_parameters,
    )

    if not build_indexes:
        return ProjectionResult(
            conversations=_count(
                connection,
                "conversation_origins",
                "conversation_id",
                scope,
            ),
            message_parts=_count_scoped_parts(connection, "message_parts", scope),
            assets=_count_scoped_parts(connection, "assets", scope),
            retrieval_chunks=_count(
                connection,
                "retrieval_chunks",
                "conversation_id",
                scope,
            ),
        )

    connection.execute(
        f"""
        DELETE FROM retrieval_chunks
        WHERE conversation_id IN (
            SELECT conversations.id
            FROM conversations
            WHERE {conversation_condition}
              AND conversations.is_indexing_enabled = 0
        )
        """,
        conversation_parameters,
    )

    connection.execute(
        f"""
        DELETE FROM retrieval_chunks
        WHERE message_part_id IN (
            SELECT message_parts.id
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            WHERE {message_condition}
              AND (
                    message_parts.status <> 'resolved'
                    OR message_parts.text_content = ''
              )
        )
        """,
        message_parameters,
    )
    connection.execute(
        f"""
        INSERT INTO retrieval_chunks(
            message_part_id,
            message_id,
            conversation_id,
            kind,
            text_content,
            sender_name,
            content_hash,
            index_version,
            sent_at
        )
        SELECT
            message_parts.id,
            messages.id,
            messages.conversation_id,
            message_parts.kind,
            message_parts.text_content,
            messages.sender_name,
            '',
            ?,
            messages.sent_at
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {message_condition}
          AND EXISTS (
                SELECT 1 FROM conversations
                WHERE conversations.id = messages.conversation_id
                  AND conversations.is_indexing_enabled = 1
          )
          AND message_parts.status = 'resolved'
          AND message_parts.text_content <> ''
        ON CONFLICT(message_part_id) DO UPDATE SET
            message_id = excluded.message_id,
            conversation_id = excluded.conversation_id,
            kind = excluded.kind,
            text_content = excluded.text_content,
            sender_name = excluded.sender_name,
            content_hash = excluded.content_hash,
            index_version = excluded.index_version,
            sent_at = excluded.sent_at
        """,
        (RETRIEVAL_INDEX_VERSION, *message_parameters),
    )

    _ensure_hash_udf(connection)
    chunk_condition, chunk_parameters = _condition("conversation_id", scope)
    # Compute content_hash in SQL via UDF; avoids fetching every chunk into
    # Python and running 38k+ individual UPDATE statements (previously the
    # slowest part of a full-library projection).
    connection.execute(
        f"""
        UPDATE retrieval_chunks
        SET content_hash = sha256_kv(kind, text_content),
            index_version = ?
        WHERE {chunk_condition}
          AND (content_hash = '' OR index_version <> ?)
        """,
        (RETRIEVAL_INDEX_VERSION, *chunk_parameters, RETRIEVAL_INDEX_VERSION),
    )

    return ProjectionResult(
        conversations=_count(
            connection,
            "conversation_origins",
            "conversation_id",
            scope,
        ),
        message_parts=_count_scoped_parts(connection, "message_parts", scope),
        assets=_count_scoped_parts(connection, "assets", scope),
        retrieval_chunks=_count(
            connection,
            "retrieval_chunks",
            "conversation_id",
            scope,
        ),
    )


def _classify_link_parts(
    connection: sqlite3.Connection,
    *,
    message_condition: str,
    message_parameters: tuple[str, ...],
) -> None:
    rows = connection.execute(
        f"""
        SELECT
            message_parts.id,
            messages.type,
            messages.content,
            message_parts.metadata_json
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {message_condition}
          AND messages.type IN ('text', 'link')
        """,
        message_parameters,
    ).fetchall()

    updates: list[tuple[str, str, int]] = []
    for row in rows:
        message_type = str(row[1])
        content = str(row[2])
        url = _first_http_url(content)
        if message_type != "link" and url is None:
            continue

        metadata_json = str(row[3])
        if url is not None and _is_projection_metadata(metadata_json):
            host = urlsplit(url).hostname
            metadata_json = json.dumps(
                {
                    "domain": host.casefold() if host else "",
                    "projection": _LINK_METADATA_MARKER,
                    "url": url,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        updates.append((content, metadata_json, int(row[0])))

    connection.executemany(
        """
        UPDATE message_parts
        SET kind = 'link',
            status = 'resolved',
            text_content = ?,
            metadata_json = ?
        WHERE id = ?
        """,
        updates,
    )


def _first_http_url(content: str) -> str | None:
    match = _URL_PATTERN.search(content)
    if match is None:
        return None
    candidate = match.group(0).rstrip(".,;:!?)]}")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        # Malformed/unfinished bracket (e.g. "https://[baidu", bad IPv6 literal)
        # in message text. Skip this candidate instead of crashing the whole
        # import job (2026-09-13: one bad message aborted every projection run).
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    return candidate


def _is_projection_metadata(metadata_json: str) -> bool:
    try:
        metadata = json.loads(metadata_json)
    except (TypeError, ValueError):
        return False
    return metadata == {} or metadata.get("projection") == _LINK_METADATA_MARKER


def _normalize_scope(
    conversation_ids: Iterable[str] | None,
) -> tuple[str, ...] | None:
    if conversation_ids is None:
        return None
    return tuple(dict.fromkeys(str(item) for item in conversation_ids))


def _condition(
    column: str,
    scope: tuple[str, ...] | None,
) -> tuple[str, tuple[str, ...]]:
    if scope is None:
        return "1 = 1", ()

    if not scope:
        return "0 = 1", ()
    placeholders = ", ".join("?" for _item in scope)
    return f"{column} IN ({placeholders})", scope


def _content_hash(kind: str, text: str) -> str:
    payload = f"{kind}\x1f{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ensure_hash_udf(connection: sqlite3.Connection) -> None:
    """Register sha256_kv(kind, text) UDF if not already present.

    Lets projection compute content_hash in a single UPDATE instead of
    fetching every retrieval_chunk into Python and running executemany.
    """
    try:
        connection.create_function("sha256_kv", 2, _content_hash, deterministic=True)
    except sqlite3.OperationalError:
        # UDF already registered on this connection (create_function raises
        # when overwriting an existing function in some sqlite3 builds).
        pass


def _count(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    scope: tuple[str, ...] | None,
) -> int:
    condition, parameters = _condition(column, scope)
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {condition}",
            parameters,
        ).fetchone()[0]
    )


def _count_scoped_parts(
    connection: sqlite3.Connection,
    table: str,
    scope: tuple[str, ...] | None,
) -> int:
    condition, parameters = _condition("messages.conversation_id", scope)
    if table == "message_parts":
        join = "message_parts JOIN messages ON messages.id = message_parts.message_id"
    else:
        join = (
            "assets JOIN message_parts ON message_parts.id = assets.part_id "
            "JOIN messages ON messages.id = message_parts.message_id"
        )
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {join} WHERE {condition}",
            parameters,
        ).fetchone()[0]
    )
