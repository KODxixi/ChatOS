from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from fixtures.synthetic_sqlite import create_synthetic_sources, sha256_file
from wechat_ai.config import Settings


def load_import_service_module():
    try:
        return importlib.import_module("wechat_ai.services.import_service")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "wechat_ai.services.import_service has not been implemented"
        ) from exc


def make_settings(root: Path, source_dir: Path) -> Settings:
    return Settings(
        project_root=root,
        source_dir=source_dir,
        library_db=root / "library" / "wechat-ai.sqlite",
        output_dir=root / "output",
        web_dist=root / "web" / "dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


def test_service_imports_synthetic_sources_repeatably_without_changing_hashes(
    tmp_path: Path,
) -> None:
    import_service = load_import_service_module()
    fixtures = create_synthetic_sources(tmp_path / "sources")
    settings = make_settings(tmp_path, fixtures.root)
    hashes_before = {
        path.name: sha256_file(path)
        for path in fixtures.database_paths
    }

    service = import_service.ImportService(settings)
    first = service.run()
    second = service.run()

    assert first.discovered_sources == 3
    assert first.normalized_conversations == 2
    assert first.inserted_messages == 5
    assert second.discovered_sources == 3
    assert second.normalized_conversations == 2
    assert second.inserted_messages == 0
    assert {
        path.name: sha256_file(path)
        for path in fixtures.database_paths
    } == hashes_before


def test_semantic_enrichment_failure_never_changes_successful_import_result(
    tmp_path: Path,
) -> None:
    import_service = load_import_service_module()
    fixtures = create_synthetic_sources(tmp_path / "sources")
    settings = make_settings(tmp_path, fixtures.root)
    calls: list[str] = []

    def fail_after_import() -> None:
        calls.append("called")
        raise RuntimeError("synthetic semantic failure")

    result = import_service.ImportService(
        settings,
        after_import=fail_after_import,
    ).run()

    assert result.inserted_messages == 5
    assert calls == ["called"]


def test_semantic_enrichment_is_not_scheduled_after_failed_import(
    tmp_path: Path,
) -> None:
    import_service = load_import_service_module()
    fixtures = create_synthetic_sources(tmp_path / "sources")
    settings = make_settings(tmp_path, fixtures.root)
    calls: list[str] = []

    def fail_importer(
        _sources: list[Path],
        _library_db: Path,
    ):
        raise sqlite3.DatabaseError("synthetic import failure")

    with pytest.raises(import_service.ImportServiceError):
        import_service.ImportService(
            settings,
            importer=fail_importer,
            after_import=lambda: calls.append("called"),
        ).run()

    assert calls == []


@pytest.mark.parametrize("create_directory", [False, True])
def test_service_reports_source_not_found_without_leaking_path(
    tmp_path: Path,
    create_directory: bool,
) -> None:
    import_service = load_import_service_module()
    source_dir = tmp_path / "private-source"
    if create_directory:
        source_dir.mkdir()
    settings = make_settings(tmp_path, source_dir)

    with pytest.raises(import_service.ImportServiceError) as captured:
        import_service.ImportService(settings).run()

    error = captured.value
    assert error.code == "SOURCE_NOT_FOUND"
    assert error.message == "未找到可读取的微信数据库。"
    assert error.retryable is False
    assert error.details == {}
    assert str(source_dir) not in str(error)


def test_service_reports_unsupported_when_all_sqlite_schemas_are_unknown(
    tmp_path: Path,
) -> None:
    import_service = load_import_service_module()
    source_dir = tmp_path / "private-source"
    source_dir.mkdir()
    unsupported_database = source_dir / "unknown.sqlite"
    connection = sqlite3.connect(unsupported_database)
    try:
        connection.execute(
            "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, secret TEXT)"
        )
        connection.execute(
            "INSERT INTO unrelated(secret) VALUES ('must-not-leak')"
        )
        connection.commit()
    finally:
        connection.close()
    settings = make_settings(tmp_path, source_dir)

    with pytest.raises(import_service.ImportServiceError) as captured:
        import_service.ImportService(settings).run()

    error = captured.value
    assert error.code == "SOURCE_SCHEMA_UNSUPPORTED"
    assert error.message == "检测到 SQLite 数据库，但其微信数据结构不受支持。"
    assert error.retryable is False
    assert error.details == {}
    assert "unknown.sqlite" not in str(error)
    assert "unrelated" not in str(error)
    assert "must-not-leak" not in str(error)
