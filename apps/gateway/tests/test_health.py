from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _settings(tmp_path: Path, *, web_dist: Path | None = None):
    from wechat_ai.config import Settings

    return Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "source",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=web_dist or tmp_path / "web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


@pytest.mark.parametrize(
    ("source_status", "library_status", "lm_status"),
    [
        ("missing", "ready", "offline"),
        ("unsupported", "empty", "ready"),
        ("ready", "empty", "ready"),
    ],
)
def test_health_reports_app_source_library_and_lm_independently(
    tmp_path: Path,
    source_status: str,
    library_status: str,
    lm_status: str,
) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        web_dist=tmp_path / "missing-dist",
        source_probe=lambda: source_status,
        library_probe=lambda: library_status,
        lm_probe=lambda: lm_status,
        initialize_database=lambda _path: None,
    )

    response = TestClient(
        app,
        base_url="http://127.0.0.1",
    ).get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert {
        "app": payload["app"],
        "source": payload["source"],
        "library": payload["library"],
        "lm": payload["lm"],
    } == {
        "app": "ready",
        "source": source_status,
        "library": library_status,
        "lm": lm_status,
    }
    assert payload["tasks"]["status"] == "disabled"
    assert payload["defaultModel"]["status"] == "offline"


def test_health_defaults_library_status_to_database_contents(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings
    from wechat_ai.database import initialize_database

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "missing-source",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "ready",
        lm_probe=lambda: "offline",
    )
    client = TestClient(app, base_url="http://127.0.0.1")

    assert client.get("/api/health").json()["library"] == "empty"

    connection = initialize_database(settings.library_db)
    try:
        connection.execute(
            "INSERT INTO conversations "
            "(id, source_conversation_id, title, kind) "
            "VALUES (?, ?, ?, ?)",
            ("conversation-1", "source-1", "Synthetic", "direct"),
        )
        connection.commit()
    finally:
        connection.close()

    assert client.get("/api/health").json()["library"] == "ready"


def test_app_health_reports_v13_subsystems_without_private_details(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "missing-source",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    payload = TestClient(
        create_app(
            settings,
            source_probe=lambda: "missing",
            lm_probe=lambda: "offline",
        ),
        base_url="http://127.0.0.1",
    ).get("/api/health").json()

    assert set(
        (
            "tasks",
            "retrieval",
            "media",
            "deterministicGraph",
            "semanticGraph",
            "defaultModel",
            "observability",
        )
    ).issubset(payload)
    assert all(
        payload[name]["status"]
        in {"ready", "degraded", "offline", "disabled", "indexing"}
        for name in (
            "tasks",
            "retrieval",
            "media",
            "deterministicGraph",
            "semanticGraph",
            "defaultModel",
            "observability",
        )
    )
    assert str(tmp_path) not in str(payload)


def test_media_health_reports_pending_resolved_media_as_indexing(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import _media_health
    from wechat_ai.database import initialize_database

    database_path = tmp_path / "data" / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind, message_count
            ) VALUES ('conversation-1', 'conversation-1', 'Media', 'direct', 1)
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name, sent_at, type, content
            ) VALUES ('conversation-1', 'media-1', 'Alice', 1, 'image', '')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(message_id, ordinal, kind, status)
            VALUES (?, 0, 'image', 'resolved')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO assets(
                part_id, ordinal, kind, status, mime_type, local_path, sha256
            ) VALUES (?, 0, 'image', 'resolved', 'image/png', 'image.png', 'asset-hash')
            """,
            (part.lastrowid,),
        )
        connection.commit()

        health = _media_health(connection)
    finally:
        connection.close()

    assert health["status"] == "indexing"
    assert health["detail"] == "pending=1"


def test_media_health_reports_pending_resolved_documents_as_indexing(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import _media_health
    from wechat_ai.database import initialize_database

    database_path = tmp_path / "data" / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind, message_count
            ) VALUES ('conversation-1', 'conversation-1', 'Files', 'direct', 1)
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name, sent_at, type, content
            ) VALUES ('conversation-1', 'file-1', 'Alice', 1, 'file', '')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(message_id, ordinal, kind, status)
            VALUES (?, 0, 'file', 'resolved')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO assets(
                part_id, ordinal, kind, status, file_name, mime_type,
                local_path, sha256
            ) VALUES (?, 0, 'file', 'resolved', 'brief.pdf', 'application/pdf',
                      'brief.pdf', 'file-hash')
            """,
            (part.lastrowid,),
        )
        connection.commit()

        health = _media_health(connection)
    finally:
        connection.close()

    assert health["status"] == "indexing"
    assert health["detail"] == "pending=1"


def test_missing_web_build_returns_explicit_placeholder(tmp_path: Path) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path, web_dist=tmp_path / "missing-dist"),
        web_dist=tmp_path / "missing-dist",
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
        initialize_database=lambda _path: None,
    )

    response = TestClient(
        app,
        base_url="http://127.0.0.1",
    ).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "WebUI 尚未构建" in response.text


