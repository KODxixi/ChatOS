from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def has(self, profile_id: str) -> bool:
        return profile_id in self.values

    def get(self, profile_id: str) -> str | None:
        return self.values.get(profile_id)

    def set(self, profile_id: str, value: str) -> None:
        self.values[profile_id] = value

    def delete(self, profile_id: str) -> None:
        self.values.pop(profile_id, None)


def _database(tmp_path: Path) -> tuple[Path, object]:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory

    path = tmp_path / "library.sqlite"
    initialize_database(path).close()
    return path, create_connection_factory(path)


def test_background_jobs_are_persistent_sanitized_and_cancellable(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import (
        BackgroundJobService,
        OperationsValidationError,
    )

    database_path, connection_factory = _database(tmp_path)
    service = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: "job-1",
    )

    created = service.create_job("import", trigger_source="manual")
    listed = service.list_jobs()
    cancelled = service.request_cancel("job-1")

    assert created == {
        "id": "job-1",
        "type": "import",
        "status": "queued",
        "stage": "queued",
        "progressCurrent": 0,
        "progressTotal": None,
        "triggerSource": "manual",
        "errorCode": None,
        "retryCount": 0,
        "createdAt": "2026-08-02T00:00:00Z",
        "startedAt": None,
        "finishedAt": None,
        "updatedAt": "2026-08-02T00:00:00Z",
        "cancelRequestedAt": None,
    }
    assert listed["items"] == [created]
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelRequestedAt"] == "2026-08-02T00:00:00Z"

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT payload_json FROM background_jobs WHERE id = 'job-1'"
        ).fetchone()[0] == "{}"

    with pytest.raises(OperationsValidationError):
        service.create_job(
            "media",
            trigger_source="manual",
            payload={"body": "private message"},
        )
    with pytest.raises(OperationsValidationError):
        service.create_job(
            "media",
            trigger_source="manual",
            payload={"source": r"C:\private\wechat.db"},
        )


def test_active_background_jobs_merge_by_type_and_canonical_payload(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import BackgroundJobService

    database_path, connection_factory = _database(tmp_path)
    identifiers = iter(("job-1", "job-2"))
    service = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: next(identifiers),
    )

    first = service.create_job(
        "retrieval",
        trigger_source="manual",
        payload={"scope": "all", "version": 1},
    )
    duplicate = service.create_job(
        "retrieval",
        trigger_source="sync",
        payload={"version": 1, "scope": "all"},
    )
    service.claim_next()
    running_duplicate = service.create_job(
        "retrieval",
        trigger_source="manual",
        payload={"scope": "all", "version": 1},
    )
    service.complete("job-1")
    after_completion = service.create_job(
        "retrieval",
        trigger_source="manual",
        payload={"scope": "all", "version": 1},
    )

    assert duplicate["id"] == first["id"]
    assert running_duplicate["id"] == first["id"]
    assert after_completion["id"] == "job-2"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM background_jobs"
        ).fetchone()[0] == 2


