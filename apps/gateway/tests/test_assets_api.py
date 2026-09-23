from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import wave
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from wechat_ai.database import initialize_database
from wechat_ai.services.knowledge_projection_service import project_knowledge


def _seed_asset(path: Path) -> int:
    with closing(initialize_database(path)) as connection:
        connection.execute(
            """
            INSERT INTO conversations(id, source_conversation_id, title, kind)
            VALUES ('team', 'team', 'Team', 'group')
            """
        )
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('team', 'team:image', 'Alice', 1000, 'image', '[image]')
            """
        )
        project_knowledge(connection)
        connection.commit()
        return int(connection.execute("SELECT id FROM assets").fetchone()[0])


def _client(
    database_path: Path,
    asset_root: Path,
    *,
    silk_decoder=None,
    cache_budget_bytes=None,
) -> TestClient:
    from wechat_ai.api.assets import create_assets_router
    from wechat_ai.api.knowledge import create_knowledge_router

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection

    app = FastAPI()
    asset_router_options = {}
    if silk_decoder is not None:
        asset_router_options["silk_decoder"] = silk_decoder
    if cache_budget_bytes is not None:
        asset_router_options["cache_budget_bytes"] = cache_budget_bytes
    app.include_router(
        create_assets_router(
            connect,
            asset_root=asset_root,
            cache_root=database_path.parent / "cache",
            **asset_router_options,
        ),
        prefix="/api",
    )
    app.include_router(
        create_knowledge_router(connect, asset_root=asset_root),
        prefix="/api",
    )
    return TestClient(app)


def _wav_payload(pcm: bytes = b"\x00\x00") -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(pcm)
    return output.getvalue()


def test_asset_content_requires_resolved_safe_local_file(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    client = _client(database_path, asset_root)

    unresolved = client.get(f"/api/assets/{asset_id}")
    assert unresolved.status_code == 200
    assert unresolved.headers["content-type"] == "image/png"
    assert unresolved.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "local_path" not in unresolved.text

    encrypted_path = asset_root / "encrypted" / "board.dat"
    encrypted_path.parent.mkdir()
    encrypted_path.write_bytes(b"archived-encrypted-image")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET mime_type = 'application/x-wechat-image-v2',
                byte_size = ?,
                local_path = 'encrypted/board.dat'
            WHERE id = ?
            """,
            (len(b"archived-encrypted-image"), asset_id),
        )
        connection.commit()

    archived_unresolved = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]
    assert archived_unresolved["status"] == "unresolved"
    assert archived_unresolved["readinessState"] == "discovered"
    assert archived_unresolved["hasLocalFile"] is True
    assert archived_unresolved["contentUrl"] is None
    assert client.get(f"/api/assets/{asset_id}").content == unresolved.content

    image_path = asset_root / "images" / "board.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"safe-image-bytes")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                file_name = 'board.png',
                mime_type = 'image/png',
                byte_size = ?,
                local_path = 'images/board.png'
            WHERE id = ?
            """,
            (len(b"safe-image-bytes"), asset_id),
        )
        connection.commit()

    content = client.get(f"/api/assets/{asset_id}")
    assert content.status_code == 200
    assert content.content == b"safe-image-bytes"
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"].startswith("inline;")
    knowledge_asset = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]
    assert knowledge_asset == {
        "id": asset_id,
        "messagePartId": knowledge_asset["messagePartId"],
        "kind": "image",
        "status": "resolved",
        "externalUrl": None,
        "hasLocalFile": True,
        "fileName": "board.png",
        "mimeType": "image/png",
        "byteSize": len(b"safe-image-bytes"),
        "contentUrl": f"/api/assets/{asset_id}",
        "readinessState": "resolved",
        "processingUrl": None,
        "playbackUrl": None,
        "errorCode": None,
        "derivative": None,
    }
    assert "localPath" not in knowledge_asset
    assert "rawMetadata" not in knowledge_asset

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE assets SET mime_type = ? WHERE id = ?",
            ("image/png\r\nx-injected: yes", asset_id),
        )
        connection.commit()
    sanitized = client.get(f"/api/assets/{asset_id}")
    assert sanitized.status_code == 200
    assert "x-injected" not in sanitized.headers

    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"must-not-leak")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE assets SET local_path = '../secret.txt' WHERE id = ?",
            (asset_id,),
        )
        connection.commit()

    escaped = client.get(f"/api/assets/{asset_id}")
    missing = client.get("/api/assets/999999")
    assert escaped.status_code == 404
    assert missing.status_code == 404
    assert b"must-not-leak" not in escaped.content
    assert str(outside) not in escaped.text


def test_ready_derivative_is_processed_only_after_its_text_is_indexed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    image_payload = b"\x89PNG\r\n\x1a\nsynthetic"
    image_path = asset_root / "board.png"
    image_path.write_bytes(image_payload)
    content_hash = "a" * 64
    with closing(sqlite3.connect(database_path)) as connection:
        part_id = connection.execute(
            "SELECT part_id FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved', kind = 'image', mime_type = 'image/png',
                local_path = 'board.png', sha256 = ?
            WHERE id = ?
            """,
            (content_hash, asset_id),
        )
        connection.execute(
            """
            UPDATE message_parts
            SET status = 'resolved', text_content = '图中验收结论'
            WHERE id = ?
            """,
            (part_id,),
        )
        connection.execute(
            """
            INSERT INTO media_derivatives(
                asset_id, kind, status, text_content, engine,
                content_hash, created_at, updated_at
            ) VALUES (?, 'ocr', 'ready', '图中验收结论', 'custom', ?, 1, 1)
            """,
            (asset_id, content_hash),
        )
        connection.commit()

    client = _client(database_path, asset_root)
    before = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    with closing(sqlite3.connect(database_path)) as connection:
        project_knowledge(connection)
        connection.commit()

    after = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE media_derivatives
            SET status = 'error', text_content = '',
                error_code = 'EXTRACTOR_FAILED', updated_at = 2
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        connection.commit()

    failed = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    assert before["readinessState"] == "previewable"
    assert before["derivative"] == {
        "kind": "ocr",
        "status": "ready",
        "errorCode": None,
        "indexed": False,
    }
    assert after["readinessState"] == "processed"
    assert after["derivative"]["indexed"] is True
    assert failed["readinessState"] == "error"
    assert failed["errorCode"] == "EXTRACTOR_FAILED"
    assert failed["contentUrl"] == f"/api/assets/{asset_id}"