def test_existing_web_build_serves_spa_index(tmp_path: Path) -> None:
    from wechat_ai.app import create_app

    web_dist = tmp_path / "web" / "dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text(
        "<html><body>compiled-webui</body></html>",
        encoding="utf-8",
    )
    app = create_app(
        _settings(tmp_path, web_dist=web_dist),
        web_dist=web_dist,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
        initialize_database=lambda _path: None,
    )

    response = TestClient(
        app,
        base_url="http://127.0.0.1",
    ).get("/conversations")

    assert response.status_code == 200
    assert "compiled-webui" in response.text


def test_source_probe_uses_schema_discovery_after_sqlite_is_present(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import _probe_source

    source_dir = tmp_path / "decrypted"
    source_dir.mkdir()
    discovery_calls: list[Path] = []

    def discover(path: Path) -> list[object]:
        discovery_calls.append(path)
        return [object()]

    assert _probe_source(source_dir, discover_sources=discover) == "missing"
    assert discovery_calls == []

    (source_dir / "source.sqlite").touch()
    assert _probe_source(source_dir, discover_sources=discover) == "ready"
    assert discovery_calls == [source_dir]

    assert (
        _probe_source(source_dir, discover_sources=lambda _path: [])
        == "unsupported"
    )


def test_source_probe_treats_schema_probe_failure_as_unsupported(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import _probe_source

    source_dir = tmp_path / "decrypted"
    source_dir.mkdir()
    (source_dir / "source.db").touch()

    def fail_discovery(_path: Path) -> list[object]:
        raise OSError("synthetic schema failure")

    assert (
        _probe_source(source_dir, discover_sources=fail_discovery)
        == "unsupported"
    )


def test_create_app_registers_import_route(tmp_path: Path) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "missing-source",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/api/health")
    response = client.post("/api/import")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SOURCE_NOT_FOUND"


def test_create_app_registers_export_route(tmp_path: Path) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "missing-source",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/api/health")
    response = client.post(
        "/api/export",
        json={"conversationId": "missing", "format": "markdown"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def _insert_job(
    connection,
    job_id: str,
    job_type: str,
    status: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO background_jobs(
            id, type, status, stage, trigger_source, payload_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, 'stage', 'manual', '{}', ?, ?)
        """,
        (job_id, job_type, status, created_at, created_at),
    )


def _tasks_status(db_path: Path) -> str:
    from wechat_ai.database import initialize_database
    from wechat_ai.plugins.health import probe_operational_health

    initialize_database(db_path)

    class _Profiles:
        def list_profiles(self) -> list[object]:
            return []

    class _Ledger:
        def health(self) -> dict[str, object]:
            return {"status": "ready"}

    return probe_operational_health(
        db_path, _Profiles(), _Ledger()
    )["tasks"]["status"]


def test_tasks_status_ignores_degraded_job_superseded_by_later_completed(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    db = tmp_path / "library.sqlite"
    connection = initialize_database(db)
    try:
        _insert_job(
            connection, "sem-1", "semantic_graph", "degraded",
            "2026-09-04T00:00:00Z",
        )
        _insert_job(
            connection, "sem-2", "semantic_graph", "completed",
            "2026-09-04T01:00:00Z",
        )
        connection.commit()
    finally:
        connection.close()

    assert _tasks_status(db) == "ready"


def test_tasks_status_reports_indexing_when_running_supersedes_older_degraded(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    db = tmp_path / "library.sqlite"
    connection = initialize_database(db)
    try:
        _insert_job(
            connection, "sem-1", "semantic_graph", "degraded",
            "2026-09-04T00:00:00Z",
        )
        _insert_job(
            connection, "sem-2", "semantic_graph", "running",
            "2026-09-04T01:00:00Z",
        )
        connection.commit()
    finally:
        connection.close()

    assert _tasks_status(db) == "indexing"


def test_tasks_status_still_surfaces_a_genuinely_degraded_latest_job(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    db = tmp_path / "library.sqlite"
    connection = initialize_database(db)
    try:
        _insert_job(
            connection, "sem-1", "semantic_graph", "running",
            "2026-09-04T01:00:00Z",
        )
        _insert_job(
            connection, "med-1", "media", "degraded",
            "2026-09-04T02:00:00Z",
        )
        connection.commit()
    finally:
        connection.close()

    assert _tasks_status(db) == "degraded"


def test_tasks_status_reports_ready_for_quiet_terminal_jobs(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    db = tmp_path / "library.sqlite"
    connection = initialize_database(db)
    try:
        _insert_job(
            connection, "sem-1", "semantic_graph", "completed",
            "2026-09-04T00:00:00Z",
        )
        _insert_job(
            connection, "med-1", "media", "interrupted",
            "2026-09-04T01:00:00Z",
        )
        connection.commit()
    finally:
        connection.close()

    assert _tasks_status(db) == "ready"
