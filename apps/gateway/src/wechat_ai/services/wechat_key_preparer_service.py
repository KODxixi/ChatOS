from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from .operations_service import (
    OperationsConflict,
    OperationsValidationError,
    parse_wechat_key_file,
)


PreparerLauncher = Callable[[Path], None]
_RESULT_NAME = "all_keys.json"
_STATUS_NAME = "status.json"
_MAX_RESULT_BYTES = 1_000_000
_MAX_STATUS_BYTES = 64_000
_STATES = frozenset({"idle", "starting", "running", "ready", "error"})
_ERROR_CODES = frozenset(
    {
        "PREPARER_LAUNCH_FAILED",
        "PREPARER_RESULT_MISSING",
        "PREPARER_STATUS_INVALID",
        "WECHAT_NOT_RUNNING",
        "ACCESS_DENIED",
        "DATABASE_NOT_FOUND",
        "KEY_NOT_FOUND",
        "PREPARATION_FAILED",
    }
)


class KeyImportService(Protocol):
    def import_entries(
        self,
        entries: list[dict[str, str]],
    ) -> dict[str, Any]: ...


class WechatKeyPreparerService:
    """Coordinate a user-started preparer without reading WeChat processes."""

    def __init__(
        self,
        *,
        result_dir: Path,
        key_service: KeyImportService,
        launcher: PreparerLauncher,
    ) -> None:
        self._result_dir = result_dir.resolve()
        self._key_service = key_service
        self._launcher = launcher

    @property
    def _result_path(self) -> Path:
        return self._result_dir / _RESULT_NAME

    @property
    def _status_path(self) -> Path:
        return self._result_dir / _STATUS_NAME

    def start(self) -> dict[str, object]:
        self._result_dir.mkdir(parents=True, exist_ok=True)
        self._clear_one_time_files()
        starting = _public_status("starting")
        _write_json_atomic(self._status_path, starting)
        try:
            self._launcher(self._result_dir)
        except OSError:
            failed = _public_status("error", error_code="PREPARER_LAUNCH_FAILED")
            _write_json_atomic(self._status_path, failed)
            return failed
        return starting

    def status(self) -> dict[str, object]:
        if not self._status_path.is_file():
            return _public_status("idle")
        try:
            if self._status_path.stat().st_size > _MAX_STATUS_BYTES:
                raise ValueError("oversized status")
            raw = json.loads(self._status_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("invalid status")
            state = raw.get("state")
            count = raw.get("entryCount", 0)
            error_code = raw.get("errorCode")
            if (
                state not in _STATES
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                or not (error_code is None or error_code in _ERROR_CODES)
            ):
                raise ValueError("invalid status")
            if state == "ready" and not self._result_path.is_file():
                return _public_status(
                    "error",
                    error_code="PREPARER_RESULT_MISSING",
                )
            return _public_status(state, entry_count=count, error_code=error_code)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _public_status("error", error_code="PREPARER_STATUS_INVALID")

    def import_result(self) -> dict[str, Any]:
        if not self._result_path.is_file():
            raise OperationsConflict("key preparer result is not ready")
        try:
            if self._result_path.stat().st_size > _MAX_RESULT_BYTES:
                raise OperationsValidationError("wechat key import size is invalid")
            entries = parse_wechat_key_file(self._result_path.read_bytes())
            imported = self._key_service.import_entries(entries)
            return {
                "matched": True,
                "matchedCount": len(imported["items"]),
                "items": imported["items"],
            }
        finally:
            self._clear_one_time_files()

    def _clear_one_time_files(self) -> None:
        for path in (self._result_path, self._status_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def launch_wechat_key_preparer(result_dir: Path) -> None:
    """Open the standalone preparer with an explicit Windows elevation prompt."""

    if sys.platform != "win32":
        raise OSError("Windows is required")
    executable, arguments = _preparer_command(result_dir)
    if not executable.is_file():
        raise FileNotFoundError(executable)
    parameters = subprocess.list2cmdline([str(value) for value in arguments])
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        str(executable),
        parameters,
        str(executable.parent),
        1,
    )
    if result <= 32:
        raise OSError("preparer elevation was declined")


def _preparer_command(result_dir: Path) -> tuple[Path, list[str]]:
    if getattr(sys, "frozen", False):
        return (
            Path(sys.executable).resolve().with_name("chatbox-key-preparer.exe"),
            ["--result-dir", str(result_dir)],
        )
    repository_root = Path(__file__).resolve().parents[3]
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    return (
        pythonw if pythonw.is_file() else python,
        [
            str(repository_root / "tools" / "wechat_key_preparer" / "app.py"),
            "--result-dir",
            str(result_dir),
        ],
    )


def _public_status(
    state: str,
    *,
    entry_count: int = 0,
    error_code: str | None = None,
) -> dict[str, object]:
    return {
        "state": state,
        "entryCount": entry_count,
        "errorCode": error_code,
    }


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
