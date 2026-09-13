from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_operations_service import FakeCredentialStore


def _client(
    tmp_path: Path,
    *,
    job_created=None,
    sync_settings_changed=None,
    preparer=None,
) -> tuple[TestClient, FakeCredentialStore]:
    from wechat_ai.api.operations import create_operations_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.operations_service import (
        BackgroundJobService,
        SyncSettingsService,
        WechatSourceKeyService,
    )

    database_path = tmp_path / "library.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    credentials = FakeCredentialStore()
    app = FastAPI()
    app.include_router(
        create_operations_router(
            jobs=BackgroundJobService(
                connection_factory,
                clock=lambda: "2026-08-02T00:00:00Z",
                id_factory=lambda: "job-1",
            ),
            keys=WechatSourceKeyService(
                connection_factory,
                credential_store=credentials,
                verifier=lambda _database_id, _key, _salt: True,
                clock=lambda: "2026-08-02T00:00:00Z",
                id_factory=lambda: "key-ref-1",
            ),
            sync_settings=SyncSettingsService(connection_factory),
            job_created=job_created,
            sync_settings_changed=sync_settings_changed,
            preparer=preparer,
        ),
        prefix="/api",
    )
    return TestClient(app), credentials


def test_job_and_sync_routes_match_v13_contract(tmp_path: Path) -> None:
    sync_notifications: list[bool] = []
    job_notifications: list[bool] = []
    client, _credentials = _client(
        tmp_path,
        job_created=lambda: job_notifications.append(True),
        sync_settings_changed=lambda: sync_notifications.append(True),
    )

    created = client.post("/api/import/jobs")
    listed = client.get("/api/jobs")
    fetched = client.get("/api/jobs/job-1")
    cancelled = client.post("/api/jobs/job-1/cancel")
    defaults = client.get("/api/settings/sync")
    updated = client.patch(
        "/api/settings/sync",
        json={"enabled": True, "intervalMinutes": 60},
    )

    assert created.status_code == 202
    assert job_notifications == [True]
    assert created.json()["status"] == "queued"
    assert listed.json()["items"][0]["id"] == "job-1"
    assert fetched.json()["id"] == "job-1"
    assert cancelled.json()["status"] == "cancelled"
    assert defaults.json() == {"enabled": False, "intervalMinutes": 15}
    assert updated.json() == {"enabled": True, "intervalMinutes": 60}
    assert sync_notifications == [True]
    assert client.patch(
        "/api/settings/sync",
        json={"intervalMinutes": 1441},
    ).status_code == 422


def test_multipart_key_entry_response_never_returns_secret_or_path(
    tmp_path: Path,
) -> None:
    client, credentials = _client(tmp_path)
    secret = "aa" * 32

    response = client.post(
        "/api/setup/wechat-keys/import",
        files={
            "databaseId": (None, "message/message_0.db"),
            "key": (None, secret),
            "salt": (None, "11" * 16),
        },
    )
    status = client.get("/api/setup/status")

    assert response.status_code == 200
    assert response.json()["items"][0] == {
        "keyRef": "key-ref-1",
        "databaseId": "message/message_0.db",
        "verified": True,
        "verifiedAt": "2026-08-02T00:00:00Z",
        "saltHash": (
            "b8f12ea8c9a95d4b4641b03d9fa5a71ad30b44ed6cd4bf79"
            "3bbe1a5801b986d4"
        ),
    }
    assert secret not in response.text
    assert "credentialTarget" not in response.text
    assert status.json() == {
        "setupComplete": False,
        "dataRootConfigured": False,
        "archiveRootConfigured": False,
        "wechatSource": {"detected": False},
        "wechatKeys": {"total": 1, "verified": 1},
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
    assert credentials.values

    deleted = client.delete("/api/setup/wechat-keys/key-ref-1")
    assert deleted.status_code == 204
    assert credentials.values == {}


def test_multipart_key_file_accepts_existing_all_keys_shape(tmp_path: Path) -> None:
    client, _credentials = _client(tmp_path)

    response = client.post(
        "/api/setup/wechat-keys/import",
        files={
            "file": (
                "all_keys.json",
                b'{"message\\\\message_0.db":{"enc_key":"'
                + (b"aa" * 32)
                + b'","salt":"'
                + (b"11" * 16)
                + b'"}}',
                "application/json",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["databaseId"] == "message/message_0.db"


def test_auto_match_key_route_returns_safe_manual_fallback(tmp_path: Path) -> None:
    client, _credentials = _client(tmp_path)

    response = client.post("/api/setup/wechat-keys/auto")

    assert response.status_code == 200
    assert response.json() == {
        "matched": False,
        "matchedCount": 0,
        "items": [],
    }


def test_local_key_preparer_routes_never_return_secret_or_path(tmp_path: Path) -> None:
    class FakePreparer:
        def start(self):
            return {"state": "starting", "entryCount": 0, "errorCode": None}

        def status(self):
            return {"state": "ready", "entryCount": 1, "errorCode": None}

        def import_result(self):
            return {
                "matched": True,
                "matchedCount": 1,
                "items": [{"databaseId": "message/message_0.db"}],
            }

    client, _credentials = _client(tmp_path, preparer=FakePreparer())

    started = client.post("/api/setup/wechat-keys/preparer")
    status = client.get("/api/setup/wechat-keys/preparer")
    imported = client.post("/api/setup/wechat-keys/preparer/import")

    assert started.json() == {
        "state": "starting",
        "entryCount": 0,
        "errorCode": None,
    }
    assert status.json() == {
        "state": "ready",
        "entryCount": 1,
        "errorCode": None,
    }
    assert imported.json()["matchedCount"] == 1
    combined = started.text + status.text + imported.text
    assert "enc_key" not in combined
    assert "all_keys.json" not in combined
    assert str(tmp_path) not in combined
