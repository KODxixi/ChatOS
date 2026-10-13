"""Production-path regressions using disposable SQLite/Vault data and no network."""
from __future__ import annotations

import asyncio
import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_round3_data_safety import snapshot
from wechat_ai.api.tasks import create_tasks_router
from wechat_ai.database import initialize_database
from wechat_ai.services import incremental_organize as organize
from wechat_ai.services import llm_service, project_extractor
from wechat_ai.services.conversation_service import create_connection_factory
from wechat_ai.services.job_worker import JobWorker
from wechat_ai.services.operations_service import BackgroundJobService


_RAW_TABLES = frozenset({"conversations", "messages", "message_parts", "assets"})


def _authorizer_deny_raw(action: int, arg: str | None, column: str | None,
                         database: str | None, trigger: str | None) -> int:
    import sqlite3 as sq
    # INSERT OR REPLACE is reported as SQLITE_INSERT, not a separate action.
    if action in (sq.SQLITE_INSERT, sq.SQLITE_UPDATE, sq.SQLITE_DELETE):
        if (arg or "").lower() in _RAW_TABLES:
            return sq.SQLITE_DENY
    return sq.SQLITE_OK



@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Windows creates a loopback socketpair for event-loop wakeups. Initialize
    # that internal pipe before blocking network; HTTP requests stay in-process.
    loop = asyncio.new_event_loop()

    def blocked(*args, **kwargs):
        pytest.fail("Network access is forbidden in round-2 regression tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)

    def unavailable(*, local_only=False):
        assert local_only, "Organize must only probe the local model"
        return False

    monkeypatch.setattr(llm_service, "is_available", unavailable)
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def library(tmp_path: Path):
    path = tmp_path / "library.sqlite"
    now = int(time.time() * 1000) - 10_000
    latest = {}
    # Import-only fixture setup; use the real migrations, never a hand-built schema.
    with closing(initialize_database(path)) as connection:
        for i in range(30):
            cid = f"conv_{i:03d}"
            latest[cid] = (now + i * 10 + 9, i * 10 + 10)
            connection.execute(
                "INSERT INTO conversations"
                "(id,source_conversation_id,title,kind,message_count,last_message_at,is_indexing_enabled) "
                "VALUES (?,?,?,'group',10,?,1)",
                (cid, cid, f"回归设计项目{i:03d}群", latest[cid][0]),
            )
            for j in range(10):
                connection.execute(
                    "INSERT INTO messages"
                    "(id,conversation_id,dedupe_key,sender_id,sender_name,sent_at,type,content) "
                    "VALUES (?,?,?,'synthetic-owner','杨杰峰',?,'text',?)",
                    (
                        i * 10 + j + 1, cid, f"{cid}-{j}", now + i * 10 + j,
                        "明天下午三点前提交方案设计稿给甲方" if j == 9 else "收到",
                    ),
                )
        # Artificial IDs and edited content ensure source-message dedup preserves
        # user choices even when the deterministic extracted task ID differs.
        for i, status in enumerate(("completed", "ignored")):
            connection.execute(
                "INSERT INTO tasks"
                "(task_id,conversation_id,content,sent_at,status,priority,due_date,"
                "source_message_id,created_at,updated_at) VALUES (?,?,?,?,?,'low','2099-01-01',?,11,12)",
                (f"manual-{status}", f"conv_{i:03d}", f"用户编辑的任务-{status}",
                 latest[f"conv_{i:03d}"][0], status, latest[f"conv_{i:03d}"][1]),
            )
        connection.commit()
        raw_before = snapshot(connection)
        protected_before = [
            tuple(row) for row in connection.execute("SELECT * FROM tasks ORDER BY task_id")
        ]

    real_connect = create_connection_factory(path)
    denied = []

    def connect():
        connection = real_connect()

        def authorize(*args):
            result = _authorizer_deny_raw(*args)
            if result == sqlite3.SQLITE_DENY:
                denied.append(args)
            return result

        connection.set_authorizer(authorize)
        return connection

    vault = tmp_path / "vault"
    jobs = BackgroundJobService(connect)
    worker = JobWorker(jobs, handlers={
        "incremental_organize": lambda job, context: organize.run_incremental_organize(
            connect, jobs, job.id, vault_root=vault,
        ),
    })
    state = SimpleNamespace(connect=connect, jobs=jobs, worker=worker, vault=vault, latest=latest)
    yield state
    # Check complete raw/user-edited rows, including after failed pipeline runs.
    with closing(connect()) as connection:
        assert snapshot(connection) == raw_before
        assert [
            tuple(row) for row in connection.execute(
                "SELECT * FROM tasks WHERE task_id IN ('manual-completed','manual-ignored') ORDER BY task_id"
            )
        ] == protected_before
    assert denied == []


def _run(library):
    job = library.jobs.create_job("incremental_organize", trigger_source="test")
    # Runs synchronously: no background thread, running gateway, or live service.
    assert library.worker.run_once()
    return library.jobs.get_job(job["id"])


def _checkpoint(library, processed, *, pending=True):
    expected = dict(list(library.latest.items())[:processed])
    with closing(library.connect()) as connection:
        watermarks = {
            row["conversation_id"]: (row["last_message_sent_at"], row["last_message_id"])
            for row in connection.execute("SELECT * FROM conversation_watermarks")
        }
        assert watermarks == expected
        assert {row[0] for row in connection.execute("SELECT conversation_id FROM organize_pending")} == (
            set(expected) if pending else set()
        )
        tasks = [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY task_id")]
        # The two protected tasks predate the first organize run.
        expected_tasks = expected | dict(list(library.latest.items())[:2])
        assert len(tasks) == len(expected_tasks)
        assert {row["conversation_id"]: row["source_message_id"] for row in tasks} == {
            cid: latest[1] for cid, latest in expected_tasks.items()
        }
        return tasks


def _interrupt_after_watermark(monkeypatch, after):
    original = organize._set_watermark
    seen = []

    def interrupt(connection, cid, sent_at, message_id):
        original(connection, cid, sent_at, message_id)
        seen.append(cid)
        if len(seen) == after:
            # This is deliberately after the real write, before its pending row.
            assert connection.in_transaction
            assert connection.execute(
                "SELECT 1 FROM organize_pending WHERE conversation_id=?", (cid,)
            ).fetchone() is None
            raise RuntimeError("injected interruption after watermark, before pending")

    monkeypatch.setattr(organize, "_set_watermark", interrupt)
    return seen


def _assert_briefs(library, count=30):
    briefs = list(library.vault.glob("conversations/project/*/project-brief.md"))
    assert len(briefs) == count
    for brief in briefs:
        text = brief.read_text(encoding="utf-8")
        assert "消息总数: 10" in text
        assert "明天下午三点前提交方案设计稿给甲方" in text


def test_organize_preserves_raw_and_user_modified_tasks(library):
    job = _run(library)
    assert (job["status"], job["errorCode"]) == ("degraded", "LLM_UNAVAILABLE")
    assert job["result"]["newTasks"] == 28
    assert job["result"]["duplicatesSkipped"] == 2
    assert job["result"]["protectedTasksPreserved"] >= 2
    assert job["result"]["projectsUpdated"] == 30
    tasks = _checkpoint(library, 30, pending=False)
    _assert_briefs(library)

    # An unchanged library must neither revive tasks nor regenerate briefs.
    briefs = {path: path.read_bytes() for path in library.vault.rglob("project-brief.md")}
    replay = _run(library)
    assert (replay["status"], replay["errorCode"]) == ("completed", None)
    assert replay["result"]["messagesScanned"] == 0
    assert replay["result"]["newTasks"] == 0
    assert replay["result"]["projectsUpdated"] == 0
    assert _checkpoint(library, 30, pending=False) == tasks
    assert {path: path.read_bytes() for path in library.vault.rglob("project-brief.md")} == briefs


def test_pending_and_watermark_same_transaction(library, monkeypatch):
    with monkeypatch.context() as fault:
        seen = _interrupt_after_watermark(fault, 1)
        job = _run(library)
    assert seen == ["conv_000"]
    assert (job["status"], job["errorCode"]) == ("failed", "ORGANIZE_PIPELINE_ERROR")
    _checkpoint(library, 0)
    _assert_briefs(library, 0)

    resumed = _run(library)
    assert (resumed["status"], resumed["errorCode"]) == ("degraded", "LLM_UNAVAILABLE")
    assert resumed["result"]["watermarksAdvanced"] == 30
    _checkpoint(library, 30, pending=False)
    _assert_briefs(library)


def test_batch_interrupt_no_lost_pending(library, monkeypatch):
    with monkeypatch.context() as fault:
        seen = _interrupt_after_watermark(fault, 26)
        job = _run(library)
    assert seen == list(library.latest)[:26]
    assert (job["status"], job["errorCode"]) == ("failed", "ORGANIZE_PIPELINE_ERROR")
    # Fresh connections see precisely the committed first batch; conversation 26
    # lost both its task and watermark, with no orphaned pending/projection state.
    _checkpoint(library, 25)
    _assert_briefs(library, 0)

    def fail_brief(vault_root, info):
        assert vault_root == library.vault
        raise OSError("injected brief write failure")

    with monkeypatch.context() as fault:
        fault.setattr(project_extractor, "_write_project_brief", fail_brief)
        resumed = _run(library)
    assert (resumed["status"], resumed["errorCode"]) == ("degraded", "PROJECT_WRITE_FAILED")
    assert resumed["result"]["conversationsScanned"] == 5
    assert resumed["result"]["newTasks"] == 5
    assert resumed["result"]["watermarksAdvanced"] == 5
    tasks = _checkpoint(library, 30)
    _assert_briefs(library, 0)

    # No inserts into messages between runs: all 30 must now come from pending.
    recovered = _run(library)
    assert (recovered["status"], recovered["errorCode"]) == ("completed", None)
    assert recovered["result"]["messagesScanned"] == 0
    assert recovered["result"]["newTasks"] == 0
    assert recovered["result"]["watermarksAdvanced"] == 0
    assert recovered["result"]["projectsUpdated"] == 30
    assert _checkpoint(library, 30, pending=False) == tasks
    _assert_briefs(library)


def test_taskpatch_rejects_invalid_status() -> None:
    from wechat_ai.api.tasks import TaskPatch
    from pydantic import ValidationError

    # Valid values accepted (round3 extended to pending/in_progress/draft).
    for s in ("todo", "completed", "ignored", "done", "pending", "in_progress", "draft"):
        m = TaskPatch(status=s)
        assert m.status == s
    for p in ("low", "medium", "high"):
        m = TaskPatch(priority=p)
        assert m.priority == p
    # Truly invalid values rejected.
    with pytest.raises(ValidationError):
        TaskPatch(status="urgent")
    with pytest.raises(ValidationError):
        TaskPatch(status="bogus")
    with pytest.raises(ValidationError):
        TaskPatch(priority="urgent")



def test_tasks_api_returns_source_message_id(library, offline):
    job = _run(library)
    assert job["errorCode"] == "LLM_UNAVAILABLE"
    app = FastAPI()
    app.include_router(create_tasks_router(library.connect), prefix="/api")
    with TestClient(app, backend_options={"loop_factory": lambda: offline}) as client:
        response = client.get("/api/tasks", params={"limit": 500})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 30
    assert len(payload["items"]) == 30
    assert {item["conversationId"]: item["sourceMessageId"] for item in payload["items"]} == {
        cid: latest[1] for cid, latest in library.latest.items()
    }
    assert all(type(item["sourceMessageId"]) is int for item in payload["items"])


def test_organize_honors_disabled_override(library):
    with closing(library.connect()) as connection:
        connection.execute(
            "INSERT INTO conversation_overrides(conversation_id,is_indexing_enabled,updated_at) "
            "VALUES ('conv_029',0,'test')"
        )
        connection.commit()
    job = _run(library)
    assert (job["status"], job["errorCode"]) == ("degraded", "LLM_UNAVAILABLE")
    assert job["result"]["conversationsScanned"] == 29
    assert job["result"]["newTasks"] == 27
    assert job["result"]["projectsUpdated"] == 29
    _checkpoint(library, 29, pending=False)
    _assert_briefs(library, 29)
