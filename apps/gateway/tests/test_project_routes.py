from __future__ import annotations

import sqlite3
import time as _time
from collections import Counter
from pathlib import Path

from wechat_ai.app import create_app
from wechat_ai.config import Settings


def test_project_routes_have_one_owner_per_method(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web",
        lm_base_url="http://127.0.0.1:9123/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    routes = Counter(
        (method, owner.include_context.prefix + route.path)
        for owner in app.routes
        if hasattr(owner, "original_router")
        for route in owner.original_router.routes
        for method in getattr(route, "methods", ())
        if method in {"GET", "POST"}
    )
    assert routes["GET", "/api/projects"] == 1
    assert routes["POST", "/api/projects"] == 1
    assert routes["GET", "/api/conversations/projects"] == 1


def test_upsert_project_row_writes_project_and_pulse(tmp_path: Path) -> None:
    """P3-1: gchat_service.upsert_project_row 幂等落 projects + project_pulses。"""
    from wechat_ai.database import initialize_database
    from wechat_ai.services.gchat_service import upsert_project_row

    conn = initialize_database(tmp_path / "lib.sqlite")
    upsert_project_row(conn, "proj-abc", "武汉HYP", now_ms=1, summary="1 conv, 10 msgs")
    upsert_project_row(conn, "proj-abc", "武汉HYP", now_ms=2, summary="1 conv, 10 msgs")  # idempotent
    try:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM project_pulses").fetchone()[0] == 1
        row = conn.execute("SELECT name, status FROM projects WHERE id='proj-abc'").fetchone()
        assert row[0] == "武汉HYP"
        assert row[1] == "active"
    finally:
        conn.close()


def test_organize_pipeline_writes_project_rows(tmp_path: Path) -> None:
    """P3-1: organize 项目阶段此前只写 Markdown；现在应同时落 SQLite 两表。"""
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.operations_service import BackgroundJobService
    from wechat_ai.services.incremental_organize import run_incremental_organize

    db_path = tmp_path / "lib.sqlite"
    initialize_database(db_path).close()
    cf = create_connection_factory(db_path)
    jobs = BackgroundJobService(
        cf,
        clock=lambda: "2026-10-13T00:00:00Z",
        id_factory=lambda: "job-proj-1",
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO conversations(id, source_conversation_id, title, kind, message_count, is_indexing_enabled, domain)"
            " VALUES ('c1','c1','武汉HYP造型群','group',20,1,'project')"
        )
        for i in range(5):
            conn.execute(
                "INSERT INTO messages(id, conversation_id, sender_name, sent_at, type, content, dedupe_key)"
                " VALUES (?,?,?,?,'text',?,?)",
                (100 + i, "c1", "张三", int(_time.time() * 1000) - i * 1000,
                 f"尽快把武汉HYP造型扁带平段方案发给甲方确认{i}", f"dk-{i}"),
            )
        conn.commit()

    job = jobs.create_job("incremental_organize", trigger_source="test", payload={})
    result = run_incremental_organize(cf, jobs, job["id"], vault_root=tmp_path)

    with sqlite3.connect(db_path) as conn:
        p = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        pp = conn.execute("SELECT COUNT(*) FROM project_pulses").fetchone()[0]
    assert p >= 1
    assert pp >= 1