def test_document_derivative_is_reported_as_processed_for_a_file_asset(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    document = asset_root / "brief.pdf"
    document.write_bytes(b"pdf bytes")
    content_hash = hashlib.sha256(document.read_bytes()).hexdigest()
    with closing(sqlite3.connect(database_path)) as connection:
        part_id = connection.execute(
            "SELECT part_id FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE messages SET type = 'file', content = '' WHERE id = "
            "(SELECT message_id FROM message_parts WHERE id = ?)",
            (part_id,),
        )
        connection.execute(
            """
            UPDATE message_parts
            SET kind = 'file', status = 'resolved', text_content = '文件正文'
            WHERE id = ?
            """,
            (part_id,),
        )
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved', kind = 'file', mime_type = 'application/pdf',
                file_name = 'brief.pdf', local_path = 'brief.pdf',
                sha256 = ?, byte_size = ?
            WHERE id = ?
            """,
            (content_hash, document.stat().st_size, asset_id),
        )
        connection.execute(
            """
            INSERT INTO media_derivatives(
                asset_id, kind, status, text_content, engine,
                content_hash, created_at, updated_at
            ) VALUES (?, 'document', 'ready', '文件正文', 'document', ?, 1, 1)
            """,
            (asset_id, content_hash),
        )
        project_knowledge(connection)
        connection.commit()

    asset = _client(database_path, asset_root).get(
        "/api/knowledge/items",
        params={"kind": "file"},
    ).json()["items"][0]["assets"][0]

    assert asset["derivative"] == {
        "kind": "document",
        "status": "ready",
        "errorCode": None,
        "indexed": True,
    }
    assert asset["readinessState"] == "processed"


def test_silk_playback_is_decoded_once_into_local_wav_cache(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    silk_path.write_bytes(b"\x02#!SILK_V3 synthetic")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                kind = 'audio',
                file_name = 'voice.silk',
                mime_type = 'audio/silk',
                byte_size = ?,
                local_path = 'voice.silk',
                sha256 = 'synthetic-silk'
            WHERE id = ?
            """,
            (silk_path.stat().st_size, asset_id),
        )
        connection.commit()

    decoded_inputs: list[bytes] = []
    wav_payload = _wav_payload()

    def decode(raw: bytes) -> bytes:
        decoded_inputs.append(raw)
        return wav_payload

    client = _client(
        database_path,
        asset_root,
        silk_decoder=decode,
    )

    before = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]
    assert before["readinessState"] == "resolved"
    assert before["processingUrl"] == f"/api/assets/{asset_id}/playback"
    assert before["playbackUrl"] is None

    first = client.get(f"/api/assets/{asset_id}/playback")
    second = client.get(f"/api/assets/{asset_id}/playback")

    after = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    assert first.status_code == 200
    assert first.content == wav_payload
    assert first.headers["content-type"] == "audio/wav"
    assert first.headers["content-disposition"].startswith("inline;")
    assert second.content == first.content
    assert decoded_inputs == [silk_path.read_bytes()]
    assert after["readinessState"] == "previewable"
    assert after["processingUrl"] == f"/api/assets/{asset_id}/playback"
    assert after["playbackUrl"] == f"/api/assets/{asset_id}/playback"
    assert after["errorCode"] is None
    with closing(sqlite3.connect(database_path)) as connection:
        source_hash, metadata_json = connection.execute(
            "SELECT sha256, metadata_json FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
    assert source_hash == hashlib.sha256(silk_path.read_bytes()).hexdigest()
    assert json.loads(metadata_json)["_playback"] == {
        "errorCode": None,
        "sourceHash": source_hash,
        "status": "ready",
    }

    partial = client.get(
        f"/api/assets/{asset_id}/playback",
        headers={"range": "bytes=0-3"},
    )
    head = client.head(f"/api/assets/{asset_id}/playback")
    assert partial.status_code == 206
    assert partial.content == b"RIFF"
    assert head.status_code == 200
    assert head.content == b""


def test_silk_playback_uses_actual_content_hash_not_stored_digest(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    first_silk = b"\x02#!SILK_V3 A"
    second_silk = b"\x02#!SILK_V3 B"
    silk_path.write_bytes(first_silk)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                kind = 'audio',
                file_name = 'voice.silk',
                mime_type = 'audio/silk',
                byte_size = ?,
                local_path = 'voice.silk',
                sha256 = ?
            WHERE id = ?
            """,
            (silk_path.stat().st_size, "0" * 64, asset_id),
        )
        connection.commit()

    decoded_inputs: list[bytes] = []

    def decode(raw: bytes) -> bytes:
        decoded_inputs.append(raw)
        return _wav_payload(raw[-1:] * 2)

    client = _client(database_path, asset_root, silk_decoder=decode)
    first = client.get(f"/api/assets/{asset_id}/playback")
    silk_path.write_bytes(second_silk)
    second = client.get(f"/api/assets/{asset_id}/playback")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content != second.content
    assert decoded_inputs == [first_silk, second_silk]


def test_corrupt_silk_returns_bounded_decode_error(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    silk_path.write_bytes(b"\x02#!SILK_V3\x01\x02broken")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                kind = 'audio',
                file_name = 'voice.silk',
                mime_type = 'audio/silk',
                byte_size = ?,
                local_path = 'voice.silk',
                sha256 = ?
            WHERE id = ?
            """,
            (silk_path.stat().st_size, "1" * 64, asset_id),
        )
        connection.commit()

    client = _client(database_path, asset_root)
    response = client.get(
        f"/api/assets/{asset_id}/playback"
    )

    asset = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VOICE_DECODE_FAILED"
    assert response.json()["error"]["retryable"] is True
    assert str(silk_path) not in response.text
    assert asset["readinessState"] == "error"
    assert asset["processingUrl"] == f"/api/assets/{asset_id}/playback"
    assert asset["playbackUrl"] is None
    assert asset["errorCode"] == "VOICE_DECODE_FAILED"


def test_failed_silk_playback_can_retry_without_losing_the_source(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    silk_payload = b"\x02#!SILK_V3 retryable"
    silk_path.write_bytes(silk_payload)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved', kind = 'audio',
                file_name = 'voice.silk', mime_type = 'audio/silk',
                byte_size = ?, local_path = 'voice.silk'
            WHERE id = ?
            """,
            (len(silk_payload), asset_id),
        )
        connection.commit()

    attempts = 0

    def decode(_raw: bytes) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("synthetic decode failure")
        return _wav_payload()

    client = _client(database_path, asset_root, silk_decoder=decode)
    failed = client.get(f"/api/assets/{asset_id}/playback")
    failed_asset = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]
    retried = client.get(f"/api/assets/{asset_id}/playback")
    ready_asset = client.get(
        "/api/knowledge/items",
        params={"kind": "image"},
    ).json()["items"][0]["assets"][0]

    assert failed.status_code == 422
    assert failed_asset["readinessState"] == "error"
    assert failed_asset["contentUrl"] == f"/api/assets/{asset_id}"
    assert retried.status_code == 200
    assert attempts == 2
    assert ready_asset["readinessState"] == "previewable"
    assert ready_asset["errorCode"] is None


def test_silk_playback_refuses_to_exceed_cache_budget(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    silk_path.write_bytes(b"\x02#!SILK_V3 synthetic")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                kind = 'audio',
                file_name = 'voice.silk',
                mime_type = 'audio/silk',
                byte_size = ?,
                local_path = 'voice.silk'
            WHERE id = ?
            """,
            (silk_path.stat().st_size, asset_id),
        )
        connection.commit()

    response = _client(
        database_path,
        asset_root,
        silk_decoder=lambda _raw: _wav_payload(),
        cache_budget_bytes=1,
    ).get(f"/api/assets/{asset_id}/playback")

    assert response.status_code == 507
    assert response.json()["error"]["code"] == "VOICE_CACHE_FULL"


def test_silk_cache_rejects_audio_directory_link_outside_cache_root(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset_id = _seed_asset(database_path)
    silk_path = asset_root / "voice.silk"
    silk_path.write_bytes(b"\x02#!SILK_V3 synthetic")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                kind = 'audio',
                file_name = 'voice.silk',
                mime_type = 'audio/silk',
                byte_size = ?,
                local_path = 'voice.silk'
            WHERE id = ?
            """,
            (silk_path.stat().st_size, asset_id),
        )
        connection.commit()

    cache_root = database_path.parent / "cache"
    outside = tmp_path / "outside-cache"
    cache_root.mkdir()
    outside.mkdir()
    try:
        os.symlink(
            outside,
            cache_root / "audio",
            target_is_directory=True,
        )
    except OSError as error:
        pytest.skip(f"Directory links are unavailable: {error}")

    response = _client(
        database_path,
        asset_root,
        silk_decoder=lambda _raw: _wav_payload(),
    ).get(f"/api/assets/{asset_id}/playback")

    assert response.status_code == 422
    assert list(outside.iterdir()) == []
