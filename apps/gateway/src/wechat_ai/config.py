from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import get_project_root, resolve_project_path

DEFAULT_EMBEDDING_MODEL = "text-embedding-qwen3-embedding-0.6b"
DEFAULT_EMBEDDING_DIMENSIONS = 1024


def validate_embedding_config(model: str, dimensions: int) -> None:
    if (model, dimensions) != (DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMENSIONS):
        raise ValueError("ChatOS requires text-embedding-qwen3-embedding-0.6b / 1024; old embedding settings are not supported")


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    source_dir: Path
    library_db: Path
    output_dir: Path
    lm_base_url: str
    retrieval_base_url: str = "http://127.0.0.1:9123/v1"
    wechat_media_root: Path | None = None
    rerank_enabled: bool = True
    activity_filter_enabled: bool = True
    activity_filter_inactive_days: int = 30
    config_path: Path | None = None
    config_identity: str | None = None
    # indexing (message embedding pipeline)
    indexing_model: str = DEFAULT_EMBEDDING_MODEL
    indexing_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
    indexing_batch_size: int = 1024
    indexing_context_length: int = 2048
    indexing_min_text_length: int = 4
    indexing_filler_filter_enabled: bool = True
    indexing_filler_patterns: list[str] | None = None
    indexing_group_filter_enabled: bool = True
    indexing_group_size_threshold: int = 30
    indexing_noise_group_keywords: list[str] | None = None
    indexing_sync_enabled: bool = False
    indexing_sync_interval_minutes: int = 15

    def __post_init__(self) -> None:
        validate_embedding_config(self.indexing_model, self.indexing_dimensions)

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

    indexing = raw.get("indexing", {})
    if not isinstance(indexing, dict):
        raise ValueError("indexing must be a JSON object")

    def _str_list(value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return value
        return fallback

    return Settings(
        project_root=root,
        source_dir=resolve_project_path(root, raw.get("sourceDir", "decrypted")),
        library_db=resolve_project_path(
            root,
            raw.get("libraryDb", "data/library/wechat-ai.sqlite"),
        ),
        output_dir=resolve_project_path(root, raw.get("outputDir", "output")),
        config_path=path.resolve(),
        config_identity=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        lm_base_url=str(
            # 2026-09-22：默认值改为 llama-swap 9123（甲-1 迁移后 8080 已停用）。
            # 未配 settings.json 的 lmStudio.baseUrl 时指向 llama-swap 唯一入口。
            lm_studio.get("baseUrl", "http://127.0.0.1:9123/v1")
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
        indexing_model=str(os.environ.get("LOCAL_EMBEDDING_MODEL") or indexing.get("model", DEFAULT_EMBEDDING_MODEL)),
        indexing_dimensions=int(os.environ.get("LOCAL_EMBEDDING_DIMENSIONS") or indexing.get("dimensions", DEFAULT_EMBEDDING_DIMENSIONS)),
        indexing_batch_size=int(indexing.get("batchSize", 1024)),
        indexing_context_length=int(indexing.get("contextLength", 2048)),
        indexing_min_text_length=int(indexing.get("minTextLength", 4)),
        indexing_filler_filter_enabled=bool(indexing.get("fillerFilterEnabled", True)),
        indexing_filler_patterns=_str_list(indexing.get("fillerPatterns"), None),
        indexing_group_filter_enabled=bool(indexing.get("groupFilterEnabled", True)),
        indexing_group_size_threshold=int(indexing.get("groupSizeThreshold", 30)),
        indexing_noise_group_keywords=_str_list(indexing.get("noiseGroupKeywords"), None),
        indexing_sync_enabled=bool(indexing.get("syncEnabled", False)),
        indexing_sync_interval_minutes=int(indexing.get("syncIntervalMinutes", 15)),
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
