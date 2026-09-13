from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .database import initialize_database
from .wechat_media import (
    decode_v2_image,
    discover_v2_image_keys,
    extract_video_media_key,
    extract_wxgf_content,
    extract_image_media_key,
    load_voice_data,
    locate_image_dat,
    locate_image_dat_by_context,
    locate_video_by_context,
)


SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MESSAGE_TABLE_PATTERN = re.compile(r"Msg_[0-9a-f]{32}")
REQUIRED_MESSAGE_COLUMNS = {
    "local_id",
    "server_id",
    "local_type",
    "sort_seq",
    "real_sender_id",
    "create_time",
    "status",
    "source",
    "message_content",
    "compress_content",
    "packed_info_data",
}
_MIME_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+_-]*/[A-Za-z0-9][A-Za-z0-9.+_-]*$"
)


class SourceSchemaError(RuntimeError):
    pass


class DatabaseKind(str, Enum):
    SESSION = "session"
    CONTACT = "contact"
    MESSAGE = "message"
    RESOURCE = "resource"
    MEDIA = "media"
    HARDLINK = "hardlink"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SourceDatabase:
    path: Path
    kind: DatabaseKind
    shard_id: str | None = None


@dataclass(frozen=True)
class ImportResult:
    discovered_sources: int
    normalized_conversations: int
    inserted_messages: int


@dataclass(frozen=True)
class _Contact:
    user_name: str
    display_name: str


@dataclass(frozen=True)
class _Session:
    user_name: str
    title: str
    last_message_at: int | None


@dataclass(frozen=True)
class _MediaSource:
    file_name: str
    mime_type: str | None
    path: Path | None = None
    data: bytes | None = None
    metadata_json: str = "{}"
    require_browser_image: bool = False


@dataclass(frozen=True)
class _Message:
    conversation_id: str
    source_message_id: str
    dedupe_key: str
    sender_id: str
    sender_name: str
    sent_at: int
    message_type: str
    content: str
    media_source: _MediaSource | None = None


def connect_read_only(path: str | Path) -> sqlite3.Connection:
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    uri = f"{source_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def discover_sources(
    source: str | Path | Iterable[str | Path],
) -> list[SourceDatabase]:
    discovered: list[SourceDatabase] = []
    for path in _candidate_paths(source):
        kind = detect_database_kind(path)
        if kind is None:
            continue
        discovered.append(
            SourceDatabase(
                path=path,
                kind=kind,
                shard_id=path.stem if kind is DatabaseKind.MESSAGE else None,
            )
        )
    return discovered


def detect_database_kind(path: str | Path) -> DatabaseKind | None:
    with closing(connect_read_only(path)) as connection:
        tables = _table_names(connection)

    if "SessionTable" in tables:
        return DatabaseKind.SESSION
    if {"contact", "chat_room"} <= tables:
        return DatabaseKind.CONTACT
    if "Name2Id" in tables and any(
        MESSAGE_TABLE_PATTERN.fullmatch(table_name)
        for table_name in tables
    ):
        return DatabaseKind.MESSAGE
    if {"MessageResourceInfo", "MessageResourceDetail"} <= tables:
        return DatabaseKind.RESOURCE
    if "VoiceInfo" in tables:
        return DatabaseKind.MEDIA
    if "dir2id" in tables and "image_hardlink_info_v4" in tables:
        return DatabaseKind.HARDLINK
    return None


def make_dedupe_key(
    shard_id: str,
    table_name: str,
    *,
    server_id: object,
    local_id: object,
    sort_seq: object,
) -> str:
    normalized_server_id = _normalize_identifier(server_id)
    if normalized_server_id not in {"", "0"}:
        return f"{shard_id}:{table_name}:server:{normalized_server_id}"

    normalized_local_id = _normalize_identifier(local_id)
    normalized_sort_seq = _normalize_identifier(sort_seq)
    if not normalized_local_id or not normalized_sort_seq:
        raise SourceSchemaError(
            "A message without server_id requires local_id and sort_seq"
        )
    return (
        f"{shard_id}:{table_name}:local:"
        f"{normalized_local_id}:{normalized_sort_seq}"
    )


