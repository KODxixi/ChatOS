from __future__ import annotations

import json
from pathlib import Path


def _runtime(tmp_path: Path):
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-gateway.sqlite"
    initialize_database(database_path).close()
    identifiers = iter(f"run-{index}" for index in range(1, 20))
    return AgentRunService(
        create_connection_factory(database_path),
        clock=lambda: 8_000,
        id_factory=lambda: next(identifiers),
    )


def test_agent_run_events_are_durable_and_replay_from_sequence(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    created = runtime.create_run(
        request={"question": "What changed?", "scope": {"mode": "global"}},
        source="http",
    )

    assert created["status"] == "queued"
    claimed = runtime.claim_next()
    assert claimed is not None
    assert claimed.id == created["id"]

    first = runtime.append_event(claimed.id, {"type": "status", "phase": "retrieving"})
    second = runtime.append_event(claimed.id, {"type": "delta", "text": "answer"})
    runtime.complete(claimed.id, thread_id=None, answer="answer")

    assert first == {
        "type": "status",
        "phase": "retrieving",
        "runId": claimed.id,
        "seq": 1,
    }
    assert second["seq"] == 2
    assert runtime.list_events(claimed.id, after_seq=1) == [second]
    assert runtime.get_run(claimed.id)["status"] == "completed"
    assert runtime.get_run(claimed.id)["threadId"] is None
    assert runtime.get_run(claimed.id)["request"]["question"] == "What changed?"


def test_agent_run_recovery_does_not_regenerate_an_interrupted_answer(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    run = runtime.create_run(
        request={"question": "Continue", "scope": {"mode": "global"}},
        source="weixin",
        source_ref="inbox-1",
    )
    runtime.claim_next()
    runtime.append_event(run["id"], {"type": "delta", "text": "partial"})

    assert runtime.recover_interrupted() == 1
    recovered = runtime.get_run(run["id"])
    assert recovered["status"] == "interrupted"
    assert recovered["errorCode"] == "APP_RESTARTED"
    assert runtime.claim_next() is None


def test_cancel_queued_agent_run_is_terminal_and_audited(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = runtime.create_run(
        request={"question": "Stop", "scope": {"mode": "global"}},
        source="http",
    )

    cancelled = runtime.request_cancel(run["id"])

    assert cancelled["status"] == "cancelled"
    assert runtime.claim_next() is None
    assert runtime.list_events(run["id"], after_seq=0)[-1]["type"] == "cancelled"


def test_failed_agent_run_can_be_requeued_without_changing_run_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = runtime.create_run(
        request={"question": "Retry", "scope": {"mode": "global"}},
        source="http",
    )
    runtime.claim_next()
    runtime.fail(run["id"], "MODEL_OFFLINE")

    retried = runtime.retry(run["id"])

    assert retried["id"] == run["id"]
    assert retried["status"] == "queued"
    assert runtime.claim_next() is not None


def test_queued_agent_run_request_can_be_updated_by_project_router(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = runtime.create_run(
        request={"question": "Launch", "scope": {"mode": "global"}},
        source="weixin",
        source_ref="inbox-1",
    )

    updated = runtime.update_request(
        run["id"],
        {"scope": {"mode": "project", "projectId": "project-1"}, "projectId": "project-1"},
    )

    assert updated["request"]["scope"] == {
        "mode": "project",
        "projectId": "project-1",
    }


def test_migration_keeps_weixin_tokens_out_of_sqlite(tmp_path: Path) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(tmp_path / "schema.sqlite")
    try:
        channel_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(channel_accounts)")
        }
        context_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(channel_contexts)")
        }
        assert "bot_token" not in channel_columns
        assert "context_token" not in context_columns
        assert "bot_token_ref" in channel_columns
        assert "context_token_ref" in context_columns

        sql = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        )
        assert "UNIQUE(account_id, external_message_id)" in sql
    finally:
        connection.close()


def test_gateway_worker_notifies_channel_lifecycle_callbacks(tmp_path: Path) -> None:
    from wechat_ai.services.agent_gateway_runtime import AgentGatewayWorker

    runtime = _runtime(tmp_path)
    runtime.create_run(
        request={"question": "Hello", "scope": {"mode": "global"}},
        source="weixin",
        source_ref="inbox-1",
    )
    started: list[str] = []
    finished: list[str] = []
    executed: list[str] = []

    def execute(run_id: str, _request: object):
        executed.append(run_id)
        return iter([{"type": "done", "threadId": "thread-1"}])

    worker = AgentGatewayWorker(
        runtime,
        execute,
        on_started=lambda run: started.append(run.id),
        on_finished=lambda run, result: finished.append(
            f"{run.id}:{result['status']}"
        ),
    )

    assert worker.run_once() is True
    assert started == ["run-1"]
    assert executed == ["run-1"]
    assert finished == ["run-1:completed"]
