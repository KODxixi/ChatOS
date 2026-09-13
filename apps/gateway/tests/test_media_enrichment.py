from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import wave
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


def _wav_payload() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x00")
    return output.getvalue()


def _seed_media_database(path: Path, asset_root: Path) -> sqlite3.Connection:
    from wechat_ai.database import initialize_database

    asset_root.mkdir(parents=True, exist_ok=True)
    image = asset_root / "image.png"
    image.write_bytes(b"synthetic-image")
    connection = initialize_database(path)
    connection.execute(
        """
        INSERT INTO conversations(id, source_conversation_id, title, kind, message_count)
        VALUES ('conversation-1', 'conversation-1', 'Media', 'direct', 1)
        """
    )
    cursor = connection.execute(
        """
        INSERT INTO messages(conversation_id, dedupe_key, sender_name, sent_at, type, content)
        VALUES ('conversation-1', 'media-1', 'Alice', 1, 'image', '')
        """
    )
    part = connection.execute(
        """
        INSERT INTO message_parts(message_id, ordinal, kind, status)
        VALUES (?, 0, 'image', 'resolved')
        """,
        (cursor.lastrowid,),
    )
    connection.execute(
        """
        INSERT INTO assets(part_id, ordinal, kind, status, file_name, mime_type, local_path, sha256)
        VALUES (?, 0, 'image', 'resolved', 'image.png', 'image/png', 'image.png', 'asset-hash')
        """,
        (part.lastrowid,),
    )
    connection.commit()
    return connection


def test_media_enrichment_persists_ocr_as_a_rebuildable_derivative(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    with closing(_seed_media_database(database_path, asset_root)) as connection:
        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=lambda path: "验收清单",
        )
        result = service.enrich_pending(connection)

        derivative = connection.execute(
            "SELECT kind, text_content, status, engine FROM media_derivatives"
        ).fetchone()
        part_text = connection.execute(
            "SELECT text_content FROM message_parts"
        ).fetchone()[0]

    assert result.ready == 1
    assert tuple(derivative) == ("ocr", "验收清单", "ready", "custom")
    assert part_text == "验收清单"


def test_media_enrichment_is_idempotent_and_skips_missing_assets(tmp_path: Path) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    with closing(_seed_media_database(database_path, asset_root)) as connection:
        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=lambda path: "验收清单",
        )
        first = service.enrich_pending(connection)
        second = service.enrich_pending(connection)
        count = connection.execute("SELECT COUNT(*) FROM media_derivatives").fetchone()[0]

    assert first.ready == 1
    assert second.skipped == 1
    assert count == 1


def test_failed_media_enrichment_is_retryable_for_the_same_content(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    attempts = 0

    def extract(_path: Path) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic extractor failure")
        return "重试成功"

    with closing(_seed_media_database(database_path, asset_root)) as connection:
        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=extract,
        )
        failed = service.enrich_pending(connection)
        retried = service.enrich_pending(connection)
        derivative = connection.execute(
            "SELECT status, text_content, error_code FROM media_derivatives"
        ).fetchone()

    assert failed.errors == 1
    assert retried.ready == 1
    assert attempts == 2
    assert tuple(derivative) == ("ready", "重试成功", None)


def test_only_ready_media_derivative_becomes_searchable(tmp_path: Path) -> None:
    from wechat_ai.services.knowledge_projection_service import project_knowledge
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    attempts = 0

    def extract(_path: Path) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic extractor failure")
        return "仅 ready 可检索"

    with closing(_seed_media_database(database_path, asset_root)) as connection:
        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=extract,
        )
        service.enrich_pending(connection)
        project_knowledge(connection)
        failed_count = connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE text_content = ?",
            ("仅 ready 可检索",),
        ).fetchone()[0]

        service.enrich_pending(connection)
        project_knowledge(connection)
        ready_count = connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE text_content = ?",
            ("仅 ready 可检索",),
        ).fetchone()[0]

    assert failed_count == 0
    assert ready_count == 1


def test_ready_assets_do_not_starve_later_pending_media(tmp_path: Path) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    second_image = asset_root / "second.png"
    with closing(_seed_media_database(database_path, asset_root)) as connection:
        second_image.write_bytes(b"second-synthetic-image")
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name, sent_at, type, content
            ) VALUES ('conversation-1', 'media-2', 'Alice', 2, 'image', '')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(message_id, ordinal, kind, status)
            VALUES (?, 0, 'image', 'resolved')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO assets(
                part_id, ordinal, kind, status, mime_type, local_path, sha256
            ) VALUES (?, 0, 'image', 'resolved', 'image/png', 'second.png', 'second-hash')
            """,
            (part.lastrowid,),
        )
        connection.commit()

        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=lambda path: path.stem,
        )
        first = service.enrich_pending(connection, limit=1)
        second = service.enrich_pending(connection, limit=1)
        ready_count = connection.execute(
            "SELECT COUNT(*) FROM media_derivatives WHERE status = 'ready'"
        ).fetchone()[0]

    assert first.ready == 1
    assert second.ready == 1
    assert ready_count == 2


def test_media_enrichment_skips_no_text_and_reports_later_batches(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    attempts = 0

    def extract(path: Path) -> str | None:
        nonlocal attempts
        attempts += 1
        return None if path.name == "image.png" else path.stem

    with closing(_seed_media_database(database_path, asset_root)) as connection:
        second_image = asset_root / "second.png"
        second_image.write_bytes(b"second-synthetic-image")
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name, sent_at, type, content
            ) VALUES ('conversation-1', 'media-2', 'Alice', 2, 'image', '')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(message_id, ordinal, kind, status)
            VALUES (?, 0, 'image', 'resolved')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO assets(
                part_id, ordinal, kind, status, mime_type, local_path, sha256
            ) VALUES (?, 0, 'image', 'resolved', 'image/png', 'second.png', 'second-hash')
            """,
            (part.lastrowid,),
        )
        connection.commit()

        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=extract,
        )
        first = service.enrich_pending(connection, limit=1)
        second = service.enrich_pending(connection, limit=1)

    assert first.no_text == 1
    assert first.has_more is True
    assert second.ready == 1
    assert second.has_more is False
    assert attempts == 2


