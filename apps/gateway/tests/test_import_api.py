from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fixtures.synthetic_sqlite import create_synthetic_sources, sha256_file
from wechat_ai.config import Settings
from wechat_ai.services.import_service import ImportService, ImportServiceError


def load_import_api_module():
    try:
        return importlib.import_module("wechat_ai.api.import_data")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "wechat_ai.api.import_data has not been implemented"
        ) from exc


def make_client(import_runner) -> TestClient:
    import_api = load_import_api_module()
    app = FastAPI()
    app.include_router(
        import_api.create_import_router(import_runner=import_runner),
        prefix="/api",
    )
    return TestClient(app)


def make_settings(root: Path, source_dir: Path) -> Settings:
    return Settings(
        project_root=root,
        source_dir=source_dir,
        library_db=root / "library" / "wechat-ai.sqlite",
        output_dir=root / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


def test_import_endpoint_returns_camel_case_counts_and_repeat_zero(
    tmp_path: Path,
) -> None:
    fixtures = create_synthetic_sources(tmp_path / "sources")
    service = ImportService(make_settings(tmp_path, fixtures.root))
    hashes_before = {
        path.name: sha256_file(path)
        for path in fixtures.database_paths
    }
    client = make_client(service.run)

    first = client.post("/api/import")
    second = client.post("/api/import")

    assert first.status_code == 200
    assert first.json() == {
        "discoveredSources": 3,
        "normalizedConversations": 2,
        "insertedMessages": 5,
    }
    assert second.status_code == 200
    assert second.json() == {
        "discoveredSources": 3,
        "normalizedConversations": 2,
        "insertedMessages": 0,
    }
    assert {
        path.name: sha256_file(path)
        for path in fixtures.database_paths
    } == hashes_before


def test_import_endpoint_returns_stable_source_not_found_error() -> None:
    def fail_import():
        raise ImportServiceError("SOURCE_NOT_FOUND")

    response = make_client(fail_import).post("/api/import")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "SOURCE_NOT_FOUND",
            "message": "未找到可读取的微信数据库。",
            "retryable": False,
            "details": {},
        }
    }


def test_import_endpoint_returns_sanitized_schema_unsupported_error() -> None:
    secret = (
        r"C:\private\unknown.sqlite SELECT * FROM messages "
        "secret-message-body"
    )

    def fail_import():
        try:
            raise RuntimeError(secret)
        except RuntimeError as cause:
            raise ImportServiceError("SOURCE_SCHEMA_UNSUPPORTED") from cause

    response = make_client(fail_import).post("/api/import")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "SOURCE_SCHEMA_UNSUPPORTED",
            "message": "检测到 SQLite 数据库，但其微信数据结构不受支持。",
            "retryable": False,
            "details": {},
        }
    }
    assert secret not in response.text
    assert "Traceback" not in response.text
