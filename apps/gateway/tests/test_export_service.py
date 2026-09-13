from __future__ import annotations

import importlib
import json
import os
from contextlib import closing
from pathlib import Path

import pytest

from wechat_ai.config import Settings
from wechat_ai.database import initialize_database


CONVERSATION_ID = "../../private-conversation"
CONVERSATION_TITLE = "Quarterly: Plan / 2026*"
RAW_SECRET = "raw-payload-secret"
DEDUPE_SECRET = "dedupe-secret"


def load_export_service_module():
    try:
        return importlib.import_module("wechat_ai.services.export_service")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "wechat_ai.services.export_service has not been implemented"
        ) from exc


def create_settings_with_library(root: Path) -> Settings:
    library_db = root / "library" / "wechat-ai.sqlite"
    with closing(initialize_database(library_db)) as connection:
        connection.execute(
            """
            INSERT INTO conversations (
                id,
                source_conversation_id,
                title,
                kind,
                last_message_at,
                message_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                CONVERSATION_ID,
                "source-private-id",
                CONVERSATION_TITLE,
                "group",
                1_700_000_001_000,
                2,
            ),
        )
        connection.executemany(
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    CONVERSATION_ID,
                    "source-message-1",
                    f"{DEDUPE_SECRET}-1",
                    "alice",
                    "Alice",
                    1_700_000_000_000,
                    "text",
                    "Synthetic hello",
                    json.dumps({"private": RAW_SECRET}),
                ),
                (
                    CONVERSATION_ID,
                    "source-message-2",
                    f"{DEDUPE_SECRET}-2",
                    "bob",
                    "Bob",
                    1_700_000_001_000,
                    "image",
                    "[图片]",
                    json.dumps({"private": RAW_SECRET}),
                ),
            ),
        )
        connection.commit()

    return Settings(
        project_root=root,
        source_dir=root / "sources",
        library_db=library_db,
        output_dir=root / "exports",
        web_dist=root / "web" / "dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


def test_markdown_export_has_safe_generated_name_and_allowed_content_only(
    tmp_path: Path,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)

    result = export_service.ExportService(settings).export_conversation(
        CONVERSATION_ID,
        "markdown",
    )

    assert result.format == "markdown"
    assert result.message_count == 2
    assert Path(result.file_name).name == result.file_name
    assert result.file_name.endswith(".md")
    assert not set('<>:"/\\|?*').intersection(result.file_name)
    assert ".." not in result.file_name

    target = settings.output_dir / "markdown" / result.file_name
    assert target.is_file()
    assert target.resolve().parent == (settings.output_dir / "markdown").resolve()
    content = target.read_text(encoding="utf-8")
    assert f"# {CONVERSATION_TITLE}" in content
    assert "Alice" in content
    assert "Synthetic hello" in content
    assert "Bob" in content
    assert "[图片]" in content
    assert RAW_SECRET not in content
    assert DEDUPE_SECRET not in content
    assert str(settings.library_db) not in content


def test_json_export_contains_only_public_conversation_and_message_fields(
    tmp_path: Path,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)

    result = export_service.ExportService(settings).export_conversation(
        CONVERSATION_ID,
        "json",
    )

    target = settings.output_dir / "json" / result.file_name
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert result.format == "json"
    assert result.message_count == 2
    assert result.file_name.endswith(".json")
    assert set(payload) == {"conversation", "messages"}
    assert set(payload["conversation"]) == {
        "id",
        "title",
        "kind",
        "lastMessageAt",
        "messageCount",
    }
    assert set(payload["messages"][0]) == {
        "id",
        "senderId",
        "senderName",
        "sentAt",
        "type",
        "content",
    }
    assert payload["messages"][0]["content"] == "Synthetic hello"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert RAW_SECRET not in serialized
    assert DEDUPE_SECRET not in serialized
    assert "source-private-id" not in serialized
    assert "source-message-1" not in serialized
    assert str(settings.library_db) not in serialized


def test_export_flushes_and_atomically_replaces_from_same_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)
    real_fsync = export_service.os.fsync
    real_replace = export_service.os.replace
    fsynced_descriptors: list[int] = []
    replacements: list[tuple[Path, Path]] = []

    def record_fsync(descriptor: int) -> None:
        fsynced_descriptors.append(descriptor)
        real_fsync(descriptor)

    def record_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(export_service.os, "fsync", record_fsync)
    monkeypatch.setattr(export_service.os, "replace", record_replace)

    result = export_service.ExportService(settings).export_conversation(
        CONVERSATION_ID,
        "markdown",
    )

    final_path = settings.output_dir / "markdown" / result.file_name
    assert fsynced_descriptors
    assert len(replacements) == 1
    temporary_path, replaced_path = replacements[0]
    assert temporary_path.parent == replaced_path.parent == final_path.parent
    assert replaced_path == final_path
    assert temporary_path.suffix == ".tmp"
    assert final_path.is_file()
    assert not temporary_path.exists()


def test_replace_failure_preserves_existing_file_and_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)
    service = export_service.ExportService(settings)
    first = service.export_conversation(CONVERSATION_ID, "markdown")
    final_path = settings.output_dir / "markdown" / first.file_name
    original = final_path.read_bytes()

    def fail_replace(
        _source: os.PathLike[str],
        _target: os.PathLike[str],
    ) -> None:
        raise OSError(r"C:\private\must-not-leak")

    monkeypatch.setattr(export_service.os, "replace", fail_replace)

    with pytest.raises(export_service.ExportServiceError) as captured:
        service.export_conversation(CONVERSATION_ID, "markdown")

    error = captured.value
    assert error.code == "EXPORT_FAILED"
    assert error.message == "导出失败，未生成文件。"
    assert error.retryable is False
    assert error.details == {}
    assert "must-not-leak" not in str(error)
    assert final_path.read_bytes() == original
    assert list(final_path.parent.glob("*.tmp")) == []


def test_fsync_failure_leaves_no_final_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("private write failure")

    monkeypatch.setattr(export_service.os, "fsync", fail_fsync)

    with pytest.raises(export_service.ExportServiceError):
        export_service.ExportService(settings).export_conversation(
            CONVERSATION_ID,
            "json",
        )

    output_directory = settings.output_dir / "json"
    assert list(output_directory.iterdir()) == []


def test_unknown_conversation_has_stable_error_and_creates_no_output(
    tmp_path: Path,
) -> None:
    export_service = load_export_service_module()
    settings = create_settings_with_library(tmp_path)

    with pytest.raises(export_service.ExportServiceError) as captured:
        export_service.ExportService(settings).export_conversation(
            "missing-conversation",
            "markdown",
        )

    error = captured.value
    assert error.code == "CONVERSATION_NOT_FOUND"
    assert error.message == "未找到指定会话。"
    assert error.retryable is False
    assert error.details == {}
    assert not settings.output_dir.exists()
