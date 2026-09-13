from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from ..config import Settings
from ..wechat_db import (
    ImportResult,
    SourceDatabase,
    SourceSchemaError,
    discover_sources,
    import_sources,
)


ImportErrorCode = Literal[
    "SOURCE_NOT_FOUND",
    "SOURCE_SCHEMA_UNSUPPORTED",
]
DiscoverSources = Callable[[Path], list[SourceDatabase]]
ImportSources = Callable[[list[Path], Path], ImportResult]
AfterImport = Callable[[], object]
SourceRefresher = Callable[[], object]

_ERROR_MESSAGES: dict[ImportErrorCode, str] = {
    "SOURCE_NOT_FOUND": "未找到可读取的微信数据库。",
    "SOURCE_SCHEMA_UNSUPPORTED": (
        "检测到 SQLite 数据库，但其微信数据结构不受支持。"
    ),
}
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


class ImportServiceError(RuntimeError):
    def __init__(self, code: ImportErrorCode) -> None:
        self.code = code
        self.message = _ERROR_MESSAGES[code]
        self.retryable = False
        self.details: dict[str, object] = {}
        super().__init__(self.message)


class ImportService:
    def __init__(
        self,
        settings: Settings,
        *,
        discoverer: DiscoverSources = discover_sources,
        importer: ImportSources = import_sources,
        source_refresher: SourceRefresher | None = None,
        after_import: AfterImport | None = None,
    ) -> None:
        self._settings = settings
        self._discoverer = discoverer
        self._importer = importer
        self._uses_default_importer = importer is import_sources
        self._source_refresher = source_refresher
        self._after_import = after_import

    def run(self) -> ImportResult:
        if self._source_refresher is not None:
            self._source_refresher()
        candidates = _database_candidates(self._settings.source_dir)
        if not candidates:
            raise ImportServiceError("SOURCE_NOT_FOUND")

        try:
            sources = self._discoverer(self._settings.source_dir)
        except (OSError, sqlite3.DatabaseError, SourceSchemaError) as exc:
            raise ImportServiceError("SOURCE_SCHEMA_UNSUPPORTED") from exc
        if not sources:
            raise ImportServiceError("SOURCE_SCHEMA_UNSUPPORTED")

        try:
            source_paths = [source.path for source in sources]
            if self._uses_default_importer:
                result = import_sources(
                    source_paths,
                    self._settings.library_db,
                    media_root=self._settings.wechat_media_root,
                    activity_filter_enabled=self._settings.activity_filter_enabled,
                    activity_filter_inactive_days=self._settings.activity_filter_inactive_days,
                )
            else:
                result = self._importer(
                    source_paths,
                    self._settings.library_db,
                )
        except (sqlite3.DatabaseError, SourceSchemaError) as exc:
            raise ImportServiceError("SOURCE_SCHEMA_UNSUPPORTED") from exc
        if self._after_import is not None:
            try:
                self._after_import()
            except Exception:
                pass
        return result


def _database_candidates(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        return []
    try:
        return [
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.casefold() in _SQLITE_SUFFIXES
        ]
    except OSError:
        return []
