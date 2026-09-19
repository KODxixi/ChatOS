from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from wechat_ai.services.knowledge_archiver import archive_library
from wechat_ai.services.local_knowledge_index import LocalKnowledgeIndex


def _database(path: Path, asset_root: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                sent_at INTEGER NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE TABLE message_parts (
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                text_content TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY,
                part_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                file_name TEXT,
                mime_type TEXT,
                byte_size INTEGER,
                external_url TEXT,
                local_path TEXT
            );
            CREATE TABLE retrieval_chunks (
                id INTEGER PRIMARY KEY,
                message_part_id INTEGER NOT NULL,
                text_content TEXT NOT NULL
            );
            INSERT INTO conversations VALUES ('team', 'Team / 核心');
            INSERT INTO messages VALUES
                (1, 'team', 'Alice', 1700000000000, 'image', '[图片]'),
                (2, 'team', 'Bob',   1700000100000, 'voice', '[语音]'),
                (3, 'team', 'Carol', 1700000200000, 'attachment', '[文件]'),
                (4, 'team', 'Alice', 1700000300000, 'text', '规范 https://example.com/spec'),
                (5, 'team', 'Alice', 1700000400000, 'text', 'accessToken=do-not-copy https://example.com/private?sessionToken=do-not-copy');
            INSERT INTO message_parts VALUES
                (11, 1, 'image', 'resolved', '白板上的方案草图'),
                (12, 2, 'audio', 'resolved', '明天下午确认方案'),
                (13, 3, 'attachment', 'unresolved', ''),
                (14, 4, 'link', 'resolved', 'https://example.com/spec'),
                (15, 5, 'link', 'resolved', 'accessToken=do-not-copy https://example.com/private?sessionToken=do-not-copy');
            INSERT INTO assets VALUES
                (21, 11, 0, 'image', 'resolved', 'board.jpg', 'image/jpeg', 11, NULL, 'image-object'),
                (22, 12, 0, 'audio', 'resolved', 'voice.wav', 'audio/wav', 11, NULL, 'audio-object'),
                (23, 13, 0, 'attachment', 'unresolved', 'proposal.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', NULL, NULL, NULL);
            INSERT INTO retrieval_chunks VALUES
                (31, 11, 'OCR: 白板上的方案草图'),
                (32, 12, '转写: 明天下午确认方案');
            """
        )
        connection.commit()
    asset_root.mkdir(parents=True)
    (asset_root / "image-object").write_bytes(b"image-bytes")
    (asset_root / "audio-object").write_bytes(b"audio-bytes")


def _connect(path: Path):
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    return connect


def test_archive_builds_rebuildable_manifest_objects_and_local_model_registry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    _database(database_path, asset_root)

    stats = archive_library(
        _connect(database_path),
        library_root=tmp_path,
        asset_root=asset_root,
    )

    knowledge_root = tmp_path / "knowledge"
    manifests = list((knowledge_root / "index").rglob("*.md"))
    assert len(manifests) == 1
    content = manifests[0].read_text(encoding="utf-8")
    assert "source_message_id: 1" in content
    assert "kind: image" in content
    assert "OCR: 白板上的方案草图" in content
    assert "kind: audio" in content
    assert "转写: 明天下午确认方案" in content
    assert "kind: link" in content and "https://example.com/spec" in content
    assert "do-not-copy" not in content
    assert "source_part_id: 13" not in content
    catalog = next((knowledge_root / "catalog").rglob("*.md")).read_text(encoding="utf-8")
    assert "kind: attachment" in catalog and "status: unresolved" in catalog
    assert "do-not-copy" not in catalog
    assert "疑似敏感内容" in catalog

    image_hash = hashlib.sha256(b"image-bytes").hexdigest()
    archived_image = knowledge_root / "archive" / "objects" / image_hash[:2] / image_hash
    assert archived_image.read_bytes() == b"image-bytes"
    registry = (knowledge_root / "local-model-registry.yaml").read_text(encoding="utf-8")
    assert "chatos:" in registry
    assert "text-embedding-qwen3-embedding-8b" in registry
    assert json.dumps(str((knowledge_root / "index").resolve())) in registry
    assert stats["assetsArchived"] == 2
    assert stats["documentsWritten"] == 1
    assert stats["catalogDocumentsWritten"] == 1
    assert stats["unresolvedAssets"] == 1


def test_local_model_index_runs_only_after_archive_and_persists_honest_status(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    _database(database_path, asset_root)
    cli_path = tmp_path / "cli.py"
    cli_path.write_text("# test seam\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"files_indexed": 1, "chunks_indexed": 4}),
            stderr="",
        )

    index = LocalKnowledgeIndex(
        library_root=tmp_path,
        cli_path=cli_path,
        python_command=("python",),
        runner=run,
    )
    assert index.start() is True
    index.refresh(_connect(database_path), asset_root=asset_root)

    status = index.status()
    assert status["status"] == "completed"
    assert status["stats"]["chunks_indexed"] == 4
    assert calls and calls[0][-1] == "index"
    assert "--kb" in calls[0] and "chatos" in calls[0]
    assert "--db" in calls[0]
    persisted = json.loads((tmp_path / "knowledge" / "index-status.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