def import_sources(
    source: str | Path | Iterable[str | Path],
    application_database: str | Path,
    *,
    media_root: str | Path | None = None,
    knowledge_projector: Callable[..., object] | None = None,
    graph_projector: Callable[..., object] | None = None,
    activity_filter_enabled: bool = False,
    activity_filter_inactive_days: int = 30,
) -> ImportResult:
    sources = discover_sources(source)
    contacts = _load_contacts(sources)
    sessions = _load_sessions(sources)
    connection = initialize_database(application_database)
    normalized_conversation_ids: set[str] = set()
    inserted_messages = 0
    media_sources: dict[str, _MediaSource] = {}
    resolved_media_root = (
        Path(media_root).resolve() if media_root is not None else None
    )
    image_keys = (
        discover_v2_image_keys(resolved_media_root)
        if resolved_media_root is not None
        else ()
    )
    hardlink_database = next(
        (
            item.path
            for item in sources
            if item.kind is DatabaseKind.HARDLINK
        ),
        None,
    )
    media_databases = tuple(
        item.path for item in sources if item.kind is DatabaseKind.MEDIA
    )

    try:
        for session in sessions.values():
            contact = contacts.get(session.user_name)
            title = (
                contact.display_name
                if contact is not None
                else session.title
            )
            _upsert_conversation(
                connection,
                user_name=session.user_name,
                title=title,
                kind=_conversation_kind(session.user_name),
                last_message_at=session.last_message_at,
            )
            normalized_conversation_ids.add(session.user_name)
        # 阶段1：会话 upsert 作为短事务先落盘，避免与后续全量消息迭代共享长事务。
        connection.commit()

        # 阶段2：消息 INSERT 按批提交（每 2000 条），批次之间释放写锁，
        # 让 gateway 的 BEGIN IMMEDIATE 与 HTTP 写请求有窗口抢锁，避免分钟级持锁。
        import_batch_size = 2000
        pending_writes = 0
        for message_source in (
            item for item in sources if item.kind is DatabaseKind.MESSAGE
        ):
            for table_name, user_name, message in _iter_shard_messages(
                message_source,
                contacts,
                media_root=resolved_media_root,
                hardlink_database=hardlink_database,
                media_databases=media_databases,
            ):
                if user_name not in normalized_conversation_ids:
                    session = sessions.get(user_name)
                    contact = contacts.get(user_name)
                    title = (
                        contact.display_name
                        if contact is not None
                        else session.title
                        if session is not None
                        else user_name
                    )
                    _upsert_conversation(
                        connection,
                        user_name=user_name,
                        title=title,
                        kind=_conversation_kind(user_name),
                        last_message_at=(
                            session.last_message_at
                            if session is not None
                            else None
                        ),
                    )
                    normalized_conversation_ids.add(user_name)
                cursor = connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id,
                        source_message_id,
                        dedupe_key,
                        sender_id,
                        sender_name,
                        sent_at,
                        type,
                        content,
                        raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(dedupe_key) DO NOTHING
                    """,
                    (
                        message.conversation_id,
                        message.source_message_id,
                        message.dedupe_key,
                        message.sender_id,
                        message.sender_name,
                        message.sent_at,
                        message.message_type,
                        message.content,
                    ),
                )
                inserted = max(cursor.rowcount, 0)
                inserted_messages += inserted
                if inserted == 0:
                    connection.execute(
                        """
                        UPDATE messages
                        SET
                            conversation_id = ?,
                            source_message_id = ?,
                            sender_id = ?,
                            sender_name = ?,
                            sent_at = ?,
                            type = ?,
                            content = ?
                        WHERE dedupe_key = ?
                          AND (
                                conversation_id IS NOT ?
                             OR source_message_id IS NOT ?
                             OR sender_id IS NOT ?
                             OR sender_name IS NOT ?
                             OR sent_at IS NOT ?
                             OR type IS NOT ?
                             OR content IS NOT ?
                          )
                        """,
                        (
                            message.conversation_id,
                            message.source_message_id,
                            message.sender_id,
                            message.sender_name,
                            message.sent_at,
                            message.message_type,
                            message.content,
                            message.dedupe_key,
                            message.conversation_id,
                            message.source_message_id,
                            message.sender_id,
                            message.sender_name,
                            message.sent_at,
                            message.message_type,
                            message.content,
                        ),
                    )
                if message.media_source is not None:
                    media_sources[message.dedupe_key] = message.media_source
                pending_writes += 1
                if pending_writes >= import_batch_size:
                    connection.commit()
                    pending_writes = 0

        connection.commit()

        for conversation_id in normalized_conversation_ids:
            connection.execute(
                """
                UPDATE conversations
                SET
                    message_count = (
                        SELECT COUNT(*)
                        FROM messages
                        WHERE conversation_id = conversations.id
                    ),
                    last_message_at = COALESCE(
                        (
                            SELECT MAX(sent_at)
                            FROM messages
                            WHERE conversation_id = conversations.id
                        ),
                        last_message_at
                    )
                WHERE id = ?
                """,
                (conversation_id,),
            )
        connection.commit()

        # 阶段3.5：活跃度过滤——用户最近 N 天未发言的会话标记为不索引，
        # 避免营销群/潜水群占用检索和投影资源。
        if activity_filter_enabled and normalized_conversation_ids:
            from .services.activity_filter import evaluate_activity

            evaluate_activity(
                connection,
                inactive_days=activity_filter_inactive_days,
                conversation_ids=tuple(sorted(normalized_conversation_ids)),
            )

        from .services.knowledge_projection_service import project_knowledge

        fact_conversation_ids = tuple(sorted(normalized_conversation_ids))
        project_knowledge(
            connection,
            conversation_ids=fact_conversation_ids,
            build_indexes=False,
        )
        # project_knowledge 不自行 commit（事务所有权归调用方），显式落盘后再物化媒体。
        connection.commit()
        # 阶段5：媒体物化；慢操作（读文件/V2 解码/ffmpeg 转码）在每个 asset 的
        # 写库提交之间运行，不再跨慢计算持有写连接。
        _materialize_media_assets(
            connection,
            media_sources,
            asset_root=Path(application_database).parent / "assets",
            image_keys=image_keys,
        )
        connection.commit()

        _run_projection_phase(
            connection,
            conversation_ids=tuple(sorted(normalized_conversation_ids)),
            knowledge_projector=knowledge_projector,
            graph_projector=graph_projector,
        )
    finally:
        connection.close()

    return ImportResult(
        discovered_sources=len(sources),
        normalized_conversations=len(normalized_conversation_ids),
        inserted_messages=inserted_messages,
    )




def _run_projection_phase(
    connection: sqlite3.Connection,
    *,
    conversation_ids: tuple[str, ...],
    knowledge_projector: Callable[..., object] | None,
    graph_projector: Callable[..., object] | None,
) -> bool:
    from .services.graph_service import project_graph
    from .services.knowledge_projection_service import project_knowledge

    connection.execute(
        """
        UPDATE source_accounts
        SET projection_status = 'indexing'
        WHERE id = 'wechat:default'
        """
    )
    connection.commit()

    active_knowledge_projector = knowledge_projector or project_knowledge
    active_graph_projector = graph_projector or project_graph
    try:
        active_knowledge_projector(
            connection,
            conversation_ids=conversation_ids,
        )
        active_graph_projector(
            connection,
            conversation_ids=conversation_ids,
        )
    except Exception:
        connection.rollback()
        connection.execute(
            """
            UPDATE source_accounts
            SET projection_status = 'degraded'
            WHERE id = 'wechat:default'
            """
        )
        connection.commit()
        return False

    connection.execute(
        """
        UPDATE source_accounts
        SET projection_status = 'ready'
        WHERE id = 'wechat:default'
        """
    )
    connection.commit()
    return True
def _candidate_paths(
    source: str | Path | Iterable[str | Path],
) -> list[Path]:
    if isinstance(source, (str, Path)):
        requested = [Path(source)]
    else:
        requested = [Path(item) for item in source]

    candidates: dict[Path, Path] = {}
    for requested_path in requested:
        if requested_path.is_dir():
            paths = (
                child
                for child in requested_path.iterdir()
                if child.is_file() and child.suffix.lower() in SQLITE_SUFFIXES
            )
        else:
            paths = (requested_path,)

        for path in paths:
            if path.is_file():
                candidates[path.resolve()] = path.resolve()
    return sorted(candidates.values(), key=lambda path: str(path).casefold())


def _load_contacts(sources: Iterable[SourceDatabase]) -> dict[str, _Contact]:
    contacts: dict[str, _Contact] = {}
    for source in sources:
        if source.kind is not DatabaseKind.CONTACT:
            continue
        with closing(connect_read_only(source.path)) as connection:
            columns = _table_columns(connection, "contact")
            user_column = _required_column(
                columns,
                "user_name",
                "username",
                "str_user_name",
            )
            nickname_column = _optional_column(
                columns,
                "nick_name",
                "nickname",
                "display_name",
            )
            remark_column = _optional_column(columns, "remark", "con_remark")
            for row in connection.execute(
                f"SELECT * FROM {_quote_identifier('contact')}"
            ):
                user_name = _as_text(row[user_column])
                if not user_name:
                    continue
                display_name = (
                    _as_text(row[remark_column])
                    if remark_column is not None
                    else ""
                )
                if not display_name and nickname_column is not None:
                    display_name = _as_text(row[nickname_column])
                contacts[user_name] = _Contact(
                    user_name=user_name,
                    display_name=display_name or user_name,
                )
    return contacts


def _load_sessions(sources: Iterable[SourceDatabase]) -> dict[str, _Session]:
    sessions: dict[str, _Session] = {}
    for source in sources:
        if source.kind is not DatabaseKind.SESSION:
            continue
        with closing(connect_read_only(source.path)) as connection:
            columns = _table_columns(connection, "SessionTable")
            user_column = _required_column(
                columns,
                "user_name",
                "username",
                "str_user_name",
            )
            title_column = _optional_column(
                columns,
                "nick_name",
                "nickname",
                "display_name",
                "remark",
            )
            timestamp_column = _optional_column(
                columns,
                "last_timestamp",
                "sort_timestamp",
                "last_message_at",
                "timestamp",
            )
            for row in connection.execute(
                f"SELECT * FROM {_quote_identifier('SessionTable')}"
            ):
                user_name = _as_text(row[user_column])
                if not user_name:
                    continue
                title = (
                    _as_text(row[title_column])
                    if title_column is not None
                    else user_name
                )
                timestamp = (
                    _timestamp_ms(row[timestamp_column])
                    if timestamp_column is not None
                    and row[timestamp_column] is not None
                    else None
                )
                sessions[user_name] = _Session(
                    user_name=user_name,
                    title=title or user_name,
                    last_message_at=timestamp,
                )
    return sessions


def _iter_shard_messages(
    source: SourceDatabase,
    contacts: dict[str, _Contact],
    *,
    media_root: Path | None = None,
    hardlink_database: Path | None = None,
    media_databases: tuple[Path, ...] = (),
) -> Iterator[tuple[str, str, _Message]]:
    if source.shard_id is None:
        raise SourceSchemaError(f"Message shard has no id: {source.path}")

    with closing(connect_read_only(source.path)) as connection:
        table_names = _table_names(connection)
        id_to_user_name, user_names = _read_name_mapping(connection)

        for user_name in user_names:
            table_name = _message_table_name(user_name)
            if table_name not in table_names:
                continue
            columns = set(_table_columns(connection, table_name))
            missing_columns = REQUIRED_MESSAGE_COLUMNS - columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise SourceSchemaError(
                    f"{source.path.name}:{table_name} is missing {missing}"
                )

            has_wcdb_ct = "WCDB_CT_message_content" in columns
            wcdb_select = (
                "WCDB_CT_message_content"
                if has_wcdb_ct
                else "NULL AS WCDB_CT_message_content"
            )
            query = (
                "SELECT "
                "local_id, server_id, local_type, sort_seq, "
                "real_sender_id, create_time, message_content, packed_info_data, "
                f"{wcdb_select} "
                f"FROM {_quote_identifier(table_name)}"
            )
            for row in connection.execute(query):
                sender_id, sender_name = _resolve_sender(
                    source.shard_id,
                    row["real_sender_id"],
                    id_to_user_name,
                    contacts,
                )
                server_id = row["server_id"]
                local_id = row["local_id"]
                sort_seq = row["sort_seq"]
                dedupe_key = make_dedupe_key(
                    source.shard_id,
                    table_name,
                    server_id=server_id,
                    local_id=local_id,
                    sort_seq=sort_seq,
                )
                normalized_server_id = _normalize_identifier(server_id)
                source_message_id = (
                    normalized_server_id
                    if normalized_server_id not in {"", "0"}
                    else (
                        f"{_normalize_identifier(local_id)}:"
                        f"{_normalize_identifier(sort_seq)}"
                    )
                )
                message_type, content = _normalize_message_content(
                    row["local_type"],
                    row["message_content"],
                    wcdb_ct=row["WCDB_CT_message_content"],
                )
                media_source = _media_source_from_packed_info(
                    source.path.parent,
                    row["packed_info_data"],
                    message_type=message_type,
                )
                if media_source is None:
                    media_source = _resolve_wechat_media_source(
                        message_type=message_type,
                        packed_info=row["packed_info_data"],
                        server_id=server_id,
                        conversation_hash=table_name.removeprefix("Msg_"),
                        sent_at=row["create_time"],
                        media_root=media_root,
                        hardlink_database=hardlink_database,
                        media_databases=media_databases,
                    )
                yield (
                    table_name,
                    user_name,
                    _Message(
                        conversation_id=user_name,
                        source_message_id=source_message_id,
                        dedupe_key=dedupe_key,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        sent_at=_timestamp_ms(row["create_time"]),
                        message_type=message_type,
                        content=content,
                        media_source=media_source,
                    ),
                )


def _read_name_mapping(
    connection: sqlite3.Connection,
) -> tuple[dict[str, str], list[str]]:
    columns = _table_columns(connection, "Name2Id")
    id_column = _optional_column(columns, "id", "user_id", "row_id")
    user_column = _required_column(
        columns,
        "user_name",
        "username",
        "str_user_name",
    )
    if id_column is None:
        id_column = "__wechat_rowid__"
        select_name_mapping = (
            "SELECT rowid AS "
            f"{_quote_identifier(id_column)}, * "
            f"FROM {_quote_identifier('Name2Id')}"
        )
    else:
        select_name_mapping = (
            f"SELECT * FROM {_quote_identifier('Name2Id')}"
        )
    id_to_user_name: dict[str, str] = {}
    user_names: list[str] = []
    for row in connection.execute(select_name_mapping):
        user_name = _as_text(row[user_column])
        if not user_name:
            continue
        id_to_user_name[_normalize_identifier(row[id_column])] = user_name
        user_names.append(user_name)
    return id_to_user_name, user_names


def _resolve_sender(
    shard_id: str,
    raw_sender_id: object,
    id_to_user_name: dict[str, str],
    contacts: dict[str, _Contact],
) -> tuple[str, str]:
    normalized_sender_id = _normalize_identifier(raw_sender_id)
    user_name = id_to_user_name.get(normalized_sender_id)
    if user_name is not None:
        contact = contacts.get(user_name)
        return (
            user_name,
            contact.display_name if contact is not None else user_name,
        )

    unresolved = f"{shard_id}:sender:{normalized_sender_id or 'unknown'}"
    return unresolved, unresolved




def _media_source_from_packed_info(
    source_root: Path,
    raw_value: object,
    *,
    message_type: str,
) -> _MediaSource | None:
    if message_type not in {
        "image", "file", "audio", "video", "sticker", "attachment"
    }:
        return None
    if isinstance(raw_value, bytes):
        try:
            raw_text = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw_value, str):
        raw_text = raw_value
    else:
        return None
    try:
        metadata = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None
    raw_path = next(
        (
            metadata.get(key)
            for key in ("local_path", "path", "file_path", "media_path")
            if isinstance(metadata.get(key), str) and metadata.get(key)
        ),
        None,
    )
    if not isinstance(raw_path, str):
        return None

    root = source_root.resolve()
    requested = Path(raw_path)
    candidate = (requested if requested.is_absolute() else root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None

    raw_name = metadata.get("file_name")
    file_name = (
        Path(raw_name).name
        if isinstance(raw_name, str) and raw_name
        else candidate.name
    )
    mime_type = _safe_mime_type(metadata.get("mime_type"), file_name)
    return _MediaSource(
        file_name=file_name,
        mime_type=mime_type,
        path=candidate,
    )


def _resolve_wechat_media_source(
    *,
    message_type: str,
    packed_info: object,
    server_id: object,
    conversation_hash: str,
    sent_at: object,
    media_root: Path | None,
    hardlink_database: Path | None,
    media_databases: tuple[Path, ...],
) -> _MediaSource | None:
    if message_type == "image" and media_root is not None:
        media_key = extract_image_media_key(packed_info)
        if media_key is None:
            return None
        candidate = (
            locate_image_dat(
                hardlink_database,
                media_root,
                media_key=media_key,
            )
            if hardlink_database is not None
            else None
        )
        if candidate is None:
            candidate = locate_image_dat_by_context(
                media_root,
                conversation_hash=conversation_hash,
                sent_at=sent_at,
                media_key=media_key,
            )
        if candidate is None:
            return None
        return _MediaSource(
            file_name=f"{media_key}.dat",
            mime_type=None,
            path=candidate,
            require_browser_image=True,
        )

    if message_type == "voice":
        for media_database in media_databases:
            voice_data = load_voice_data(
                media_database,
                server_id=server_id,
            )
            if voice_data is None:
                continue
            normalized_server_id = _normalize_identifier(server_id) or "voice"
            return _MediaSource(
                file_name=f"{normalized_server_id}.silk",
                mime_type="audio/silk",
                data=voice_data,
                metadata_json=json.dumps(
                    {"codec": "silk", "playable": False},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

    if message_type == "video" and media_root is not None:
        media_key = extract_video_media_key(packed_info)
        if media_key is None:
            return None
        candidate = locate_video_by_context(
            media_root,
            conversation_hash=conversation_hash,
            sent_at=sent_at,
            media_key=media_key,
        )
        if candidate is None:
            return None
        return _MediaSource(
            file_name=f"{media_key}.mp4",
            mime_type="video/mp4",
            path=candidate,
        )
    return None


def _materialize_media_assets(
    connection: sqlite3.Connection,
    media_sources: dict[str, _MediaSource],
    *,
    asset_root: Path,
    image_keys: tuple[tuple[bytes, int], ...] = (),
) -> None:
    if not media_sources:
        return
    asset_root.mkdir(parents=True, exist_ok=True)
    for dedupe_key, media_source in sorted(media_sources.items()):
        payload: bytes | None = None
        mime_type = media_source.mime_type
        previewable = True
        metadata_json = media_source.metadata_json
        file_name = media_source.file_name
        if media_source.data is not None:
            payload = media_source.data
        elif media_source.path is not None and media_source.path.is_file():
            if media_source.require_browser_image:
                payload = media_source.path.read_bytes()
                browser_image = _browser_image_payload(payload)
                if browser_image is not None:
                    payload, mime_type = browser_image
                else:
                    if not payload.startswith(b"\x07\x08V2\x08\x07"):
                        continue
                    encrypted_payload = payload
                    for aes_key, xor_key in image_keys:
                        decoded = decode_v2_image(
                            encrypted_payload,
                            aes_key=aes_key,
                            xor_key=xor_key,
                        )
                        decoded_image = (
                            _browser_image_payload(decoded)
                            if decoded is not None
                            else None
                        )
                        if decoded_image is None:
                            continue
                        payload, mime_type = decoded_image
                        file_name = (
                            f"{Path(media_source.file_name).stem}"
                            f"{_image_extension(mime_type)}"
                        )
                        metadata_json = json.dumps(
                            {
                                "sourceEncoding": "wechat-v2",
                                "previewable": True,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        break
                    else:
                        previewable = False
                        mime_type = "application/x-wechat-image-v2"
                        metadata_json = json.dumps(
                            {
                                "encryption": "wechat-v2",
                                "previewable": False,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
            else:
                payload = media_source.path.read_bytes()
        if payload is None:
            continue
        digest = hashlib.sha256(payload).hexdigest()
        destination = asset_root / digest
        _write_content_addressed(payload, destination, digest)

        row = connection.execute(
            """
            SELECT assets.id AS asset_id, message_parts.id AS part_id
            FROM messages
            JOIN message_parts
              ON message_parts.message_id = messages.id
             AND message_parts.ordinal = 0
            JOIN assets
              ON assets.part_id = message_parts.id
             AND assets.ordinal = 0
            WHERE messages.dedupe_key = ?
            """,
            (dedupe_key,),
        ).fetchone()
        if row is None:
            continue
        if previewable:
            connection.execute(
                """
                UPDATE message_parts
                SET status = 'resolved'
                WHERE id = ?
                """,
                (row["part_id"],),
            )
            connection.execute(
                """
                UPDATE assets
                SET status = 'resolved',
                    file_name = ?,
                    mime_type = ?,
                    byte_size = ?,
                    external_url = NULL,
                    local_path = ?,
                    sha256 = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    file_name,
                    mime_type,
                    destination.stat().st_size,
                    digest,
                    digest,
                    metadata_json,
                    row["asset_id"],
                ),
            )
        else:
            connection.execute(
                """
                UPDATE assets
                SET file_name = ?,
                    mime_type = ?,
                    byte_size = ?,
                    external_url = NULL,
                    local_path = ?,
                    sha256 = ?,
                    metadata_json = ?
                WHERE id = ?
                  AND status <> 'resolved'
                """,
                (
                    file_name,
                    mime_type,
                    destination.stat().st_size,
                    digest,
                    digest,
                    metadata_json,
                    row["asset_id"],
                ),
            )
        # 每个 asset 物化写库后立即提交，释放写锁；下一轮的文件读/V2 解码/
        # ffmpeg 转码慢操作不再持有写连接，避免阻塞 gateway 抢锁。
        connection.commit()