def test_media_batch_followup_requires_progress_and_remaining_work() -> None:
    from wechat_ai.app import _media_followup_needed
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentResult

    assert _media_followup_needed(
        MediaEnrichmentResult(has_more=True, no_text=1)
    ) is True
    assert _media_followup_needed(
        MediaEnrichmentResult(has_more=True, engine_unavailable=1)
    ) is False
    assert _media_followup_needed(
        MediaEnrichmentResult(has_more=True, errors=1)
    ) is False


def test_media_job_merges_document_and_media_progress() -> None:
    from wechat_ai.app import _merge_enrichment_results
    from wechat_ai.services.document_enrichment_service import (
        DocumentEnrichmentResult,
    )
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentResult

    result = _merge_enrichment_results(
        MediaEnrichmentResult(ready=2, attempted=3, has_more=False),
        DocumentEnrichmentResult(
            ready=1,
            no_text=1,
            attempted=2,
            has_more=True,
        ),
    )

    assert result.ready == 3
    assert result.no_text == 1
    assert result.attempted == 5
    assert result.has_more is True


def test_media_followup_payload_is_unique_from_the_running_job() -> None:
    from wechat_ai.app import _media_followup_payload

    assert _media_followup_payload("job-current") == {
        "cause": "media-followup",
        "after": "job-current",
    }


def test_media_enrichment_versions_derivatives_from_actual_managed_bytes(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    with closing(_seed_media_database(database_path, asset_root)) as connection:
        image = asset_root / "image.png"

        def extract(path: Path) -> str:
            return path.read_text(encoding="utf-8")

        service = MediaEnrichmentService(
            asset_root=asset_root,
            image_text_extractor=extract,
        )
        first_payload = image.read_bytes()
        first = service.enrich_pending(connection)
        image.write_text("updated-image", encoding="utf-8")
        second_payload = image.read_bytes()
        second = service.enrich_pending(connection)
        derivatives = connection.execute(
            """
            SELECT content_hash, text_content
            FROM media_derivatives
            ORDER BY id
            """
        ).fetchall()
        asset_hash = connection.execute(
            "SELECT sha256 FROM assets"
        ).fetchone()[0]
        part_text = connection.execute(
            "SELECT text_content FROM message_parts"
        ).fetchone()[0]

    assert first.ready == 1
    assert second.ready == 1
    assert [tuple(row) for row in derivatives] == [
        (hashlib.sha256(first_payload).hexdigest(), "synthetic-image"),
        (hashlib.sha256(second_payload).hexdigest(), "updated-image"),
    ]
    assert asset_hash == hashlib.sha256(second_payload).hexdigest()
    assert part_text == "updated-image"


def test_silk_transcription_uses_a_verified_cached_wav(tmp_path: Path) -> None:
    from wechat_ai.services.media_enrichment_service import MediaEnrichmentService

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    cache_root = tmp_path / "cache"
    silk_payload = b"\x02#!SILK_V3 synthetic"
    transcribed_paths: list[Path] = []

    def transcribe(path: Path) -> str:
        transcribed_paths.append(path)
        with wave.open(str(path), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getnframes() == 1
        return "语音转写"

    with closing(_seed_media_database(database_path, asset_root)) as connection:
        voice = asset_root / "voice.silk"
        voice.write_bytes(silk_payload)
        connection.execute(
            """
            UPDATE assets
            SET kind = 'audio', mime_type = 'audio/silk',
                local_path = 'voice.silk', sha256 = 'stale-hash'
            """
        )
        connection.execute("UPDATE message_parts SET kind = 'audio'")
        connection.commit()

        service = MediaEnrichmentService(
            asset_root=asset_root,
            cache_root=cache_root,
            audio_transcriber=transcribe,
            silk_decoder=lambda _payload: _wav_payload(),
        )
        result = service.enrich_pending(connection)
        derivative = connection.execute(
            "SELECT kind, status, text_content FROM media_derivatives"
        ).fetchone()
        metadata_json = connection.execute(
            "SELECT metadata_json FROM assets"
        ).fetchone()[0]

    source_hash = hashlib.sha256(silk_payload).hexdigest()
    assert result.ready == 1
    assert transcribed_paths == [cache_root / "audio" / f"{source_hash}.wav"]
    assert tuple(derivative) == ("transcript", "ready", "语音转写")
    assert json.loads(metadata_json)["_playback"] == {
        "errorCode": None,
        "sourceHash": source_hash,
        "status": "ready",
    }


def test_whisper_cpp_adapter_uses_configured_local_binary(tmp_path: Path) -> None:
    from wechat_ai.services.media_enrichment_service import _whisper_cpp_transcriber

    audio = tmp_path / "voice.silk"
    audio.write_bytes(b"synthetic")
    completed = type("Completed", (), {"returncode": 0, "stdout": "\n你好\n"})()
    with patch.dict(
        "os.environ",
        {
            "CHATBOX_WHISPER_CPP": "C:/tools/whisper-cli.exe",
            "CHATBOX_WHISPER_MODEL": "C:/models/ggml-base.bin",
        },
    ), patch("subprocess.run", return_value=completed) as run:
        text = _whisper_cpp_transcriber(audio)

    assert text == "你好"
    run.assert_called_once()
