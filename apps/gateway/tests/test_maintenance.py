from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path


def _library(path: Path) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(path)
    connection.execute(
        "INSERT INTO app_meta(key, value) VALUES ('private_probe', 'do-not-export')"
    )
    connection.commit()
    connection.close()


def test_backup_retains_three_valid_copies_and_restore_plan_is_opaque(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.maintenance_service import MaintenanceService

    library = tmp_path / "data" / "library" / "gchat.sqlite"
    backups = tmp_path / "data" / "backups"
    state = tmp_path / "data" / "state"
    _library(library)
    ticks = iter(
        (
            "20260802T010001Z",
            "20260802T010002Z",
            "20260802T010003Z",
            "20260802T010004Z",
        )
    )
    service = MaintenanceService(
        library_db=library,
        backup_dir=backups,
        state_dir=state,
        clock=lambda: next(ticks),
    )

    created = [service.create_backup() for _ in range(4)]
    listed = service.list_backups()
    planned = service.plan_restore(created[-1]["id"], confirmed=True)

    assert len(listed["items"]) == 3
    assert created[0]["id"] not in {item["id"] for item in listed["items"]}
    assert planned == {"scheduled": True, "backupId": created[-1]["id"]}
    pending = json.loads((state / "pending-restore.json").read_text("utf-8"))
    assert pending == {"backupId": created[-1]["id"]}
    assert str(tmp_path) not in json.dumps(listed)
    assert all(item["quickCheck"] == "ok" for item in listed["items"])


def test_restore_plan_requires_confirmation_and_rejects_unknown_backup(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.maintenance_service import (
        MaintenanceNotFound,
        MaintenanceService,
        MaintenanceValidationError,
    )

    library = tmp_path / "library.sqlite"
    _library(library)
    service = MaintenanceService(
        library_db=library,
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
    )

    try:
        service.plan_restore("missing", confirmed=False)
    except MaintenanceValidationError:
        pass
    else:
        raise AssertionError("restore must require explicit confirmation")

    try:
        service.plan_restore("missing", confirmed=True)
    except MaintenanceNotFound:
        pass
    else:
        raise AssertionError("unknown backup must be rejected")


def test_diagnostic_zip_contains_only_redacted_operational_summary(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.maintenance_service import MaintenanceService

    library = tmp_path / "library.sqlite"
    _library(library)
    service = MaintenanceService(
        library_db=library,
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
    )

    payload = service.diagnostic_zip()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["diagnostic.json"]
        report = json.loads(archive.read("diagnostic.json"))

    serialized = json.dumps(report)
    assert report["schemaVersion"] == 26
    assert report["quickCheck"] == "ok"
    assert "jobStatusCounts" in report
    assert "do-not-export" not in serialized
    assert str(tmp_path) not in serialized
    assert "question" not in serialized.casefold()
    assert "answer" not in serialized.casefold()
    assert "key" not in serialized.casefold()


def test_apply_pending_restore_replaces_library_only_after_validation(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.maintenance_service import MaintenanceService

    library = tmp_path / "library.sqlite"
    _library(library)
    service = MaintenanceService(
        library_db=library,
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
        clock=lambda: "20260802T020000Z",
    )
    backup = service.create_backup()
    with sqlite3.connect(library) as connection:
        connection.execute(
            "UPDATE app_meta SET value = 'changed' WHERE key = 'private_probe'"
        )
        connection.commit()
    service.plan_restore(backup["id"], confirmed=True)

    assert service.apply_pending_restore() is True
    with sqlite3.connect(library) as connection:
        value = connection.execute(
            "SELECT value FROM app_meta WHERE key = 'private_probe'"
        ).fetchone()[0]
    assert value == "do-not-export"
    assert not (tmp_path / "state" / "pending-restore.json").exists()