def _safe_mime_type(raw_value: object, file_name: str) -> str | None:
    if isinstance(raw_value, str) and _MIME_TYPE_PATTERN.fullmatch(raw_value):
        return raw_value.casefold()
    guessed = mimetypes.guess_type(file_name)[0]
    if guessed is None or _MIME_TYPE_PATTERN.fullmatch(guessed) is None:
        return None
    return guessed.casefold()


def _copy_content_addressed(
    source: Path,
    destination: Path,
    expected_digest: str,
) -> None:
    if destination.is_file() and _sha256_path(destination) == expected_digest:
        return
    temporary = destination.with_name(
        f".{expected_digest}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copyfile(source, temporary)
        if _sha256_path(temporary) != expected_digest:
            raise OSError("Copied asset failed content hash verification")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_content_addressed(
    payload: bytes,
    destination: Path,
    expected_digest: str,
) -> None:
    if destination.is_file() and _sha256_path(destination) == expected_digest:
        return
    temporary = destination.with_name(
        f".{expected_digest}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(payload)
        if _sha256_path(temporary) != expected_digest:
            raise OSError("Written asset failed content hash verification")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _browser_image_mime(payload: bytes) -> str | None:
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if (
        len(payload) >= 12
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    ):
        return "image/webp"
    if payload.startswith(b"BM"):
        return "image/bmp"
    return None


def _browser_image_payload(payload: bytes) -> tuple[bytes, str] | None:
    mime_type = _browser_image_mime(payload)
    if mime_type is not None:
        return payload, mime_type
    wxgf_content = extract_wxgf_content(payload)
    if wxgf_content is None:
        return None
    content_type, content = wxgf_content
    if content_type.startswith("image/"):
        return content, content_type
    png = _transcode_hevc_to_png(content)
    return (png, "image/png") if png is not None else None


def _transcode_hevc_to_png(payload: bytes) -> bytes | None:
    if not payload or len(payload) > 128 * 1024 * 1024:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    creation_flags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "hevc",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-fs",
                str(64 * 1024 * 1024),
                "pipe:1",
            ],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 64 * 1024 * 1024:
        return None
    return result.stdout if result.stdout.startswith(b"\x89PNG\r\n\x1a\n") else None


def _image_extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime_type, ".img")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_message_content(
    raw_type: object,
    raw_content: object,
    *,
    wcdb_ct: object = None,
) -> tuple[str, str]:
    try:
        local_type = int(raw_type)
    except (TypeError, ValueError):
        local_type = -1
    if local_type > 0xFFFFFFFF:
        local_type &= 0xFFFFFFFF

    # WeChat 4.0: WCDB_CT_message_content=4 means message_content is a
    # zstd-compressed BLOB. Decompress before treating as text.
    if local_type == 1 and wcdb_ct == 4 and isinstance(raw_content, bytes):
        try:
            import zstandard

            raw_content = zstandard.ZstdDecompressor().decompress(raw_content)
        except Exception:
            return "unsupported_1", "[文本内容不可读]"

    if local_type == 1:
        if isinstance(raw_content, bytes):
            try:
                content = raw_content.decode("utf-8").strip()
            except UnicodeDecodeError:
                return "unsupported_1", "[文本内容不可读]"
        else:
            content = _as_text(raw_content)
        if "\ufffd" in content or any(
            ord(character) < 32
            and character not in {"\t", "\n", "\r"}
            for character in content
        ):
            return "unsupported_1", "[文本内容不可读]"
        return "text", content

    typed_placeholders = {
        3: ("image", "[图片]"),
        34: ("voice", "[语音]"),
        43: ("video", "[视频]"),
        47: ("sticker", "[表情]"),
        49: ("attachment", "[附件]"),
    }
    return typed_placeholders.get(
        local_type,
        (f"unsupported_{local_type}", f"[不支持的消息类型: {local_type}]"),
    )


