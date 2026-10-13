from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeAgentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def stream(self, **request):
        self.calls.append(request)
        return iter(
            [
                {"type": "status", "phase": "retrieving"},
                {"type": "delta", "text": "answer"},
                {"type": "done", "threadId": "thread-1"},
            ]
        )


def _make_thread_service(path: Path):
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.conversation_service import create_connection_factory

    initialize_database(path).close()
    identifiers = iter((f"thread-{index}" for index in range(1, 20)))
    return AgentThreadService(
        create_connection_factory(path),
        clock=lambda: 8_000,
        id_factory=lambda: next(identifiers),
    )


def test_agent_stream_api_maps_exact_request_and_returns_ndjson(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router

    agent = FakeAgentService()
    app = FastAPI()
    app.include_router(
        create_agent_router(agent, _make_thread_service(tmp_path / "api.sqlite")),
        prefix="/api",
    )

    response = TestClient(app).post(
        "/api/agent/stream",
        json={
            "threadId": None,
            "question": "What changed?",
            "modelProfileId": "local-default",
            "scope": {"mode": "global"},
            "options": {
                "reasoningMode": "deep",
                "includeThreadHistory": False,
                "generateInsights": True,
            },
            "remoteSendConfirmation": {"fingerprint": "preview-fingerprint"},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["status", "delta", "done"]
    assert agent.calls == [
        {
            "thread_id": None,
            "question": "What changed?",
                "model_profile_id": "local-default",
                "scope": {"mode": "global"},
                "project_id": None,
                "remote_send_confirmation": "preview-fingerprint",
                "reasoning_mode": "deep",
                "include_thread_history": False,
                "generate_insights": True,
        }
    ]


def test_agent_stream_options_have_safe_defaults() -> None:
    from wechat_ai.api.agent import AgentStreamRequest

    request = AgentStreamRequest.model_validate(
        {
            "question": "What changed?",
            "scope": {"mode": "global"},
        }
    )

    assert request.options.model_dump() == {
        "reasoningMode": "auto",
        "includeThreadHistory": True,
        "generateInsights": False,
    }


def test_agent_stream_api_returns_remote_preview_as_sanitized_409(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.services.agent_service import (
        RemoteSendConfirmationRequired,
        RemoteSendPreview,
    )
    from wechat_ai.services.agent_thread_service import AgentCitation

    preview = RemoteSendPreview(
        destination_host="models.example",
        question="What changed?",
        evidence=(
            AgentCitation(
                conversation_id="conversation-alpha",
                message_id=1,
                part_id=1,
                sender_name="Alice",
                sent_at=2_000,
                excerpt="Launch scope approved",
            ),
        ),
        fingerprint="preview-fingerprint",
    )

    class ConfirmationAgent:
        def stream(self, **_request):
            raise RemoteSendConfirmationRequired(preview)

    app = FastAPI()
    app.include_router(
        create_agent_router(
            ConfirmationAgent(),
            _make_thread_service(tmp_path / "remote-api.sqlite"),
        ),
        prefix="/api",
    )
    response = TestClient(app).post(
        "/api/agent/stream",
        json={
            "question": "What changed?",
            "modelProfileId": "remote-profile",
            "scope": {"mode": "global"},
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "REMOTE_SEND_CONFIRMATION_REQUIRED",
            "message": "发送到远程模型前需要确认本次内容。",
            "retryable": False,
            "details": {"preview": preview.to_dict()},
        }
    }
    assert "secret" not in response.text.casefold()


def test_agent_stream_api_rejects_filtered_global_scope(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.services.agent_thread_service import normalize_knowledge_scope

    class ValidatingAgent(FakeAgentService):
        def stream(self, **request):
            normalize_knowledge_scope(request["scope"])
            return super().stream(**request)

    app = FastAPI()
    app.include_router(
        create_agent_router(
            ValidatingAgent(),
            _make_thread_service(tmp_path / "scope-api.sqlite"),
        ),
        prefix="/api",
    )

    response = TestClient(app).post(
        "/api/agent/stream",
        json={
            "question": "What changed?",
            "modelProfileId": "local-default",
            "scope": {
                "mode": "global",
                "conversationIds": ["conversation-alpha"],
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "KNOWLEDGE_SCOPE_INVALID"


def test_agent_thread_api_crud_is_persistent(tmp_path: Path) -> None:
    from wechat_ai.api.agent import create_agent_router

    database_path = tmp_path / "threads-api.sqlite"
    threads = _make_thread_service(database_path)
    app = FastAPI()
    app.include_router(
        create_agent_router(FakeAgentService(), threads),
        prefix="/api",
    )
    client = TestClient(app)

    created = client.post(
        "/api/agent/threads",
        json={"title": "Research", "scope": {"mode": "global"}},
    )
    assert created.status_code == 201
    thread_id = created.json()["id"]
    assert created.json()["turns"] == []

    listed = client.get("/api/agent/threads?limit=20")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [thread_id]

    fetched = client.get(f"/api/agent/threads/{thread_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Research"

    patched = client.patch(
        f"/api/agent/threads/{thread_id}",
        json={"title": "Research archive", "isArchived": True},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Research archive"
    assert patched.json()["isArchived"] is True

    deleted = client.delete(f"/api/agent/threads/{thread_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/agent/threads/{thread_id}").status_code == 404


def test_agent_threads_can_be_grouped_by_gchat_project(tmp_path: Path) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.services.gchat_service import GchatService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "project-threads.sqlite"
    threads = _make_thread_service(database_path)
    project = GchatService(create_connection_factory(database_path)).create_project("Launch")
    app = FastAPI()
    app.include_router(
        create_agent_router(FakeAgentService(), threads),
        prefix="/api",
    )
    client = TestClient(app)

    created = client.post(
        "/api/agent/threads",
        json={
            "title": "Launch decision",
            "projectId": project["id"],
            "scope": {"mode": "global"},
        },
    )

    assert created.status_code == 201
    assert created.json()["projectId"] == project["id"]
    listed = client.get(f"/api/agent/threads?projectId={project['id']}")
    assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]


def test_new_project_thread_defaults_to_project_scope_but_allows_global_override(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "project-thread-default.sqlite"
    threads = _make_thread_service(database_path)
    project = GchatService(create_connection_factory(database_path)).create_project(
        "Launch"
    )
    app = FastAPI()
    app.include_router(
        create_agent_router(FakeAgentService(), threads),
        prefix="/api",
    )
    client = TestClient(app)

    defaulted = client.post(
        "/api/agent/threads",
        json={"title": "Project thread", "projectId": project["id"]},
    )
    assert defaulted.status_code == 201
    assert defaulted.json()["scope"] == {
        "mode": "project",
        "projectId": project["id"],
    }

    global_override = client.post(
        "/api/agent/threads",
        json={
            "title": "Global research",
            "projectId": project["id"],
            "scope": {"mode": "global"},
        },
    )
    assert global_override.status_code == 201
    assert global_override.json()["scope"] == {"mode": "global"}


def test_agent_runs_api_queues_replays_and_cancels_durable_runs(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-runs-api.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    identifiers = iter(f"api-run-{index}" for index in range(1, 20))
    runs = AgentRunService(
        connection_factory,
        clock=lambda: 9_000,
        id_factory=lambda: next(identifiers),
    )
    notified: list[bool] = []
    app = FastAPI()
    app.include_router(
        create_agent_router(
            FakeAgentService(),
            AgentThreadService(connection_factory),
            runs=runs,
            run_created=lambda: notified.append(True),
        ),
        prefix="/api",
    )
    client = TestClient(app)

    created = client.post(
        "/api/agent/runs",
        json={"question": "What changed?", "scope": {"mode": "global"}},
    )
    assert created.status_code == 202
    run_id = created.json()["runId"]
    assert created.json()["status"] == "queued"
    assert notified == [True]

    claimed = runs.claim_next()
    assert claimed is not None
    runs.append_event(run_id, {"type": "grounding", "status": "grounded"})
    runs.complete(run_id, thread_id=None, answer="answer")

    replay = client.get(f"/api/agent/runs/{run_id}/events?afterSeq=0")
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("application/x-ndjson")
    assert json.loads(replay.text)["runId"] == run_id
    assert json.loads(replay.text)["seq"] == 1


def test_agent_stream_events_include_durable_run_id_and_sequence(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-stream-runtime.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    runs = AgentRunService(connection_factory, clock=lambda: 9_000)
    agent = FakeAgentService()
    app = FastAPI()
    app.include_router(
        create_agent_router(
            agent,
            AgentThreadService(connection_factory),
            runs=runs,
        ),
        prefix="/api",
    )

    response = TestClient(app).post(
        "/api/agent/stream",
        json={"question": "What changed?", "scope": {"mode": "global"}},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert len({event["runId"] for event in events}) == 1
    run_id = events[0]["runId"]
    assert agent.calls == [
        {
            "thread_id": None,
            "question": "What changed?",
            "model_profile_id": None,
            "scope": {"mode": "global"},
            "project_id": None,
            "remote_send_confirmation": None,
            "reasoning_mode": "auto",
            "include_thread_history": True,
            "generate_insights": False,
            "execution_run_id": run_id,
        }
    ]
    assert runs.get_run(run_id)["status"] == "completed"


def test_agent_run_index_filters_summarizes_and_returns_private_call_details(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.call_ledger_service import CallLedgerService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-run-index.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    now = [1_000]
    run_ids = iter(("run-old", "run-new", "run-weixin"))
    runs = AgentRunService(
        connection_factory,
        clock=lambda: now[0],
        id_factory=lambda: next(run_ids),
    )
    ledger_ids = iter(("model-old", "model-new", "retrieval-new"))
    ledger = CallLedgerService(
        connection_factory,
        clock=lambda: now[0],
        id_factory=lambda: next(ledger_ids),
    )

    old = runs.create_run(
        request={"question": "private old", "scope": {"mode": "global"}},
        source="http",
        start_immediately=True,
    )
    old_model = ledger.start_span(
        run_id=old["runId"],
        kind="model",
        operation="chat.completions",
        model_profile_id="local-default",
        metadata={"provider": "lm-studio", "reasoningMode": "fast", "isRemote": False},
    )
    now[0] = 1_100
    ledger.finish_span(old_model, input_tokens=3, output_tokens=2, total_tokens=5)
    runs.complete(old["runId"], thread_id=None, answer="private answer")

    now[0] = 2_000
    new = runs.create_run(
        request={"question": "private new", "scope": {"mode": "global"}},
        source="http",
        start_immediately=True,
    )
    new_model = ledger.start_span(
        run_id=new["runId"],
        kind="model",
        operation="chat.completions",
        model_profile_id="local-default",
        metadata={"provider": "lm-studio", "reasoningMode": "fast", "isRemote": False},
    )
    retrieval = ledger.start_span(
        run_id=new["runId"],
        kind="retrieval",
        operation="fts5-v1",
        metadata={"backend": "fts5-v1", "indexVersion": 1, "scopeMode": "global"},
    )
    now[0] = 2_250
    ledger.finish_span(new_model, input_tokens=8, output_tokens=5, total_tokens=13)
    ledger.finish_span(retrieval, candidate_count=7, citation_count=2)
    runs.complete(new["runId"], thread_id=None, answer="private answer")

    now[0] = 3_000
    runs.create_run(
        request={"question": "private weixin", "scope": {"mode": "global"}},
        source="weixin",
        source_ref="bot-1:1",
    )

    app = FastAPI()
    app.include_router(
        create_agent_router(
            FakeAgentService(),
            AgentThreadService(connection_factory),
            runs=runs,
            call_ledger=ledger,
        ),
        prefix="/api",
    )
    client = TestClient(app)

    first_page = client.get(
        "/api/agent/runs?limit=1&status=completed&source=http&modelProfileId=local-default&startedAfter=500&startedBefore=3000"
    )
    assert first_page.status_code == 200
    payload = first_page.json()
    assert [item["runId"] for item in payload["items"]] == ["run-new"]
    assert payload["nextCursor"] is not None
    assert payload["retentionDays"] == 90
    assert payload["summary"] == {
        "total": 2,
        "active": 0,
        "failed": 0,
        "averageDurationMs": 175,
        "inputTokens": 11,
        "outputTokens": 7,
        "totalTokens": 18,
    }
    assert payload["items"][0]["metrics"] == {
        "totalDurationMs": 250,
        "retrievalCandidateCount": 7,
        "citationCount": 2,
        "modelCallCount": 1,
        "inputTokens": 8,
        "outputTokens": 5,
        "totalTokens": 13,
    }

    second_page = client.get(
        "/api/agent/runs",
        params={
            "limit": 1,
            "status": "completed",
            "source": "http",
            "modelProfileId": "local-default",
            "startedAfter": 500,
            "startedBefore": 3000,
            "cursor": payload["nextCursor"],
        },
    ).json()
    assert [item["runId"] for item in second_page["items"]] == ["run-old"]

    calls = client.get("/api/agent/runs/run-new/calls")
    assert calls.status_code == 200
    assert [item["kind"] for item in calls.json()["items"]] == ["model", "retrieval"]
    serialized = json.dumps(calls.json(), ensure_ascii=False)
    assert "private new" not in serialized
    assert "private answer" not in serialized
    assert "excerpt" not in serialized

    invalid_cursor = client.get("/api/agent/runs?cursor=not-a-run-cursor")
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "AGENT_RUN_INVALID_CURSOR"


def test_agent_route_candidate_api_confirms_and_rejects_reviewable_routes(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.agent import create_agent_router

    class FakeRoutes:
        def confirm(self, candidate_id: str, project_id: str | None = None):
            return {"id": candidate_id, "status": "confirmed", "projectId": project_id}

        def reject(self, candidate_id: str):
            return {"id": candidate_id, "status": "rejected"}

        def list_candidates(self):
            return {"items": []}

    app = FastAPI()
    app.include_router(
        create_agent_router(
            FakeAgentService(),
            _make_thread_service(tmp_path / "route-api.sqlite"),
            routes=FakeRoutes(),
        ),
        prefix="/api",
    )
    client = TestClient(app)

    confirmed = client.post(
        "/api/agent/route-candidates/candidate-1/confirm",
        json={"projectId": "project-2"},
    )
    rejected = client.post("/api/agent/route-candidates/candidate-2/reject")

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