def test_recover_interrupted_requeues_only_rebuildable_jobs(tmp_path: Path) -> None:
    from wechat_ai.services.operations_service import BackgroundJobService

    database_path, connection_factory = _database(tmp_path)
    identifiers = iter(("import-running", "retrieval-running", "retrieval-requeued"))
    service = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T01:00:00Z",
        id_factory=lambda: next(identifiers),
    )
    service.create_job("import", trigger_source="manual")
    service.create_job("retrieval", trigger_source="manual")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE background_jobs SET status = 'running', stage = 'indexing'"
        )
        connection.commit()

    recovered = service.recover_interrupted()

    assert recovered == {"interrupted": 2, "requeued": 1}
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, type, status, stage, error_code, trigger_source
            FROM background_jobs ORDER BY id
            """
        ).fetchall()
    assert rows == [
        (
            "import-running",
            "import",
            "interrupted",
            "interrupted",
            "JOB_INTERRUPTED",
            "manual",
        ),
        (
            "retrieval-requeued",
            "retrieval",
            "queued",
            "queued",
            None,
            "recovery",
        ),
        (
            "retrieval-running",
            "retrieval",
            "interrupted",
            "interrupted",
            "JOB_INTERRUPTED",
            "manual",
        ),
    ]
    assert service.recover_interrupted() == {"interrupted": 0, "requeued": 0}


def test_claim_cancel_and_terminal_job_updates_are_atomic(tmp_path: Path) -> None:
    from wechat_ai.services.operations_service import BackgroundJobService

    _database_path, connection_factory = _database(tmp_path)
    identifiers = iter(("job-cancel", "job-complete", "job-degrade", "job-fail"))
    service = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T02:00:00Z",
        id_factory=lambda: next(identifiers),
    )
    service.create_job(
        "import",
        trigger_source="manual",
        payload={"scope": "cancel"},
    )
    service.create_job(
        "media",
        trigger_source="manual",
        payload={"scope": "complete"},
    )
    service.create_job(
        "backup",
        trigger_source="manual",
        payload={"scope": "degrade"},
    )
    service.create_job(
        "semantic_graph",
        trigger_source="manual",
        payload={"scope": "fail"},
    )

    claimed_cancel = service.claim_next()
    assert claimed_cancel is not None
    assert claimed_cancel.id == "job-cancel"
    assert claimed_cancel.payload == {"scope": "cancel"}
    service.request_cancel(claimed_cancel.id)
    assert service.should_cancel(claimed_cancel.id) is True
    assert service.complete(claimed_cancel.id)["status"] == "cancelled"
    assert service.get_job(claimed_cancel.id)["errorCode"] is None

    claimed_complete = service.claim_next()
    assert claimed_complete is not None
    assert service.complete(claimed_complete.id)["status"] == "completed"
    claimed_degrade = service.claim_next()
    assert claimed_degrade is not None
    assert service.degrade(
        claimed_degrade.id,
        error_code="PARTIAL_RESULT",
    )["status"] == "degraded"
    claimed_fail = service.claim_next()
    assert claimed_fail is not None
    assert service.fail(
        claimed_fail.id,
        error_code="JOB_HANDLER_FAILED",
    )["status"] == "failed"
    assert service.claim_next() is None


def test_claim_next_never_returns_one_job_to_two_workers(tmp_path: Path) -> None:
    from wechat_ai.services.operations_service import BackgroundJobService

    _database_path, connection_factory = _database(tmp_path)
    service = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T02:00:00Z",
        id_factory=lambda: "job-atomic",
    )
    service.create_job("backup", trigger_source="manual")
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        return service.claim_next()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: claim(), range(2)))

    claimed = [result for result in results if result is not None]
    assert [result.id for result in claimed] == ["job-atomic"]


def test_sync_settings_use_app_meta_defaults_and_validate_partial_updates(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import (
        OperationsValidationError,
        SyncSettingsService,
    )

    database_path, connection_factory = _database(tmp_path)
    service = SyncSettingsService(connection_factory)

    assert service.get() == {"enabled": False, "intervalMinutes": 15}
    assert service.patch(enabled=True) == {
        "enabled": True,
        "intervalMinutes": 15,
    }
    assert service.patch(interval_minutes=30) == {
        "enabled": True,
        "intervalMinutes": 30,
    }
    with pytest.raises(OperationsValidationError):
        service.patch(interval_minutes=4)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT value FROM app_meta WHERE key = 'sync_enabled'"
        ).fetchone()[0] == "1"
        assert connection.execute(
            "SELECT value FROM app_meta WHERE key = 'sync_interval_minutes'"
        ).fetchone()[0] == "30"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'operational_settings'"
        ).fetchone() is None


def test_wechat_keys_store_only_credential_references_and_support_deletion(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import WechatSourceKeyService

    database_path, connection_factory = _database(tmp_path)
    credentials = FakeCredentialStore()
    service = WechatSourceKeyService(
        connection_factory,
        credential_store=credentials,
        verifier=lambda database_id, key, salt: (
            database_id == "message/message_0.db"
            and len(key) == 32
            and salt == bytes.fromhex("11" * 16)
        ),
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: "key-ref-1",
    )

    imported = service.import_entries(
        [
            {
                "databaseId": "message/message_0.db",
                "key": "aa" * 32,
                "salt": "11" * 16,
            }
        ]
    )
    assert imported == {
        "items": [
            {
                "keyRef": "key-ref-1",
                "databaseId": "message/message_0.db",
                "verified": True,
                "verifiedAt": "2026-08-02T00:00:00Z",
                "saltHash": (
                    "b8f12ea8c9a95d4b4641b03d9fa5a71ad30b44ed6cd4bf79"
                    "3bbe1a5801b986d4"
                ),
            }
        ]
    }
    target = "Gchat/WeChat/message/message_0.db"
    assert credentials.values == {target: "aa" * 32}

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM wechat_source_key_refs"
        ).fetchone()
        assert row == (
            "key-ref-1",
            "message/message_0.db",
            imported["items"][0]["saltHash"],
            "2026-08-02T00:00:00Z",
            target,
        )
        assert "aa" * 32 not in json.dumps(row)

    service.delete("key-ref-1")
    assert credentials.values == {}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM wechat_source_key_refs"
        ).fetchone() is None


def test_wechat_keys_auto_match_project_key_file_without_exposing_secret(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import WechatSourceKeyService

    project_root = tmp_path / "project"
    project_root.mkdir()
    project_root.joinpath("config.json").write_text(
        json.dumps({"db_dir": str(tmp_path / "wechat-data")}),
        encoding="utf-8",
    )
    project_root.joinpath("all_keys.json").write_text(
        json.dumps(
            {
                "message\\message_0.db": {
                    "enc_key": "aa" * 32,
                    "salt": "11" * 16,
                }
            }
        ),
        encoding="utf-8",
    )
    _database_path, connection_factory = _database(tmp_path)
    credentials = FakeCredentialStore()
    service = WechatSourceKeyService(
        connection_factory,
        credential_store=credentials,
        project_root=project_root,
        verifier=lambda database_id, key, salt: (
            database_id == "message/message_0.db"
            and key == bytes.fromhex("aa" * 32)
            and salt == bytes.fromhex("11" * 16)
        ),
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: "key-ref-auto",
    )

    result = service.auto_match()

    assert result["matched"] is True
    assert result["matchedCount"] == 1
    assert result["items"][0]["databaseId"] == "message/message_0.db"
    assert "aa" * 32 not in json.dumps(result)
    assert str(project_root) not in json.dumps(result)
    assert credentials.values == {
        "Gchat/WeChat/message/message_0.db": "aa" * 32
    }


def test_wechat_keys_auto_match_reports_manual_fallback_when_file_is_missing(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import WechatSourceKeyService

    project_root = tmp_path / "project"
    project_root.mkdir()
    _database_path, connection_factory = _database(tmp_path)
    service = WechatSourceKeyService(
        connection_factory,
        credential_store=FakeCredentialStore(),
        project_root=project_root,
        verifier=lambda _database_id, _key, _salt: True,
    )

    assert service.auto_match() == {
        "matched": False,
        "matchedCount": 0,
        "items": [],
    }


def test_parse_wechat_key_file_accepts_pinned_upstream_metadata() -> None:
    from wechat_ai.services.operations_service import parse_wechat_key_file

    payload = json.dumps(
        {
            "message\\message_0.db": {
                "enc_key": "aa" * 32,
                "salt": "11" * 16,
                "size_mb": 4.0,
            },
            "_db_dir": r"C:\private\wechat\db_storage",
        }
    ).encode()

    assert parse_wechat_key_file(payload) == [
        {
            "databaseId": r"message\message_0.db",
            "enc_key": "aa" * 32,
            "salt": "11" * 16,
            "size_mb": 4.0,
        }
    ]


def test_setup_status_does_not_require_a_gchat_model_runtime(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.operations_service import WechatSourceKeyService

    _database_path, connection_factory = _database(tmp_path)
    credentials = FakeCredentialStore()
    default_service = WechatSourceKeyService(
        connection_factory,
        credential_store=credentials,
        verifier=lambda _database_id, _key, _salt: True,
    )
    assert default_service.status() == {
        "setupComplete": False,
        "dataRootConfigured": False,
        "archiveRootConfigured": False,
        "wechatSource": {"detected": False},
        "wechatKeys": {"total": 0, "verified": 0},
        "defaultModel": {
            "profileId": None,
            "ready": False,
            "privacyConfirmed": False,
        },
        "firstImport": {
            "started": False,
            "completed": False,
            "jobId": None,
        },
    }

    ready_service = WechatSourceKeyService(
        connection_factory,
        credential_store=credentials,
        verifier=lambda _database_id, _key, _salt: True,
        data_root_probe=lambda: True,
        archive_root_probe=lambda: True,
        wechat_source_probe=lambda: True,
        default_model_probe=lambda: {
            "profileId": None,
            "ready": False,
            "privacyConfirmed": False,
        },
        first_import_probe=lambda: {
            "started": True,
            "completed": True,
            "jobId": "job-import",
        },
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: "key-ref-ready",
    )
    ready_service.import_entries(
        [
            {
                "databaseId": "message/message_0.db",
                "key": "aa" * 32,
                "salt": "11" * 16,
            }
        ]
    )
    assert ready_service.status()["setupComplete"] is True


def test_project_wechat_key_verifier_checks_source_salt_and_decrypted_header(
    tmp_path: Path,
) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from wechat_ai.services.operations_service import (
        create_project_wechat_key_verifier,
    )

    project_root = tmp_path / "project"
    encrypted_root = tmp_path / "encrypted"
    source = encrypted_root / "message" / "message_0.db"
    source.parent.mkdir(parents=True)
    project_root.mkdir()
    project_root.joinpath("config.json").write_text(
        json.dumps({"db_dir": str(encrypted_root)}),
        encoding="utf-8",
    )
    key = bytes.fromhex("aa" * 32)
    salt = bytes.fromhex("11" * 16)
    iv = bytes.fromhex("22" * 16)
    plaintext = bytearray(4_000)
    plaintext[0:2] = (4_096).to_bytes(2, "big")
    plaintext[2:5] = bytes((1, 1, 80))
    plaintext[5:8] = bytes((64, 32, 32))
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(bytes(plaintext)) + encryptor.finalize()
    source.write_bytes(salt + ciphertext + iv + bytes(64))
    verifier = create_project_wechat_key_verifier(project_root)

    assert verifier("message/message_0.db", key, salt) is True
    assert verifier("message/message_0.db", bytes(32), salt) is False
    assert verifier("message/message_0.db", key, bytes(16)) is False
    assert verifier("../message/message_0.db", key, salt) is False
