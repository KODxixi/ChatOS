from __future__ import annotations

import io
import json
import os
import sqlite3
import uuid
import zipfile
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..database import current_schema_version
from ..runtime_contract import APP_VERSION


Clock = Callable[[], str]
ManifestFetcher = Callable[[str], bytes]
_BACKUP_PREFIX = "gchat-backup-"
_BACKUP_SUFFIX = ".sqlite"
_PENDING_RESTORE = "pending-restore.json"


class MaintenanceError(RuntimeError):
    code = "MAINTENANCE_ERROR"


class MaintenanceValidationError(MaintenanceError, ValueError):
    code = "MAINTENANCE_REQUEST_INVALID"


class MaintenanceNotFound(MaintenanceError, LookupError):
    code = "MAINTENANCE_NOT_FOUND"


class UpdateService:
    def __init__(
        self,
        *,
        current_version: str,
        manifest_url: str | None,
        fetcher: ManifestFetcher | None = None,
    ) -> None:
        self._current_version = current_version
        self._manifest_url = manifest_url.strip() if manifest_url else None
        self._fetcher = fetcher or _fetch_manifest

    def check(self) -> dict[str, object]:
        if self._manifest_url is None:
            return {
                "status": "disabled",
                "currentVersion": self._current_version,
            }
        _require_https(self._manifest_url, "update manifest URL")
        try:
            raw = self._fetcher(self._manifest_url)
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MaintenanceError("update manifest is unavailable") from exc
        if not isinstance(payload, dict):
            raise MaintenanceValidationError("update manifest is invalid")
        version = payload.get("version")
        download_url = payload.get("downloadUrl")
        sha256 = payload.get("sha256")
        if (
            not isinstance(version, str)
            or not _version_parts(version)
            or not isinstance(download_url, str)
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in sha256)
        ):
            raise MaintenanceValidationError("update manifest is invalid")
        _require_https(download_url, "update download URL")
        available = _version_parts(version) > _version_parts(self._current_version)
        result: dict[str, object] = {
            "status": "available" if available else "current",
            "currentVersion": self._current_version,
        }
        if available:
            result.update(
                {
                    "version": version,
                    "downloadUrl": download_url,
                    "sha256": sha256.casefold(),
                }
            )
        return result


