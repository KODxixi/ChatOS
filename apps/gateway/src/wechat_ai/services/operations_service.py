from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ..credential_store import CredentialStore
from .conversation_service import ConnectionFactory


Clock = Callable[[], str]
IdFactory = Callable[[], str]
KeyVerifier = Callable[[str, bytes, bytes], bool]
BooleanProbe = Callable[[], bool]
StatusProbe = Callable[[], Mapping[str, Any]]

JOB_TYPES = frozenset(
    {
        "import",
        "retrieval",
        "deterministic_graph",
        "semantic_graph",
        "media",
        "gchat_insights",
        "backup",
    }
)
JOB_STATUSES = frozenset(
    {
        "queued",
        "running",
        "retrying",
        "completed",
        "degraded",
        "failed",
        "cancelled",
        "interrupted",
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "degraded", "failed", "cancelled", "interrupted"}
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_FORBIDDEN_PAYLOAD_KEY_PARTS = ("body", "key", "path", "secret", "credential")
_REBUILDABLE_JOB_TYPES = frozenset(JOB_TYPES - {"import"})


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    type: str
    payload: dict[str, Any]


class OperationsError(RuntimeError):
    code = "OPERATIONS_ERROR"


class OperationsValidationError(OperationsError, ValueError):
    code = "OPERATIONS_REQUEST_INVALID"


class OperationsNotFound(OperationsError, LookupError):
    code = "OPERATIONS_NOT_FOUND"


class OperationsConflict(OperationsError):
    code = "OPERATIONS_CONFLICT"


class BackgroundJobService:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: f"job-{uuid.uuid4().hex}")

    @contextmanager
    def _open(self) -> Iterable[sqlite3.Connection]:
        connection = self._connection_factory()
        connection.row_factory = sqlite3.Row
        # 共享工厂（conversation_service.create_connection_factory）已统一配置
        # busy_timeout=30000；这里再补一道兜底，确保本服务短连接在工厂配置缺失时
        # 仍有足够等待窗口，避免 import/media worker 分批持锁期间 BEGIN IMMEDIATE
        # 5s 抢锁失败抛 "database is locked"。（注：wechat_db.py 走 initialize_database
        # 自建连接，并无 sqlite timeout=20 参数，原注释指向不实，已更正。）
        connection.execute("PRAGMA busy_timeout = 20000")
        try:
            yield connection
        finally:
            connection.close()

    def create_job(
        self,
        job_type: str,
        *,
        trigger_source: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if job_type not in JOB_TYPES:
            raise OperationsValidationError("invalid background job type")
        trigger_source = _required_text(trigger_source, "trigger source", 100)
        normalized_payload = dict(payload or {})
        _validate_safe_job_payload(normalized_payload)
        payload_json = json.dumps(
            normalized_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        now = self._clock()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM background_jobs
                WHERE type = ? AND payload_json = ?
                  AND status IN ('queued', 'running', 'retrying')
                ORDER BY created_at ASC, id ASC LIMIT 1
                """,
                (job_type, payload_json),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _job_from_row(existing)
            job_id = _required_text(self._id_factory(), "job id", 200)
            connection.execute(
                """
                INSERT INTO background_jobs(
                    id, type, status, stage, progress_current, progress_total,
                    trigger_source, error_code, retry_count, payload_json,
                    created_at, started_at, finished_at, updated_at,
                    cancel_requested_at
                ) VALUES (?, ?, 'queued', 'queued', 0, NULL, ?, NULL, 0,
                          ?, ?, NULL, NULL, ?, NULL)
                """,
                (
                    job_id,
                    job_type,
                    trigger_source,
                    payload_json,
                    now,
                    now,
                ),
            )
            connection.commit()
            return self._job(connection, job_id)

    def recover_interrupted(self) -> dict[str, int]:
        now = self._clock()
        interrupted = 0
        requeued = 0
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM background_jobs
                WHERE status = 'running'
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'interrupted', stage = 'interrupted',
                        error_code = 'JOB_INTERRUPTED', finished_at = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, now, row["id"]),
                )
                interrupted += 1
            for row in rows:
                if str(row["type"]) not in _REBUILDABLE_JOB_TYPES:
                    continue
                existing = connection.execute(
                    """
                    SELECT 1 FROM background_jobs
                    WHERE type = ? AND payload_json = ?
                      AND status IN ('queued', 'running', 'retrying')
                    LIMIT 1
                    """,
                    (row["type"], row["payload_json"]),
                ).fetchone()
                if existing is not None:
                    continue
                job_id = _required_text(self._id_factory(), "job id", 200)
                connection.execute(
                    """
                    INSERT INTO background_jobs(
                        id, type, status, stage, progress_current,
                        progress_total, trigger_source, error_code,
                        retry_count, payload_json, created_at, started_at,
                        finished_at, updated_at, cancel_requested_at
                    ) VALUES (?, ?, 'queued', 'queued', 0, NULL, 'recovery',
                              NULL, 0, ?, ?, NULL, NULL, ?, NULL)
                    """,
                    (job_id, row["type"], row["payload_json"], now, now),
                )
                requeued += 1
            connection.commit()
        return {"interrupted": interrupted, "requeued": requeued}

    def claim_next(self) -> ClaimedJob | None:
        now = self._clock()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM background_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, id ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status = 'running', stage = 'running',
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            self.fail(str(row["id"]), error_code="JOB_PAYLOAD_INVALID")
            raise OperationsValidationError("background job payload is invalid") from exc
        if not isinstance(payload, dict):
            self.fail(str(row["id"]), error_code="JOB_PAYLOAD_INVALID")
            raise OperationsValidationError("background job payload is invalid")
        return ClaimedJob(
            id=str(row["id"]),
            type=str(row["type"]),
            payload=payload,
        )

    def should_cancel(self, job_id: str) -> bool:
        with self._open() as connection:
            row = self._job_row(connection, job_id)
            return (
                str(row["status"]) == "cancelled"
                or row["cancel_requested_at"] is not None
            )

    def complete(self, job_id: str) -> dict[str, Any]:
        return self._finish(job_id, status="completed", error_code=None)

    def degrade(
        self,
        job_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return self._finish(
            job_id,
            status="degraded",
            error_code=_required_text(error_code, "error code", 200),
        )

    def fail(
        self,
        job_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return self._finish(
            job_id,
            status="failed",
            error_code=_required_text(error_code, "error code", 200),
        )

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        now = self._clock()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._job_row(connection, job_id)
            current_status = str(row["status"])
            if current_status in TERMINAL_JOB_STATUSES:
                connection.commit()
                return _job_from_row(row)
            if current_status not in {"running", "retrying"}:
                connection.rollback()
                raise OperationsConflict("background job is not running")
            final_status = (
                "cancelled"
                if row["cancel_requested_at"] is not None
                else status
            )
            final_error = None if final_status == "cancelled" else error_code
            connection.execute(
                """
                UPDATE background_jobs
                SET status = ?, stage = ?, error_code = ?, finished_at = ?,
                    updated_at = ?, progress_current = CASE
                        WHEN ? = 'completed' AND progress_total IS NOT NULL
                        THEN progress_total ELSE progress_current END
                WHERE id = ?
                """,
                (
                    final_status,
                    final_status,
                    final_error,
                    now,
                    now,
                    final_status,
                    job_id,
                ),
            )
            connection.commit()
            return self._job(connection, job_id)

    def list_jobs(self) -> dict[str, Any]:
        with self._open() as connection:
            rows = connection.execute(
                """
                SELECT * FROM background_jobs
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
            items = [_job_from_row(row) for row in rows]
            return {"items": items, "total": len(items)}

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._open() as connection:
            return self._job(connection, job_id)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        now = self._clock()
        with self._open() as connection:
            row = self._job_row(connection, job_id)
            status = str(row["status"])
            if status in TERMINAL_JOB_STATUSES:
                if status == "cancelled":
                    return _job_from_row(row)
                raise OperationsConflict("background job is already finished")
            if status in {"queued", "retrying"}:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'cancelled', stage = 'cancelled',
                        cancel_requested_at = ?, finished_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, now, job_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, job_id),
                )
            connection.commit()
            return self._job(connection, job_id)

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        stage: str,
        progress_current: int | None = None,
        progress_total: int | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        if status not in JOB_STATUSES:
            raise OperationsValidationError("invalid background job status")
        stage = _required_text(stage, "background job stage", 200)
        if progress_current is not None and (
            isinstance(progress_current, bool) or progress_current < 0
        ):
            raise OperationsValidationError("invalid current progress")
        if progress_total is not None and (
            isinstance(progress_total, bool) or progress_total < 0
        ):
            raise OperationsValidationError("invalid total progress")
        now = self._clock()
        with self._open() as connection:
            row = self._job_row(connection, job_id)
            current_status = str(row["status"])
            if current_status in TERMINAL_JOB_STATUSES:
                raise OperationsConflict("background job is already finished")
            started_at = now if status == "running" and row["started_at"] is None else row["started_at"]
            finished_at = now if status in TERMINAL_JOB_STATUSES else None
            retry_count = int(row["retry_count"]) + (status == "retrying")
            connection.execute(
                """
                UPDATE background_jobs
                SET status = ?, stage = ?,
                    progress_current = COALESCE(?, progress_current),
                    progress_total = COALESCE(?, progress_total),
                    error_code = ?, retry_count = ?, started_at = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    stage,
                    progress_current,
                    progress_total,
                    error_code,
                    retry_count,
                    started_at,
                    finished_at,
                    now,
                    job_id,
                ),
            )
            connection.commit()
            return self._job(connection, job_id)

    def _job(self, connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
        return _job_from_row(self._job_row(connection, job_id))

    @staticmethod
    def _job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM background_jobs WHERE id = ?",
            (_required_text(job_id, "job id", 200),),
        ).fetchone()
        if row is None:
            raise OperationsNotFound("background job not found")
        return row


class SyncSettingsService:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _open(self) -> Iterable[sqlite3.Connection]:
        connection = self._connection_factory()
        try:
            yield connection
        finally:
            connection.close()

    def get(self) -> dict[str, Any]:
        with self._open() as connection:
            values = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    """
                    SELECT key, value FROM app_meta
                    WHERE key IN ('sync_enabled', 'sync_interval_minutes')
                    """
                )
            }
        return {
            "enabled": values.get("sync_enabled", "0") == "1",
            "intervalMinutes": int(values.get("sync_interval_minutes", "15")),
        }

    def patch(
        self,
        *,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
    ) -> dict[str, Any]:
        if enabled is not None and not isinstance(enabled, bool):
            raise OperationsValidationError("enabled must be a boolean")
        if interval_minutes is not None and (
            isinstance(interval_minutes, bool)
            or not isinstance(interval_minutes, int)
            or not 5 <= interval_minutes <= 1440
        ):
            raise OperationsValidationError(
                "intervalMinutes must be between 5 and 1440"
            )
        updates: list[tuple[str, str]] = []
        if enabled is not None:
            updates.append(("sync_enabled", "1" if enabled else "0"))
        if interval_minutes is not None:
            updates.append(("sync_interval_minutes", str(interval_minutes)))
        if updates:
            with self._open() as connection:
                connection.executemany(
                    """
                    INSERT INTO app_meta(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    updates,
                )
                connection.commit()
        return self.get()


class WechatSourceKeyService:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        credential_store: CredentialStore,
        verifier: KeyVerifier,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        data_root_probe: BooleanProbe | None = None,
        archive_root_probe: BooleanProbe | None = None,
        wechat_source_probe: BooleanProbe | None = None,
        default_model_probe: StatusProbe | None = None,
        first_import_probe: StatusProbe | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._credential_store = credential_store
        self._verifier = verifier
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: f"key-ref-{uuid.uuid4().hex}")
        self._data_root_probe = data_root_probe
        self._archive_root_probe = archive_root_probe
        self._wechat_source_probe = wechat_source_probe
        self._default_model_probe = default_model_probe
        self._first_import_probe = first_import_probe
        self._project_root = (
            Path(project_root).resolve() if project_root is not None else None
        )

    @contextmanager
    def _open(self) -> Iterable[sqlite3.Connection]:
        connection = self._connection_factory()
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def import_entries(
        self,
        entries: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not entries:
            raise OperationsValidationError("wechat key import is empty")
        normalized = [_normalize_key_entry(entry) for entry in entries]
        if len({item[0] for item in normalized}) != len(normalized):
            raise OperationsValidationError("wechat key import contains duplicates")
        for database_id, key, salt in normalized:
            if not self._verifier(database_id, key, salt):
                raise OperationsValidationError("wechat key validation failed")

        now = self._clock()
        result: list[dict[str, Any]] = []
        credential_rollbacks: list[tuple[str, str | None]] = []
        with self._open() as connection:
            try:
                for database_id, key, salt in normalized:
                    existing = connection.execute(
                        """
                        SELECT key_ref, credential_target
                        FROM wechat_source_key_refs WHERE database_id = ?
                        """,
                        (database_id,),
                    ).fetchone()
                    key_ref = (
                        str(existing["key_ref"])
                        if existing is not None
                        else _required_text(self._id_factory(), "key reference", 200)
                    )
                    credential_target = f"Gchat/WeChat/{database_id}"
                    previous_secret = self._credential_store.get(credential_target)
                    self._credential_store.set(credential_target, key.hex())
                    credential_rollbacks.append((credential_target, previous_secret))
                    salt_hash = hashlib.sha256(salt).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO wechat_source_key_refs(
                            key_ref, database_id, salt_hash, verified_at,
                            credential_target
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(database_id) DO UPDATE SET
                            salt_hash = excluded.salt_hash,
                            verified_at = excluded.verified_at,
                            credential_target = excluded.credential_target
                        """,
                        (
                            key_ref,
                            database_id,
                            salt_hash,
                            now,
                            credential_target,
                        ),
                    )
                    result.append(
                        {
                            "keyRef": key_ref,
                            "databaseId": database_id,
                            "verified": True,
                            "verifiedAt": now,
                            "saltHash": salt_hash,
                        }
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                for target, previous_secret in reversed(credential_rollbacks):
                    if previous_secret is None:
                        self._credential_store.delete(target)
                    else:
                        self._credential_store.set(target, previous_secret)
                raise
        return {"items": result}

    def auto_match(self) -> dict[str, Any]:
        """Discover and verify the supported local key file without exposing it."""
        if self._project_root is None:
            return _empty_key_match_result()

        for candidate, source in _wechat_key_file_candidates(self._project_root):
            try:
                entries = parse_wechat_key_file(
                    candidate.read_bytes()
                )
            except (OSError, OperationsValidationError):
                continue

            valid_entries: list[Mapping[str, Any]] = []
            for entry in entries:
                try:
                    database_id, key, salt = _normalize_key_entry(entry)
                except OperationsValidationError:
                    continue
                if self._verifier(database_id, key, salt):
                    valid_entries.append(entry)
            if not valid_entries:
                continue

            imported = self.import_entries(valid_entries)
            return {
                "matched": True,
                "matchedCount": len(valid_entries),
                "source": source,
                "items": imported["items"],
            }

        return _empty_key_match_result()

    def status(self) -> dict[str, Any]:
        with self._open() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS configured,
                       COUNT(verified_at) AS verified
                FROM wechat_source_key_refs
                """
            ).fetchone()
        total = int(row["configured"])
        verified = int(row["verified"])
        data_root_configured = _safe_boolean_probe(self._data_root_probe)
        archive_root_configured = _safe_boolean_probe(self._archive_root_probe)
        wechat_source_detected = _safe_boolean_probe(self._wechat_source_probe)
        default_model = _safe_default_model_status(self._default_model_probe)
        first_import = _safe_first_import_status(self._first_import_probe)
        keys_ready = total > 0 and verified == total
        setup_complete = all(
            (
                data_root_configured,
                archive_root_configured,
                wechat_source_detected,
                keys_ready,
                first_import["completed"],
            )
        )
        return {
            "setupComplete": setup_complete,
            "dataRootConfigured": data_root_configured,
            "archiveRootConfigured": archive_root_configured,
            "wechatSource": {"detected": wechat_source_detected},
            "wechatKeys": {"total": total, "verified": verified},
            "defaultModel": default_model,
            "firstImport": first_import,
        }

    def delete(self, key_ref: str) -> None:
        key_ref = _required_text(key_ref, "key reference", 200)
        with self._open() as connection:
            row = connection.execute(
                """
                SELECT credential_target FROM wechat_source_key_refs
                WHERE key_ref = ?
                """,
                (key_ref,),
            ).fetchone()
            if row is None:
                raise OperationsNotFound("wechat key reference not found")
            self._credential_store.delete(str(row["credential_target"]))
            connection.execute(
                "DELETE FROM wechat_source_key_refs WHERE key_ref = ?",
                (key_ref,),
            )
            connection.commit()


def parse_wechat_key_file(content: bytes) -> list[dict[str, str]]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationsValidationError("wechat key file is invalid") from exc
    if not isinstance(payload, dict):
        raise OperationsValidationError("wechat key file is invalid")
    if "items" in payload:
        raw_items = payload["items"]
        if not isinstance(raw_items, list) or not all(
            isinstance(item, dict) for item in raw_items
        ):
            raise OperationsValidationError("wechat key file is invalid")
        return [dict(item) for item in raw_items]
    entries: list[dict[str, str]] = []
    for database_id, raw_entry in payload.items():
        if database_id == "_db_dir" and isinstance(raw_entry, str):
            continue
        if not isinstance(database_id, str) or not isinstance(raw_entry, dict):
            raise OperationsValidationError("wechat key file is invalid")
        entry = dict(raw_entry)
        entry["databaseId"] = database_id
        entries.append(entry)
    return entries


def _wechat_key_file_candidates(
    project_root: Path,
) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = [
        (project_root / "all_keys.json", "project")
    ]
    try:
        raw = json.loads(
            project_root.joinpath("config.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        raw = {}
    if isinstance(raw, dict) and isinstance(raw.get("db_dir"), str):
        database_root = Path(raw["db_dir"])
        if not database_root.is_absolute():
            database_root = project_root / database_root
        resolved_root = database_root.resolve()
        candidates.extend(
            (
                (resolved_root / "all_keys.json", "wechat-data"),
                (resolved_root.parent / "all_keys.json", "wechat-data"),
            )
        )
    unique: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for path, source in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((resolved, source))
    return unique


def _empty_key_match_result() -> dict[str, Any]:
    return {"matched": False, "matchedCount": 0, "items": []}


def create_project_wechat_key_verifier(project_root: str | Path) -> KeyVerifier:
    root = Path(project_root)

    def verify(database_id: str, key: bytes, salt: bytes) -> bool:
        try:
            raw = json.loads(
                root.joinpath("config.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not isinstance(raw.get("db_dir"), str):
            return False
        database_root = Path(raw["db_dir"])
        if not database_root.is_absolute():
            database_root = root / database_root
        return _source_key_verifier(database_root)(database_id, key, salt)

    return verify


def _source_key_verifier(database_root: Path) -> KeyVerifier:
    resolved_root = database_root.resolve()

    def verify(database_id: str, key: bytes, salt: bytes) -> bool:
        from ..wechat_decrypt import (
            DEFAULT_PAGE_SIZE,
            DEFAULT_RESERVE_SIZE,
            SQLITE_HEADER,
            WechatDecryptError,
            _decrypt_page,
        )

        try:
            normalized_id = _database_id(database_id)
            source = resolved_root.joinpath(*normalized_id.split("/")).resolve()
            source.relative_to(resolved_root)
            with source.open("rb") as stream:
                page = stream.read(DEFAULT_PAGE_SIZE)
            if len(page) != DEFAULT_PAGE_SIZE or page[:16] != salt:
                return False
            decrypted = _decrypt_page(
                page,
                page_number=1,
                key=key,
                reserve_size=DEFAULT_RESERVE_SIZE,
            )
        except (OSError, ValueError, WechatDecryptError):
            return False
        return (
            len(decrypted) == DEFAULT_PAGE_SIZE
            and decrypted.startswith(SQLITE_HEADER)
            and int.from_bytes(decrypted[16:18], "big") == DEFAULT_PAGE_SIZE
            and decrypted[18] in (1, 2)
            and decrypted[19] in (1, 2)
            and decrypted[20] == DEFAULT_RESERVE_SIZE
            and decrypted[21:24] == bytes((64, 32, 32))
        )

    return verify


def _safe_boolean_probe(probe: BooleanProbe | None) -> bool:
    if probe is None:
        return False
    try:
        return probe() is True
    except Exception:
        return False


def _safe_default_model_status(
    probe: StatusProbe | None,
) -> dict[str, Any]:
    fallback = {
        "profileId": None,
        "ready": False,
        "privacyConfirmed": False,
    }
    if probe is None:
        return fallback
    try:
        raw = probe()
    except Exception:
        return fallback
    if not isinstance(raw, Mapping):
        return fallback
    profile_id = raw.get("profileId")
    return {
        "profileId": (
            profile_id.strip()
            if isinstance(profile_id, str) and profile_id.strip()
            else None
        ),
        "ready": raw.get("ready") is True,
        "privacyConfirmed": raw.get("privacyConfirmed") is True,
    }


def _safe_first_import_status(
    probe: StatusProbe | None,
) -> dict[str, Any]:
    fallback = {"started": False, "completed": False, "jobId": None}
    if probe is None:
        return fallback
    try:
        raw = probe()
    except Exception:
        return fallback
    if not isinstance(raw, Mapping):
        return fallback
    job_id = raw.get("jobId")
    return {
        "started": raw.get("started") is True,
        "completed": raw.get("completed") is True,
        "jobId": (
            job_id.strip()
            if isinstance(job_id, str) and job_id.strip()
            else None
        ),
    }


def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "type": str(row["type"]),
        "status": str(row["status"]),
        "stage": str(row["stage"]),
        "progressCurrent": int(row["progress_current"]),
        "progressTotal": (
            int(row["progress_total"])
            if row["progress_total"] is not None
            else None
        ),
        "triggerSource": str(row["trigger_source"]),
        "errorCode": (
            str(row["error_code"]) if row["error_code"] is not None else None
        ),
        "retryCount": int(row["retry_count"]),
        "createdAt": str(row["created_at"]),
        "startedAt": (
            str(row["started_at"]) if row["started_at"] is not None else None
        ),
        "finishedAt": (
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
        "updatedAt": str(row["updated_at"]),
        "cancelRequestedAt": (
            str(row["cancel_requested_at"])
            if row["cancel_requested_at"] is not None
            else None
        ),
    }


def _normalize_key_entry(entry: Mapping[str, Any]) -> tuple[str, bytes, bytes]:
    raw_database_id = entry.get("databaseId")
    database_id = _database_id(raw_database_id)
    raw_key = entry.get("key", entry.get("encKey", entry.get("enc_key")))
    raw_salt = entry.get("salt")
    key = _hex_bytes(raw_key, field="wechat key", length=32)
    salt = _hex_bytes(raw_salt, field="wechat salt", length=16)
    return database_id, key, salt


def _database_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationsValidationError("databaseId is required")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith("//"):
        raise OperationsValidationError("databaseId is invalid")
    return path.as_posix()


def _hex_bytes(value: object, *, field: str, length: int) -> bytes:
    if not isinstance(value, str):
        raise OperationsValidationError(f"{field} is invalid")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise OperationsValidationError(f"{field} is invalid") from exc
    if len(decoded) != length:
        raise OperationsValidationError(f"{field} is invalid")
    return decoded


def _validate_safe_job_payload(payload: Mapping[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise OperationsValidationError("job payload keys must be strings")
                normalized_key = key.casefold().replace("-", "_")
                if any(part in normalized_key for part in _FORBIDDEN_PAYLOAD_KEY_PARTS):
                    raise OperationsValidationError("job payload contains private data")
                walk(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
            return
        if isinstance(value, str) and (
            _WINDOWS_ABSOLUTE_PATH.match(value)
            or value.startswith("/")
            or value.startswith("\\\\")
        ):
            raise OperationsValidationError("job payload contains a full path")

    walk(payload)
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise OperationsValidationError("job payload is not JSON serializable") from exc


def _required_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationsValidationError(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > limit:
        raise OperationsValidationError(f"{field} is too long")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
