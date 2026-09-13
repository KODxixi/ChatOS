from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path: Path) -> TestClient:
    from wechat_ai.api.maintenance import create_maintenance_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.maintenance_service import (
        MaintenanceService,
        UpdateService,
    )

    library = tmp_path / "data" / "library" / "gchat.sqlite"
    initialize_database(library).close()
    service = MaintenanceService(
        library_db=library,
        backup_dir=tmp_path / "data" / "backups",
        state_dir=tmp_path / "data" / "state",
        clock=lambda: "20260802T030000Z",
    )
    updates = UpdateService(
        current_version="1.3.0-beta.1",
        manifest_url=None,
    )
    app = FastAPI()
    app.include_router(
        create_maintenance_router(maintenance=service, updates=updates),
        prefix="/api",
    )
    return TestClient(app)


def test_backup_restore_diagnostic_and_update_routes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post("/api/backups")
    listed = client.get("/api/backups")
    restore = client.post(
        "/api/restore-plan",
        json={"backupId": created.json()["id"], "confirmed": True},
    )
    diagnostic = client.post("/api/diagnostics")
    update = client.get("/api/updates/check")

    assert created.status_code == 201
    assert listed.json()["items"][0]["id"] == created.json()["id"]
    assert restore.json() == {
        "scheduled": True,
        "backupId": created.json()["id"],
    }
    assert diagnostic.headers["content-type"] == "application/zip"
    assert diagnostic.headers["content-disposition"] == (
        'attachment; filename="chatbox-diagnostic.zip"'
    )
    assert update.json() == {
        "status": "disabled",
        "currentVersion": "1.3.0-beta.1",
    }


def test_update_manifest_is_validated_and_reports_available_version() -> None:
    from wechat_ai.services.maintenance_service import UpdateService

    manifest = json.dumps(
        {
            "version": "1.3.0-beta.2",
            "downloadUrl": "https://github.com/example/gchat/releases/tag/v1.3.0-beta.2",
            "sha256": "a" * 64,
        }
    ).encode()
    service = UpdateService(
        current_version="1.3.0-beta.1",
        manifest_url="https://github.com/example/gchat/releases/latest/download/update.json",
        fetcher=lambda _url: manifest,
    )

    assert service.check() == {
        "status": "available",
        "currentVersion": "1.3.0-beta.1",
        "version": "1.3.0-beta.2",
        "downloadUrl": "https://github.com/example/gchat/releases/tag/v1.3.0-beta.2",
        "sha256": "a" * 64,
    }