class MaintenanceService:
    def __init__(
        self,
        *,
        library_db: Path,
        backup_dir: Path,
        state_dir: Path,
        clock: Clock | None = None,
        retention: int = 3,
    ) -> None:
        if retention < 1:
            raise ValueError("backup retention must be positive")
        self._library_db = Path(library_db).resolve()
        self._backup_dir = Path(backup_dir).resolve()
        self._state_dir = Path(state_dir).resolve()
        self._clock = clock or _utc_stamp
        self._retention = retention

    def create_backup(self) -> dict[str, object]:
        if not self._library_db.is_file():
            raise MaintenanceNotFound("library database not found")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _safe_stamp(self._clock())
        backup_id = f"{_BACKUP_PREFIX}{stamp}-{uuid.uuid4().hex[:12]}{_BACKUP_SUFFIX}"
        destination = self._backup_path(backup_id)
        _sqlite_backup(self._library_db, destination)
        if _quick_check(destination) != "ok":
            destination.unlink(missing_ok=True)
            raise MaintenanceError("backup integrity check failed")
        self._prune_backups()
        return self._backup_metadata(destination)

    def list_backups(self) -> dict[str, object]:
        if not self._backup_dir.is_dir():
            return {"items": []}
        backups = sorted(
            (
                path
                for path in self._backup_dir.glob(
                    f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"
                )
                if path.is_file()
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
        return {"items": [self._backup_metadata(path) for path in backups]}

    def plan_restore(
        self,
        backup_id: str,
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        if confirmed is not True:
            raise MaintenanceValidationError("restore confirmation is required")
        backup = self._existing_backup(backup_id)
        if _quick_check(backup) != "ok":
            raise MaintenanceValidationError("backup integrity check failed")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        pending = self._state_dir / _PENDING_RESTORE
        temporary = self._state_dir / f".{_PENDING_RESTORE}.tmp"
        temporary.write_text(
            json.dumps({"backupId": backup.name}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, pending)
        return {"scheduled": True, "backupId": backup.name}

    def apply_pending_restore(self) -> bool:
        pending = self._state_dir / _PENDING_RESTORE
        if not pending.is_file():
            return False
        try:
            raw = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MaintenanceValidationError("restore plan is invalid") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("backupId"), str):
            raise MaintenanceValidationError("restore plan is invalid")
        backup = self._existing_backup(raw["backupId"])
        if _quick_check(backup) != "ok":
            raise MaintenanceValidationError("backup integrity check failed")

        self._library_db.parent.mkdir(parents=True, exist_ok=True)
        rollback = self._state_dir / ".pre-restore.sqlite"
        if self._library_db.is_file():
            _sqlite_backup(self._library_db, rollback)
        try:
            _sqlite_backup(backup, self._library_db)
            if _quick_check(self._library_db) != "ok":
                raise MaintenanceError("restored database integrity check failed")
        except BaseException:
            if rollback.is_file():
                _sqlite_backup(rollback, self._library_db)
            raise
        finally:
            rollback.unlink(missing_ok=True)
        pending.unlink()
        return True

    def diagnostic_zip(self) -> bytes:
        if not self._library_db.is_file():
            raise MaintenanceNotFound("library database not found")
        uri = f"{self._library_db.as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            schema_version = current_schema_version(connection)
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            job_status_counts = _grouped_counts(
                connection,
                table="background_jobs",
                column="status",
            )
            job_error_counts = _grouped_counts(
                connection,
                table="background_jobs",
                column="error_code",
                exclude_null=True,
            )
            record_counts = {
                table: _table_count(connection, table)
                for table in (
                    "source_apps",
                    "source_accounts",
                    "conversations",
                    "messages",
                    "assets",
                    "retrieval_chunks",
                    "graph_nodes",
                    "graph_edges",
                    "agent_threads",
                    "agent_turns",
                    "background_jobs",
                )
            }
        report = {
            "appVersion": APP_VERSION,
            "schemaVersion": schema_version,
            "quickCheck": quick_check,
            "jobStatusCounts": job_status_counts,
            "jobErrorCodeCounts": job_error_counts,
            "recordCounts": record_counts,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "diagnostic.json",
                json.dumps(report, ensure_ascii=False, separators=(",", ":")),
            )
        return buffer.getvalue()

    def _prune_backups(self) -> None:
        items = self.list_backups()["items"]
        if not isinstance(items, list):
            return
        for item in items[self._retention :]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            self._existing_backup(item["id"]).unlink()

    def _backup_metadata(self, path: Path) -> dict[str, object]:
        path = path.resolve()
        path.relative_to(self._backup_dir)
        stat = path.stat()
        return {
            "id": path.name,
            "createdAt": datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z"),
            "sizeBytes": stat.st_size,
            "quickCheck": _quick_check(path),
        }

    def _existing_backup(self, backup_id: str) -> Path:
        if (
            not isinstance(backup_id, str)
            or not backup_id.startswith(_BACKUP_PREFIX)
            or not backup_id.endswith(_BACKUP_SUFFIX)
            or Path(backup_id).name != backup_id
        ):
            raise MaintenanceNotFound("backup not found")
        path = self._backup_path(backup_id)
        if not path.is_file():
            raise MaintenanceNotFound("backup not found")
        return path

    def _backup_path(self, backup_id: str) -> Path:
        path = (self._backup_dir / backup_id).resolve()
        path.relative_to(self._backup_dir)
        return path


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def _quick_check(path: Path) -> str:
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError:
        return "failed"
    return str(row[0]) if row is not None else "failed"


def _grouped_counts(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
    exclude_null: bool = False,
) -> dict[str, int]:
    if not _table_exists(connection, table):
        return {}
    where = f"WHERE {column} IS NOT NULL" if exclude_null else ""
    rows = connection.execute(
        f"SELECT {column}, COUNT(*) FROM {table} {where} GROUP BY {column}"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _safe_stamp(value: str) -> str:
    if not isinstance(value, str):
        raise MaintenanceError("backup clock is invalid")
    normalized = "".join(character for character in value if character.isalnum())
    if not normalized or len(normalized) > 40:
        raise MaintenanceError("backup clock is invalid")
    return normalized


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fetch_manifest(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"Chatbox/{APP_VERSION}",
        },
    )
    with urlopen(request, timeout=5) as response:
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > 128_000:
            raise MaintenanceValidationError("update manifest is too large")
        payload = response.read(128_001)
    if len(payload) > 128_000:
        raise MaintenanceValidationError("update manifest is too large")
    return payload


def _require_https(value: str, field: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise MaintenanceValidationError(f"{field} must use HTTPS")


def _version_parts(value: str) -> tuple[int, int, int, int, int]:
    import re

    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)(?:[-.]?(?:beta|b)\.?([0-9]+))?",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return ()
    major, minor, patch, beta = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        0 if beta is not None else 1,
        int(beta or 0),
    )
