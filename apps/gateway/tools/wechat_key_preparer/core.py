from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from types import ModuleType


_HEX_32 = re.compile(r"[0-9a-f]{64}")
_HEX_16 = re.compile(r"[0-9a-f]{32}")
_RESULT_NAME = "all_keys.json"
_STATUS_NAME = "status.json"


class KeyPreparationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def prepare_keys(result_dir: Path, *, engine: ModuleType | object | None = None) -> int:
    """Extract verified keys and write one Chatbox-compatible result file."""

    output_dir = result_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / _RESULT_NAME
    status_path = output_dir / _STATUS_NAME
    _safe_unlink(result_path)
    _write_json_atomic(
        status_path,
        {"state": "running", "entryCount": 0, "errorCode": None},
    )
    extractor = engine or _load_engine()
    entries: dict[str, dict[str, str | float]] = {}
    original_print = extractor._print
    original_save_results = extractor._save_results

    def capture_results(
        db_files: list,
        _salt_to_dbs: dict,
        key_map: dict,
        _db_dir: str,
        _out_file: str,
    ) -> None:
        for relative, _path, size, salt, _page in db_files:
            key = key_map.get(salt)
            normalized = str(relative).replace("\\", "/")
            if not isinstance(key, str) or not _HEX_32.fullmatch(key.lower()):
                continue
            if not isinstance(salt, str) or not _HEX_16.fullmatch(salt.lower()):
                continue
            entries[normalized] = {
                "enc_key": key.lower(),
                "salt": salt.lower(),
                "size_mb": round(int(size) / 1024 / 1024, 1),
            }
        if not entries:
            raise KeyPreparationError("KEY_NOT_FOUND")

    try:
        database_root = extractor.auto_detect_db_dir()
        if not database_root:
            raise KeyPreparationError("DATABASE_NOT_FOUND")
        extractor._print = lambda *_args, **_kwargs: None
        extractor._save_results = capture_results
        extractor._scan_memory_raw_key(database_root, "unused.json")
        if not entries:
            raise KeyPreparationError("KEY_NOT_FOUND")
        _write_json_atomic(result_path, entries)
        _write_json_atomic(
            status_path,
            {
                "state": "ready",
                "entryCount": len(entries),
                "errorCode": None,
            },
        )
        return len(entries)
    except KeyPreparationError as exc:
        _safe_unlink(result_path)
        _write_error_status(status_path, exc.code)
        raise
    except Exception as exc:
        _safe_unlink(result_path)
        code = _error_code(exc)
        _write_error_status(status_path, code)
        raise KeyPreparationError(code) from exc
    finally:
        extractor._print = original_print
        extractor._save_results = original_save_results


def default_result_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    current = root / "Chatbox"
    legacy = root / "Gchat"
    product_root = current if current.exists() or not legacy.exists() else legacy
    return product_root / "data" / "key-preparer"


def _load_engine() -> ModuleType:
    from ._upstream import wcdb_key_tool_windows

    return wcdb_key_tool_windows


def _error_code(exc: Exception) -> str:
    message = str(exc).casefold()
    if "未运行" in message or "not running" in message:
        return "WECHAT_NOT_RUNNING"
    if "管理员" in message or "access" in message or "permission" in message:
        return "ACCESS_DENIED"
    if "数据库目录" in message or "database" in message and "directory" in message:
        return "DATABASE_NOT_FOUND"
    if "未能从进程" in message or "key" in message:
        return "KEY_NOT_FOUND"
    return "PREPARATION_FAILED"


def _write_error_status(path: Path, code: str) -> None:
    _write_json_atomic(
        path,
        {"state": "error", "entryCount": 0, "errorCode": code},
    )


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        _safe_unlink(temporary)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
