from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import get_project_root, resolve_project_path


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    source_dir: Path
    library_db: Path
    output_dir: Path
    web_dist: Path
    lm_base_url: str
    retrieval_base_url: str = "http://127.0.0.1:9123/v1"
    wechat_media_root: Path | None = None
    rerank_enabled: bool = True
    activity_filter_enabled: bool = True
    activity_filter_inactive_days: int = 30

    @property
    def asset_dir(self) -> Path:
        return self.library_db.parent / "assets"

    @property
    def cache_dir(self) -> Path:
        return self.project_root / "data" / "cache"

    @property
    def backup_dir(self) -> Path:
        return self.project_root / "data" / "backups"


def load_settings(
    *,
    project_root: Path | None = None,
    settings_path: Path | None = None,
) -> Settings:
    root = (project_root or get_project_root()).resolve()
    _configure_media_environment(root)
    path = settings_path or root / "config" / "settings.json"
    raw = _read_settings(path)
    lm_studio = raw.get("lmStudio", {})
    if not isinstance(lm_studio, dict):
        raise ValueError("lmStudio must be a JSON object")
    llama_swap = raw.get("llamaSwap", {})
    if not isinstance(llama_swap, dict):
        raise ValueError("llamaSwap must be a JSON object")

    return Settings(
        project_root=root,
        source_dir=resolve_project_path(root, raw.get("sourceDir", "decrypted")),
        library_db=resolve_project_path(
            root,
            raw.get("libraryDb", "data/library/wechat-ai.sqlite"),
        ),
        output_dir=resolve_project_path(root, raw.get("outputDir", "output")),
        web_dist=resolve_project_path(root, raw.get("webDist", "web/dist")),
        lm_base_url=str(
            lm_studio.get("baseUrl", "http://127.0.0.1:1234/v1")
        ).rstrip("/"),
        retrieval_base_url=str(
            llama_swap.get(
                "retrievalBaseUrl", "http://127.0.0.1:9123/v1"
            )
        ).rstrip("/"),
        wechat_media_root=_read_wechat_media_root(root),
        rerank_enabled=bool(raw.get("rerankEnabled", True)),
        activity_filter_enabled=bool(
            raw.get("activityFilterEnabled", True)
        ),
        activity_filter_inactive_days=int(
            raw.get("activityFilterInactiveDays", 30)
        ),
    )


def _configure_media_environment(project_root: Path) -> None:
    """Use bundled media engines when the caller has not supplied overrides."""

    defaults = {
        "CHATBOX_WHISPER_CPP": project_root
        / "tools"
        / "whisper.cpp"
        / "bin"
        / "Release"
        / "whisper-cli.exe",
        "CHATBOX_WHISPER_MODEL": project_root
        / "tools"
        / "whisper.cpp"
        / "models"
        / "ggml-base.bin",
    }
    for variable, path in defaults.items():
        if not os.environ.get(variable) and path.is_file():
            os.environ[variable] = str(path)


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("settings file must contain a JSON object")
    return raw


def _read_wechat_media_root(project_root: Path) -> Path | None:
    source_config = project_root / "config.json"
    if not source_config.is_file():
        return None
    try:
        raw = json.loads(source_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("db_dir"), str):
        return None
    database_root = Path(raw["db_dir"])
    if not database_root.is_absolute():
        database_root = project_root / database_root
    return database_root.resolve().parent / "msg" / "attach"