def _upsert_conversation(
    connection: sqlite3.Connection,
    *,
    user_name: str,
    title: str,
    kind: str,
    last_message_at: int | None,
) -> None:
    connection.execute(
        """
        INSERT INTO conversations (
            id,
            source_conversation_id,
            title,
            kind,
            last_message_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_conversation_id = excluded.source_conversation_id,
            title = excluded.title,
            kind = excluded.kind,
            last_message_at = COALESCE(
                excluded.last_message_at,
                conversations.last_message_at
            )
        WHERE conversations.source_conversation_id
                  IS NOT excluded.source_conversation_id
           OR conversations.title IS NOT excluded.title
           OR conversations.kind IS NOT excluded.kind
           OR COALESCE(
                  excluded.last_message_at,
                  conversations.last_message_at
              ) IS NOT conversations.last_message_at
        """,
        (user_name, user_name, title or user_name, kind, last_message_at),
    )


def _conversation_kind(user_name: str) -> str:
    if user_name.endswith("@chatroom"):
        return "group"
    if user_name.startswith("gh_") or user_name in {"filehelper", "newsapp"}:
        return "system"
    return "direct" if user_name else "unknown"


def _message_table_name(user_name: str) -> str:
    suffix = hashlib.md5(user_name.encode("utf-8")).hexdigest()
    return f"Msg_{suffix}"


def _timestamp_ms(value: object) -> int:
    timestamp = int(value)
    if abs(timestamp) < 100_000_000_000:
        return timestamp * 1000
    return timestamp


def _normalize_identifier(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    try:
        return str(int(text))
    except ValueError:
        return text


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[str]:
    return [
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({_quote_identifier(table_name)})"
        )
    ]


def _required_column(columns: Iterable[str], *candidates: str) -> str:
    selected = _optional_column(columns, *candidates)
    if selected is None:
        raise SourceSchemaError(
            f"Expected one of these columns: {', '.join(candidates)}"
        )
    return selected


def _optional_column(columns: Iterable[str], *candidates: str) -> str | None:
    by_normalized_name = {
        _normalize_column_name(column): column
        for column in columns
    }
    for candidate in candidates:
        match = by_normalized_name.get(_normalize_column_name(candidate))
        if match is not None:
            return match
    return None


def _normalize_column_name(column_name: str) -> str:
    return column_name.casefold().replace("_", "")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
