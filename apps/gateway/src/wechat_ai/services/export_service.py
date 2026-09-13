from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from ..config import Settings


ExportFormat = Literal["markdown", "json"]
ExportErrorCode = Literal["CONVERSATION_NOT_FOUND", "EXPORT_FAILED"]

_ERROR_MESSAGES: dict[ExportErrorCode, str] = {
    "CONVERSATION_NOT_FOUND": "未找到指定会话。",
    "EXPORT_FAILED": "导出失败，未生成文件。",
}
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


@dataclass(frozen=True)
class ExportResult:
    format: ExportFormat
    file_name: str
    message_count: int


@dataclass(frozen=True)
class _Conversation:
    id: str
    title: str
    kind: str
    last_message_at: int | None


@dataclass(frozen=True)
class _Message:
    id: int
    sender_id: str | None
    sender_name: str
    sent_at: int
    message_type: str
    content: str


class ExportServiceError(RuntimeError):
    def __init__(self, code: ExportErrorCode) -> None:
        self.code = code
        self.message = _ERROR_MESSAGES[code]
        self.retryable = False
        self.details: dict[str, object] = {}
        super().__init__(self.message)


class ExportService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def export_conversation(
        self,
        conversation_id: str,
        export_format: ExportFormat,
    ) -> ExportResult:
        if export_format not in {"markdown", "json"}:
            raise ValueError("Unsupported export format")

        try:
            conversation, messages = _read_conversation(
                self._settings.library_db,
                conversation_id,
            )
        except ExportServiceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ExportServiceError("EXPORT_FAILED") from exc

        file_name = _file_name(
            conversation.title,
            conversation.id,
            export_format,
        )
        content = (
            _render_markdown(conversation, messages)
            if export_format == "markdown"
            else _render_json(conversation, messages)
        )

        try:
            output_directory = _output_directory(
                self._settings.output_dir,
                export_format,
            )
            _atomic_write(output_directory / file_name, content)
        except OSError as exc:
            raise ExportServiceError("EXPORT_FAILED") from exc

        return ExportResult(
            format=export_format,
            file_name=file_name,
            message_count=len(messages),
        )


def _read_conversation(
    library_db: Path,
    conversation_id: str,
) -> tuple[_Conversation, list[_Message]]:
    if not library_db.is_file():
        raise ExportServiceError("EXPORT_FAILED")

    uri = f"{library_db.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT id, title, kind, last_message_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise ExportServiceError("CONVERSATION_NOT_FOUND")

        message_rows = connection.execute(
            """
            SELECT id, sender_id, sender_name, sent_at, type, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY sent_at ASC, id ASC
            """,
            (conversation_id,),
        ).fetchall()

    conversation = _Conversation(
        id=str(row["id"]),
        title=str(row["title"]),
        kind=str(row["kind"]),
        last_message_at=(
            int(row["last_message_at"])
            if row["last_message_at"] is not None
            else None
        ),
    )
    messages = [
        _Message(
            id=int(message["id"]),
            sender_id=(
                str(message["sender_id"])
                if message["sender_id"] is not None
                else None
            ),
            sender_name=str(message["sender_name"]),
            sent_at=int(message["sent_at"]),
            message_type=str(message["type"]),
            content=str(message["content"]),
        )
        for message in message_rows
    ]
    return conversation, messages


def _file_name(
    title: str,
    conversation_id: str,
    export_format: ExportFormat,
) -> str:
    normalized_title = unicodedata.normalize("NFKC", title)
    safe_title = "".join(
        character if character.isalnum() or character in {" ", "-", "_"} else "_"
        for character in normalized_title
    )
    safe_title = re.sub(r"[\s_]+", "_", safe_title).strip(" ._-")
    safe_title = safe_title[:60].rstrip(" ._-") or "conversation"
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:12]
    extension = ".md" if export_format == "markdown" else ".json"
    return f"{safe_title}-{digest}{extension}"


def _output_directory(
    configured_output: Path,
    export_format: ExportFormat,
) -> Path:
    output_root = configured_output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    subdirectory_name = "markdown" if export_format == "markdown" else "json"
    requested_directory = output_root / subdirectory_name
    requested_directory.mkdir(parents=True, exist_ok=True)
    output_directory = requested_directory.resolve()
    if not output_directory.is_relative_to(output_root):
        raise OSError("Export directory escapes the configured output root")
    return output_directory


def _atomic_write(target: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        stream = os.fdopen(
            descriptor,
            mode="w",
            encoding="utf-8",
            newline="\n",
        )
        descriptor = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _render_markdown(
    conversation: _Conversation,
    messages: list[_Message],
) -> str:
    lines = [
        f"# {conversation.title}",
        "",
        f"- 会话类型：{conversation.kind}",
        f"- 消息数：{len(messages)}",
        "",
        "## 消息",
    ]
    for message in messages:
        sender = message.sender_name or message.sender_id or "未知发送者"
        lines.extend(
            [
                "",
                f"### {_format_timestamp(message.sent_at)} · {sender}",
                "",
                message.content,
            ]
        )
    return "\n".join(lines) + "\n"


def _render_json(
    conversation: _Conversation,
    messages: list[_Message],
) -> str:
    payload = {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "kind": conversation.kind,
            "lastMessageAt": conversation.last_message_at,
            "messageCount": len(messages),
        },
        "messages": [
            {
                "id": message.id,
                "senderId": message.sender_id,
                "senderName": message.sender_name,
                "sentAt": message.sent_at,
                "type": message.message_type,
                "content": message.content,
            }
            for message in messages
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _format_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=_SHANGHAI_TIMEZONE,
    ).isoformat(timespec="seconds")
