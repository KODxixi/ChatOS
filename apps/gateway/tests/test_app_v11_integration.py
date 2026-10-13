from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from fixtures.synthetic_sqlite import create_synthetic_sources


def _settings(tmp_path: Path):
    from wechat_ai.config import Settings

    return Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "data" / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


def test_create_app_registers_v11_knowledge_agent_and_model_routes(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    assert app.title == "Chatbox"
    routes = app.openapi()["paths"]

    assert "get" in routes["/api/sources"]
    assert "get" in routes["/api/knowledge/facets"]
    assert "get" in routes["/api/knowledge/spaces"]
    assert "get" in routes["/api/knowledge/items"]
    assert "get" in routes["/api/assets/{asset_id}"]
    assert "get" in routes["/api/graph"]
    assert "get" in routes["/api/graph/edges/{edge_id}/evidence"]
    assert "get" in routes["/api/projects"]
    assert "get" in routes["/api/projects/{project_id}/pulse"]
    assert "get" in routes["/api/gchat/memories"]
    assert "get" in routes["/api/gchat/decisions"]
    assert "post" in routes["/api/agent/stream"]
    assert "get" in routes["/api/agent/threads"]
    assert "post" in routes["/api/agent/threads"]
    assert "patch" in routes["/api/agent/threads/{thread_id}"]
    assert "delete" in routes["/api/agent/threads/{thread_id}"]
    assert "get" in routes["/api/model-profiles"]
    assert "post" in routes["/api/model-profiles"]
    assert "patch" in routes["/api/model-profiles/{profile_id}"]
    assert "delete" in routes["/api/model-profiles/{profile_id}"]
    assert "post" in routes["/api/model-profiles/{profile_id}/test"]
    assert "post" in routes["/api/model-profiles/presets/deepseek-v4-flash"]
    assert "get" in routes["/api/setup/status"]
    assert "post" in routes["/api/setup/wechat-keys/import"]
    assert "post" in routes["/api/setup/wechat-keys/auto"]
    assert "delete" in routes["/api/setup/wechat-keys/{key_ref}"]
    assert "post" in routes["/api/import/jobs"]
    assert "get" in routes["/api/jobs"]
    assert "post" in routes["/api/jobs/{job_id}/cancel"]
    assert "get" in routes["/api/settings/sync"]
    assert "patch" in routes["/api/settings/sync"]
    assert "get" in routes["/api/backups"]
    assert "post" in routes["/api/backups"]
    assert "post" in routes["/api/restore-plan"]
    assert "post" in routes["/api/diagnostics"]
    assert "get" in routes["/api/updates/check"]


def test_create_app_keeps_legacy_search_and_ask_routes_for_compatibility(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    paths = app.openapi()["paths"]

    assert "/api/search" in paths
    assert "/api/ask/stream" in paths


def test_create_app_seeds_local_profile_and_recovers_interrupted_agent_run(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.database import initialize_database

    settings = _settings(tmp_path)
    with closing(initialize_database(settings.library_db)) as connection:
        connection.execute(
            """
            INSERT INTO model_profiles(
                id, name, provider, base_url, model, auth_kind,
                is_default, last_status, created_at, updated_at
            ) VALUES (
                'local-default', '本地 LM Studio', 'lm-studio',
                'http://127.0.0.1:1234/v1', NULL, 'none',
                1, 'unknown', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent_threads(
                id, title, default_scope_json,
                is_archived, created_at, updated_at
            ) VALUES ('thread-orphan', 'Interrupted', '{"mode":"global"}', 0, 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO agent_turns(
                id, thread_id, role, content, status,
                model_profile_id, scope_json, created_at
            ) VALUES (
                'assistant-orphan', 'thread-orphan', 'assistant', 'partial',
                'streaming', 'local-default', '{"mode":"global"}', 1
            )
            """
        )
        connection.commit()

    create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    with closing(sqlite3.connect(settings.library_db)) as connection:
        assert connection.execute(
            """
            SELECT base_url, is_default
            FROM model_profiles
            WHERE id = 'local-default'
            """
        ).fetchone() == ("http://127.0.0.1:1234/v1", 1)
        assert connection.execute(
            """
            SELECT content, status, error_code
            FROM agent_turns
            WHERE id = 'assistant-orphan'
            """
        ).fetchone() == ("", "interrupted", "APP_RESTARTED")


def test_create_app_seeds_deepseek_profile_for_a_new_database(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app

    settings = _settings(tmp_path)
    create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    with closing(sqlite3.connect(settings.library_db)) as connection:
        assert connection.execute(
            "SELECT id, provider, model, is_default FROM model_profiles"
        ).fetchall() == [
            ("deepseek-v4-flash", "openai-compatible", "deepseek-v4-flash", 0)
        ]


def test_app_startup_schedules_semantic_recovery_without_blocking_pages(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app

    started = threading.Event()
    release = threading.Event()

    def enrich() -> None:
        started.set()
        release.wait(timeout=2)

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
        semantic_enricher=enrich,
    )

    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            assert started.wait(timeout=1)
            assert client.get("/api/health").status_code == 200
    finally:
        release.set()


def test_semantic_recovery_does_not_reenter_while_startup_run_is_active(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app

    create_synthetic_sources(tmp_path / "decrypted")
    started = threading.Event()
    release = threading.Event()
    restarted = threading.Event()
    calls: list[str] = []

    def enrich() -> None:
        calls.append("called")
        if len(calls) == 2:
            restarted.set()
        started.set()
        release.wait(timeout=2)

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "ready",
        lm_probe=lambda: "offline",
        semantic_enricher=enrich,
    )

    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            assert started.wait(timeout=1)
            assert client.get("/api/health").status_code == 200
            assert client.post("/api/import").status_code == 200
            assert calls == ["called"]
            release.set()
            assert restarted.wait(timeout=1)
            assert calls == ["called", "called"]
    finally:
        release.set()
