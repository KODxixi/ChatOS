from __future__ import annotations

import json
import os
from pathlib import Path


class SetupError(RuntimeError):
    code = "SETUP_ERROR"


class SetupValidationError(SetupError, ValueError):
    code = "SETUP_REQUEST_INVALID"


class SetupService:
    def __init__(self, *, project_root: Path) -> None:
        self._project_root = Path(project_root).resolve()

    def configure_wechat_source(self, database_root: str) -> dict[str, object]:
        if not isinstance(database_root, str) or not database_root.strip():
            raise SetupValidationError("请选择微信数据库目录。")
        root = Path(database_root.strip())
        if not root.is_absolute() or not root.is_dir():
            raise SetupValidationError("微信数据库目录不可用。")
        resolved = root.resolve()
        if not _contains_supported_database(resolved):
            raise SetupValidationError("目录中没有可识别的微信数据库。")
        destination = self._project_root / "config.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            json.dumps(
                {"db_dir": str(resolved)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return {"detected": True, "restartRequired": False}

    def wechat_source_detected(self) -> bool:
        try:
            raw = json.loads(
                (self._project_root / "config.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not isinstance(raw.get("db_dir"), str):
            return False
        root = Path(raw["db_dir"])
        if not root.is_absolute():
            root = self._project_root / root
        return root.is_dir() and _contains_supported_database(root.resolve())


def _contains_supported_database(root: Path) -> bool:
    candidates = (
        root / "session" / "session.db",
        root / "contact" / "contact.db",
        root / "message" / "message_resource.db",
        root / "hardlink" / "hardlink.db",
    )
    if any(path.is_file() for path in candidates):
        return True
    message_dir = root / "message"
    if not message_dir.is_dir():
        return False
    return any(
        path.is_file()
        for pattern in ("message_*.db", "biz_message_*.db", "media_*.db")
        for path in message_dir.glob(pattern)
    )
