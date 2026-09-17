"""Incremental organize pipeline tests (ChatOS v0.4.0).

Verifies:
- Idempotency: running twice with no new messages produces 0 new tasks.
- Protected tasks: completed/ignored tasks are never overwritten or revived.
- Watermark advancement: new messages after watermark produce exactly one task.
- Deterministic identity: conversation_id + source_message_id.
- WeChat raw tables are never written.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from wechat_ai.services.operations_service import BackgroundJobService


def _make_db(tmp_path: Path) -> tuple[Path, object]:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory

    path = tmp_path / "library.sqlite"
    initialize_database(path).close()
    return path, create_connection_factory(path)


def _seed_conversation(
    connection: sqlite3.Connection,
    *,
    conv_id: str = "conv-test-1",
    title: str = "测试项目群",
    kind: str = "group",
    messages: list[dict] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO conversations(id, source_conversation_id, title, kind, is_indexing_enabled) VALUES (?, ?, ?, ?, 1)",
        (conv_id, conv_id, title, kind),
    )
    if messages:
        for i, m in enumerate(messages):
            connection.execute(
                """
                INSERT INTO messages(id, conversation_id, sender_name, sent_at, type, content, dedupe_key)
                VALUES (?, ?, ?, ?, 'text', ?, ?)
                """,
                (
                    m.get("id", 1000 + i),
                    conv_id,
                    m.get("sender", "张三"),
                    m.get("sent_at", int(time.time() * 1000) - i * 60000),
                    m["content"],
                    f"dedup-{conv_id}-{i}",
                ),
            )
    connection.commit()


def _run_organize(
    connection_factory: object,
    jobs_service: BackgroundJobService,
    job_id: str,
    *,
    vault_root: Path,
) -> object:
    from wechat_ai.services.incremental_organize import run_incremental_organize

    # Ensure the job exists in the database (pipeline calls transition/set_result).
    try:
        jobs_service.get_job(job_id)
    except Exception:
        jobs_service.create_job(
            "incremental_organize",
            trigger_source="test",
            payload={"cause": f"test-{job_id}"},
        )

    return run_incremental_organize(
        connection_factory,
        jobs_service,
        job_id,
        vault_root=vault_root,
    )


def _make_jobs_service(connection_factory: object) -> BackgroundJobService:
    from wechat_ai.services.operations_service import BackgroundJobService as _BJS

    return _BJS(
        connection_factory,
        clock=lambda: "2026-09-17T00:00:00Z",
        id_factory=lambda: "job-test-1",
    )


class TestIncrementalOrganize:
    def test_first_run_creates_tasks_and_watermark(
        self, tmp_path: Path
    ) -> None:
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿给甲方", "id": 1001},
                    {"content": "今天天气不错", "id": 1002},
                    {"content": "请尽快整理会议纪要并发给我", "id": 1003},
                ],
            )

        result = _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        assert result.status in ("completed", "degraded")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            tasks = conn.execute("SELECT * FROM tasks ORDER BY sent_at").fetchall()
            assert len(tasks) >= 1  # at least the actionable messages
            # Watermark should be set
            wm = conn.execute(
                "SELECT * FROM conversation_watermarks WHERE conversation_id = 'conv-test-1'"
            ).fetchone()
            assert wm is not None
            assert wm["last_message_id"] == 1001  # latest message (highest sent_at)

    def test_second_run_no_new_messages_produces_zero_new_tasks(
        self, tmp_path: Path
    ) -> None:
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿", "id": 2001},
                    {"content": "请尽快整理会议纪要", "id": 2002},
                ],
            )

        # First run
        _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        # Second run with same job id (simulate new job)
        jobs2 = BackgroundJobService(
            cf,
            clock=lambda: "2026-09-17T01:00:00Z",
            id_factory=lambda: "job-test-2",
        )
        result2 = _run_organize(cf, jobs2, "job-test-2", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            count_after = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        assert count_after == count_before  # no duplicates

    def test_completed_tasks_are_preserved(
        self, tmp_path: Path
    ) -> None:
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿", "id": 3001},
                ],
            )

        # First run
        _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            task = conn.execute("SELECT * FROM tasks LIMIT 1").fetchone()
            assert task is not None
            # Mark as completed (simulating user action)
            conn.execute(
                "UPDATE tasks SET status = 'completed', updated_at = ? WHERE task_id = ?",
                (int(time.time() * 1000), task["task_id"]),
            )
            conn.commit()

        # Second run
        jobs2 = BackgroundJobService(
            cf,
            clock=lambda: "2026-09-17T02:00:00Z",
            id_factory=lambda: "job-test-2",
        )
        _run_organize(cf, jobs2, "job-test-2", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            after = conn.execute(
                "SELECT status FROM tasks WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
            assert after["status"] == "completed"  # preserved, not revived

    def test_new_message_after_watermark_creates_one_task(
        self, tmp_path: Path
    ) -> None:
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿给甲方", "id": 4001, "sent_at": 1700000000000},
                ],
            )

        # First run
        _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            # Add a new message after watermark
            conn.execute(
                """
                INSERT INTO messages(id, conversation_id, sender_name, sent_at, type, content, dedupe_key)
                VALUES (4002, 'conv-test-1', '李四', 1800000000000, 'text', '下周三之前完成施工图修改', 'dedup-new-1')
                """
            )
            conn.commit()

        # Second run
        jobs2 = BackgroundJobService(
            cf,
            clock=lambda: "2026-09-17T03:00:00Z",
            id_factory=lambda: "job-test-2",
        )
        _run_organize(cf, jobs2, "job-test-2", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            count_after = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            wm = conn.execute(
                "SELECT last_message_id FROM conversation_watermarks WHERE conversation_id = 'conv-test-1'"
            ).fetchone()

        assert count_after == count_before + 1  # exactly one new task
        assert wm[0] == 4002  # watermark advanced

    def test_wechat_raw_tables_are_readonly(
        self, tmp_path: Path
    ) -> None:
        """Verify the pipeline never writes to conversations/messages."""
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿给甲方", "id": 5001},
                ],
            )
            conv_count_before = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            msg_count_before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        with sqlite3.connect(db_path) as conn:
            conv_count_after = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            msg_count_after = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        assert conv_count_after == conv_count_before
        assert msg_count_after == msg_count_before

    def test_job_result_contains_real_stats(
        self, tmp_path: Path
    ) -> None:
        db_path, cf = _make_db(tmp_path)
        jobs = _make_jobs_service(cf)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _seed_conversation(
                conn,
                messages=[
                    {"content": "明天下午三点前提交方案设计稿给甲方", "id": 6001},
                    {"content": "请尽快整理会议纪要并发给我", "id": 6002},
                ],
            )

        _run_organize(cf, jobs, "job-test-1", vault_root=tmp_path)

        job = jobs.get_job("job-test-1")
        assert "result" in job
        result = job["result"]
        assert isinstance(result, dict)
        assert "conversationsScanned" in result
        assert "newTasks" in result
        assert "watermarksAdvanced" in result
        assert result["watermarksAdvanced"] >= 1
