from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest


def _seed_agent_library(path: Path) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(path)
    try:
        connection.execute(
            """
            INSERT INTO model_profiles(
                id, name, provider, base_url, model, auth_kind,
                is_default, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "local-default",
                "Local",
                "lm-studio",
                "http://127.0.0.1:1234/v1",
                "local-model",
                "none",
                1,
                1_000,
                1_000,
            ),
        )
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            ("conversation-alpha", "source-alpha", "Alpha", "group"),
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "conversation-alpha",
                "message-alpha",
                "Alice",
                2_000,
                "text",
                "Launch scope approved",
            ),
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                message.lastrowid,
                0,
                "text",
                "resolved",
                "Launch scope approved",
            ),
        )
        connection.commit()
        assert message.lastrowid == 1
        assert part.lastrowid == 1
    finally:
        connection.close()


def _make_thread_service(path: Path, *, prefix: str = "generated"):
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    identifiers = (
        f"{prefix}-{index}" for index in itertools.count(1)
    )
    return AgentThreadService(
        create_connection_factory(path),
        clock=lambda: 5_000,
        id_factory=lambda: next(identifiers),
    )


def test_global_scope_has_no_filters_and_is_snapshotted_on_each_turn(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent.sqlite"
    _seed_agent_library(database_path)
    service = _make_thread_service(database_path)

    run = service.start_run(
        thread_id=None,
        question="What changed?",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )

    assert run.scope.to_dict() == {"mode": "global"}
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT role, scope_json
            FROM agent_turns
            WHERE thread_id = ?
            ORDER BY created_at, id
            """,
            (run.thread_id,),
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("assistant", '{"mode":"global"}'),
        ("user", '{"mode":"global"}'),
    ] or rows == [
        ("user", '{"mode":"global"}'),
        ("assistant", '{"mode":"global"}'),
    ]


def test_scope_validation_keeps_global_unfiltered_and_selection_strict() -> None:
    from wechat_ai.services.agent_thread_service import (
        KnowledgeScopeValidationError,
        normalize_knowledge_scope,
    )

    assert normalize_knowledge_scope({"mode": "global"}).to_dict() == {
        "mode": "global"
    }
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "global", "conversationIds": ["alpha"]}
        )
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope({"mode": "selection"})
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "selection", "conversationIds": [" "]}
        )
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "selection", "conversationKinds": ["system"]}
        )
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "selection", "kinds": ["group"]}
        )
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "selection", "messageIds": [0]}
        )
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {"mode": "selection", "contentKinds": ["unknown"]}
        )

    assert normalize_knowledge_scope(
        {
            "mode": "selection",
            "sourceAppIds": ["wechat"],
            "messageIds": [1],
            "conversationKinds": ["group"],
            "contentKinds": ["text"],
            "nodeIds": ["node-alpha"],
        }
    ).to_dict() == {
        "mode": "selection",
        "sourceAppIds": ["wechat"],
        "messageIds": [1],
        "conversationKinds": ["group"],
        "contentKinds": ["text"],
        "nodeIds": ["node-alpha"],
    }

    assert normalize_knowledge_scope(
        {"mode": "project", "projectId": "project-launch"}
    ).to_dict() == {
        "mode": "project",
        "projectId": "project-launch",
    }
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope({"mode": "project"})
    with pytest.raises(KnowledgeScopeValidationError):
        normalize_knowledge_scope(
            {
                "mode": "project",
                "projectId": "project-launch",
                "conversationIds": ["conversation-alpha"],
            }
        )


def test_completed_run_persists_answer_and_exact_citation(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_thread_service import AgentCitation

    database_path = tmp_path / "agent.sqlite"
    _seed_agent_library(database_path)
    service = _make_thread_service(database_path)
    run = service.start_run(
        thread_id=None,
        question="What changed?",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )

    service.complete_run(
        run,
        answer="The launch scope was approved.",
        citations=[
            AgentCitation(
                conversation_id="conversation-alpha",
                message_id=1,
                part_id=1,
                sender_name="Alice",
                sent_at=2_000,
                excerpt="Launch scope approved",
            )
        ],
    )
    thread = service.get_thread(run.thread_id)

    assert [turn["role"] for turn in thread["turns"]] == [
        "user",
        "assistant",
    ]
    assistant = thread["turns"][1]
    assert assistant["status"] == "complete"
    assert assistant["content"] == "The launch scope was approved."
    assert assistant["citations"] == [
        {
            "conversationId": "conversation-alpha",
            "messageId": 1,
            "partId": 1,
            "senderName": "Alice",
            "sentAt": 2_000,
            "excerpt": "Launch scope approved",
        }
    ]


def test_cancelled_run_persists_no_partial_answer(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent.sqlite"
    _seed_agent_library(database_path)
    service = _make_thread_service(database_path)
    run = service.start_run(
        thread_id=None,
        question="Question",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )

    service.cancel_run(run)

    assistant = service.get_thread(run.thread_id)["turns"][1]
    assert assistant["status"] == "cancelled"
    assert assistant["content"] == ""
    assert assistant["citations"] == []


def test_thread_rejects_concurrent_run_but_snapshots_scope_changes(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_thread_service import (
        AgentThreadBusy,
    )

    database_path = tmp_path / "agent.sqlite"
    _seed_agent_library(database_path)
    service = _make_thread_service(database_path)
    first = service.start_run(
        thread_id=None,
        question="First",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )

    with pytest.raises(AgentThreadBusy):
        service.start_run(
            thread_id=first.thread_id,
            question="Concurrent",
            model_profile_id="local-default",
            scope={"mode": "global"},
        )

    service.cancel_run(first)
    second = service.start_run(
        thread_id=first.thread_id,
        question="Different scope",
        model_profile_id="local-default",
        scope={
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
    )
    service.cancel_run(second)

    thread = service.get_thread(first.thread_id)
    assert thread["scope"] == {
        "mode": "selection",
        "conversationIds": ["conversation-alpha"],
    }
    assert [turn["scope"] for turn in thread["turns"]] == [
        {"mode": "global"},
        {"mode": "global"},
        {
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
        {
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
    ]


def test_sql_scope_resolver_applies_every_filter_and_graph_evidence(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        SqlAgentEvidenceRetriever,
        SqlKnowledgeScopeResolver,
    )
    from wechat_ai.services.agent_thread_service import (
        normalize_knowledge_scope,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "scope.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversation_origins(
                conversation_id, source_account_id,
                external_conversation_id
            ) VALUES ('conversation-alpha', 'wechat:default', 'source-alpha')
            """
        )
        connection.execute(
            """
            INSERT INTO retrieval_chunks(
                message_part_id, message_id, conversation_id, kind,
                text_content, content_hash, index_version,
                sender_name, sent_at
            ) VALUES (1, 1, 'conversation-alpha', 'text',
                      'Launch scope approved', 'hash-alpha', 1,
                      'Alice', 2000)
            """
        )
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('conversation-beta', 'source-beta', 'Beta', 'direct')
            """
        )
        connection.execute(
            """
            INSERT INTO conversation_origins(
                conversation_id, source_account_id,
                external_conversation_id
            ) VALUES ('conversation-beta', 'wechat:default', 'source-beta')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('conversation-beta', 'message-beta', 'Bob',
                      3000, 'text', 'Unrelated budget note')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'Unrelated budget note')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO retrieval_chunks(
                message_part_id, message_id, conversation_id, kind,
                text_content, content_hash, index_version,
                sender_name, sent_at
            ) VALUES (?, ?, 'conversation-beta', 'text',
                      'Unrelated budget note', 'hash-beta', 1,
                      'Bob', 3000)
            """,
            (part.lastrowid, message.lastrowid),
        )
        connection.executemany(
            """
            INSERT INTO graph_nodes(
                id, kind, label, trace_kind, trace_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ('topic-alpha', 'topic', 'Launch', 'topic', 'launch'),
                (
                    'conversation-node-alpha', 'conversation', 'Alpha',
                    'conversation', 'conversation-alpha',
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO graph_edges(
                id, source_node_id, target_node_id, kind
            ) VALUES ('edge-alpha', 'topic-alpha',
                      'conversation-node-alpha', 'related_to')
            """
        )
        connection.execute(
            """
            INSERT INTO graph_edge_evidence(
                edge_id, evidence_kind, evidence_id
            ) VALUES ('edge-alpha', 'message', '1')
            """
        )
        connection.commit()
    finally:
        connection.close()

    connection_factory = create_connection_factory(database_path)
    resolver = SqlKnowledgeScopeResolver(connection_factory)
    assert resolver.resolve(
        normalize_knowledge_scope({"mode": "global"})
    ).message_part_ids is None

    selected = resolver.resolve(
        normalize_knowledge_scope(
            {
                "mode": "selection",
                "sourceAppIds": ["wechat"],
                "sourceAccountIds": ["wechat:default"],
                "conversationIds": ["conversation-alpha"],
                "messageIds": [1],
                "conversationKinds": ["group"],
                "contentKinds": ["text"],
                "nodeIds": ["topic-alpha"],
            }
        )
    )
    assert selected.message_part_ids == (1,)
    assert resolver.resolve(
        normalize_knowledge_scope(
            {"mode": "selection", "nodeIds": ["missing-node"]}
        )
    ).message_part_ids == ()

    evidence = SqlAgentEvidenceRetriever(connection_factory).retrieve(
        "Launch scope",
        selected,
        40,
    )
    assert [item.to_citation().to_dict() for item in evidence] == [
        {
            "conversationId": "conversation-alpha",
            "messageId": 1,
            "partId": 1,
            "senderName": "Alice",
            "sentAt": 2000,
            "excerpt": "Launch scope approved",
        }
    ]


def test_thread_crud_pagination_and_delete_cascades(tmp_path: Path) -> None:
    from wechat_ai.services.agent_thread_service import AgentCitation

    database_path = tmp_path / "thread-crud.sqlite"
    _seed_agent_library(database_path)
    service = _make_thread_service(database_path)
    first_run = service.start_run(
        thread_id=None,
        question="First question",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )
    service.complete_run(
        first_run,
        answer="First answer",
        citations=[
            AgentCitation(
                conversation_id="conversation-alpha",
                message_id=1,
                part_id=1,
                sender_name="Alice",
                sent_at=2_000,
                excerpt="Launch scope approved",
            )
        ],
    )
    second = service.create_thread(
        title="Second thread",
        scope={"mode": "selection", "conversationIds": ["conversation-alpha"]},
    )
    third = service.create_thread(
        title="Third thread",
        scope={"mode": "global"},
    )

    first_page = service.list_threads(limit=2, cursor=None)
    second_page = service.list_threads(
        limit=2,
        cursor=first_page["nextCursor"],
    )
    listed_ids = [item["id"] for item in first_page["items"]]
    listed_ids.extend(item["id"] for item in second_page["items"])
    assert set(listed_ids) == {first_run.thread_id, second["id"], third["id"]}
    assert len(listed_ids) == len(set(listed_ids))

    updated = service.update_thread(
        second["id"],
        {"title": "Renamed", "isArchived": True},
    )
    assert updated["title"] == "Renamed"
    assert updated["isArchived"] is True

    service.delete_thread(first_run.thread_id)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_threads WHERE id = ?",
            (first_run.thread_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_turns WHERE thread_id = ?",
            (first_run.thread_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM agent_citations
            WHERE assistant_turn_id = ?
            """,
            (first_run.assistant_turn_id,),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_restart_recovery_interrupts_orphan_run_and_keeps_snapshots(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "thread-restart.sqlite"
    _seed_agent_library(database_path)
    first_service = _make_thread_service(database_path)
    orphan = first_service.start_run(
        thread_id=None,
        question="Before restart",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )

    restarted_service = _make_thread_service(
        database_path,
        prefix="restarted",
    )
    assert restarted_service.recover_interrupted_runs() == 1
    recovered = restarted_service.get_thread(orphan.thread_id)
    assert recovered["turns"][1]["status"] == "interrupted"
    assert recovered["turns"][1]["content"] == ""

    next_run = restarted_service.start_run(
        thread_id=orphan.thread_id,
        question="After restart",
        model_profile_id="local-default",
        scope={
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
    )
    restarted_service.cancel_run(next_run)
    snapshots = [
        turn["scope"]
        for turn in restarted_service.get_thread(orphan.thread_id)["turns"]
    ]
    assert snapshots[:2] == [{"mode": "global"}, {"mode": "global"}]
    assert snapshots[2:] == [
        {
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
        {
            "mode": "selection",
            "conversationIds": ["conversation-alpha"],
        },
    ]


class _CountingCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls: list[str] = []

    def has(self, profile_id: str) -> bool:
        return profile_id in self.values

    def get(self, profile_id: str) -> str | None:
        self.get_calls.append(profile_id)
        return self.values.get(profile_id)

    def set(self, profile_id: str, value: str) -> None:
        self.values[profile_id] = value

    def delete(self, profile_id: str) -> None:
        self.values.pop(profile_id, None)


class _FixedScopeResolver:
    def __init__(self, resolved) -> None:
        self.resolved = resolved
        self.calls = []

    def resolve(self, scope):
        self.calls.append(scope)
        return self.resolved


class _FixedRetriever:
    def __init__(self, items, *, scores=None) -> None:
        self.items = list(items)
        self.scores = list(scores or [None] * len(self.items))
        self.calls = []

    def retrieve(self, request):
        from wechat_ai.services.agent_service import RetrievalHit, RetrievalResult

        self.calls.append((request.question, request.scope, request.limit))
        return RetrievalResult(
            backend="fixed-v1",
            index_version=1,
            hits=tuple(
                RetrievalHit(
                    evidence=item,
                    rank=index,
                    score=self.scores[index - 1],
                )
                for index, item in enumerate(self.items, start=1)
            ),
            candidate_count=len(self.items),
        )


class _RecordingProvider:
    def __init__(self, deltas=("Grounded ", "answer")) -> None:
        self.deltas = deltas
        self.calls = []
        self.closed = False

    def stream_answer(self, question, contexts, *, history=()):
        self.calls.append((question, list(contexts), list(history)))
        try:
            yield from self.deltas
        finally:
            self.closed = True


def test_reasoning_mode_classifier_keeps_simple_questions_fast() -> None:
    from wechat_ai.services.agent_service import classify_reasoning_mode

    assert classify_reasoning_mode("谁批准了预算？") == "fast"
    assert classify_reasoning_mode("请深入分析两个方案的风险与取舍") == "deep"
    assert classify_reasoning_mode("Compare the tradeoffs and explain why") == "deep"


def test_agent_records_retrieval_model_usage_and_citation_spans(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ModelUsage,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.call_ledger_service import CallLedgerService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-call-spans.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    run_id = str(
        AgentRunService(
            connection_factory,
            clock=lambda: 1_000,
            id_factory=lambda: "gateway-run-1",
        ).create_run(
            request={"question": "private", "scope": {"mode": "global"}},
            source="http",
        )["id"]
    )
    now = [2_000]
    span_ids = iter(("retrieval-span", "model-span"))
    ledger = CallLedgerService(
        connection_factory,
        clock=lambda: now[0],
        id_factory=lambda: next(span_ids),
    )
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )

    class UsageProvider:
        def stream_answer(self, _question, _contexts, *, history=(), usage_sink=None):
            if usage_sink is not None:
                usage_sink(ModelUsage(input_tokens=21, output_tokens=8, total_tokens=29))
            yield "Grounded answer"

    credentials = _CountingCredentials()
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(database_path, prefix="ledger"),
        model_profile_service=_make_model_service(database_path, credentials),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: UsageProvider(),
        call_ledger=ledger,
    )

    events = list(
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
            execution_run_id=run_id,
        )
    )

    assert events[-1]["type"] == "done"
    spans = ledger.list_run_spans(run_id)["items"]
    assert [item["kind"] for item in spans] == ["retrieval", "model"]
    assert spans[0]["candidateCount"] == 1
    assert spans[0]["citationCount"] == 1
    assert [item["role"] for item in spans[0]["evidence"]] == [
        "candidate",
        "citation",
    ]
    assert spans[1]["modelProfileId"] == "local-default"
    assert spans[1]["inputTokens"] == 21
    assert spans[1]["outputTokens"] == 8
    assert spans[1]["totalTokens"] == 29


def test_deepseek_reasoning_mode_is_resolved_per_turn(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        RemoteSendConfirmationRequired,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "deepseek-reasoning.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    models = _make_model_service(database_path, credentials)
    profile = models.create_deepseek_v4_flash_preset()
    credentials.values[profile["id"]] = "synthetic-secret"
    models.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )

    class ReasoningProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        def stream_answer(
            self,
            _question,
            _contexts,
            *,
            history=(),
            thinking=None,
            reasoning_effort=None,
        ):
            del history
            self.calls.append((thinking, reasoning_effort))
            yield "answer"

    provider = ReasoningProvider()
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(database_path, prefix="reasoning"),
        model_profile_service=models,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: provider,
    )

    with pytest.raises(RemoteSendConfirmationRequired) as preview:
        service.stream(
            thread_id=None,
            question="谁批准了预算？",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
            reasoning_mode="fast",
        )
    assert list(
        service.stream(
            thread_id=None,
            question="谁批准了预算？",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=preview.value.preview.fingerprint,
            reasoning_mode="fast",
        )
    )[-1]["type"] == "done"
    assert list(
        service.stream(
            thread_id=None,
            question="请深入分析两个方案的风险与取舍",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
            reasoning_mode="auto",
        )
    )[-1]["type"] == "done"

    assert provider.calls == [(False, None), (True, "high")]


def _make_model_service(path: Path, credentials, *, profile_id="remote-profile"):
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.model_profile_service import ModelProfileService

    return ModelProfileService(
        create_connection_factory(path),
        credentials,
        clock=lambda: 7_000,
        id_factory=lambda: profile_id,
    )


def test_agent_orchestrator_emits_only_five_event_types_and_persists_result(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-orchestrator.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    model_profiles = _make_model_service(database_path, credentials)
    threads = _make_thread_service(database_path, prefix="orchestrator")
    resolved = ResolvedKnowledgeScope(mode="global", message_part_ids=None)
    resolver = _FixedScopeResolver(resolved)
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    retriever = _FixedRetriever([evidence], scores=[0.93])
    provider = _RecordingProvider()
    provider_calls = []

    def provider_factory(profile, credential):
        provider_calls.append((profile.id, credential))
        return provider

    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=model_profiles,
        scope_resolver=resolver,
        retriever=retriever,
        provider_factory=provider_factory,
    )

    events = list(
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )

    assert [event["type"] for event in events] == [
        "status",
        "status",
        "delta",
        "delta",
        "grounding",
        "citation",
        "done",
    ]
    assert {event["type"] for event in events} <= {
        "status", "delta", "grounding", "citation", "done", "error"
    }
    assert resolver.calls[0].to_dict() == {"mode": "global"}
    assert retriever.calls == [("What was approved?", resolved, 40)]
    assert provider_calls == [("local-default", None)]
    assert provider.calls[0][1][0].score == 0.93
    assert events[-2]["item"] == evidence.to_citation().to_dict()
    thread = threads.get_thread(events[-1]["threadId"])
    assert thread["turns"][1]["content"] == "Grounded answer"
    assert thread["turns"][1]["citations"] == [
        evidence.to_citation().to_dict()
    ]

    follow_up_events = list(
        service.stream(
            thread_id=events[-1]["threadId"],
            question="What is the source?",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )
    assert follow_up_events[-1]["type"] == "done"
    assert provider.calls[1][2] == [
        {"role": "user", "content": "What was approved?"},
        {"role": "assistant", "content": "Grounded answer"},
    ]

    selection_events = list(
        service.stream(
            thread_id=events[-1]["threadId"],
            question="Only this conversation?",
            model_profile_id="local-default",
            scope={
                "mode": "selection",
                "conversationIds": ["conversation-alpha"],
            },
            remote_send_confirmation=None,
        )
    )
    assert selection_events[-1]["type"] == "done"
    assert provider.calls[2][2] == []


def test_agent_options_disable_history_and_persist_grounding_audit(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-options-audit.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="options-audit")
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    provider = _RecordingProvider(("Grounded answer",))
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: provider,
    )
    first = list(
        service.stream(
            thread_id=None,
            question="First question",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )

    second = list(
        service.stream(
            thread_id=first[-1]["threadId"],
            question="Analyze without history",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
            reasoning_mode="deep",
            include_thread_history=False,
            generate_insights=False,
        )
    )

    assert provider.calls[1][2] == []
    grounding = next(event for event in second if event["type"] == "grounding")
    assert grounding == {
        "type": "grounding",
        "status": "grounded",
        "retrievalSummary": {
            "retrievedCount": 1,
            "citationCount": 1,
        },
    }
    assistant = threads.get_thread(first[-1]["threadId"])["turns"][-1]
    assert assistant["modelOptions"] == {
        "reasoningMode": "deep",
        "includeThreadHistory": False,
        "generateInsights": False,
    }
    assert assistant["groundingStatus"] == "grounded"
    assert assistant["retrievalSummary"] == {
        "retrievedCount": 1,
        "citationCount": 1,
    }


def test_generate_insights_option_controls_candidate_events(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-candidate-events.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )

    class InsightGchat:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.capture_calls: list[dict[str, object]] = []

        def capture_turn(self, **request):
            self.capture_calls.append(request)
            return None

        def generate_insights(self, **request):
            self.calls.append(request)
            return [{"id": "candidate-1", "status": "suggested"}]

    gchat = InsightGchat()
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(database_path, prefix="candidate-event"),
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: _RecordingProvider(("Answer",)),
        gchat_service=gchat,
    )

    without = list(
        service.stream(
            thread_id=None,
            question="First",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
            generate_insights=False,
        )
    )
    with_candidates = list(
        service.stream(
            thread_id=None,
            question="Second",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
            generate_insights=True,
        )
    )

    assert gchat.calls[0]["source_turn_id"] == with_candidates[-1][
        "assistantTurnId"
    ]
    assert len(gchat.calls) == 1
    assert len(gchat.capture_calls) == 1
    assert "candidate" not in [event["type"] for event in without]
    assert [
        event for event in with_candidates if event["type"] == "candidate"
    ] == [
        {
            "type": "candidate",
            "item": {"id": "candidate-1", "status": "suggested"},
        }
    ]


def test_agent_thread_run_metadata_is_present_before_and_after_model_call(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_thread_service import AgentCitation

    database_path = tmp_path / "agent-run-metadata.sqlite"
    _seed_agent_library(database_path)
    threads = _make_thread_service(database_path, prefix="metadata")
    run = threads.start_run(
        thread_id=None,
        question="Question",
        model_profile_id="local-default",
        scope={"mode": "global"},
        model_options={
            "reasoningMode": "fast",
            "includeThreadHistory": True,
            "generateInsights": False,
        },
        retrieval_summary={"retrievedCount": 1, "citationCount": 0},
    )

    streaming = threads.get_thread(run.thread_id)["turns"][-1]
    assert streaming["modelOptions"]["reasoningMode"] == "fast"
    assert streaming["groundingStatus"] == "unknown"
    assert streaming["retrievalSummary"]["citationCount"] == 0

    threads.complete_run(
        run,
        answer="Answer",
        citations=[
            AgentCitation(
                conversation_id="conversation-alpha",
                message_id=1,
                part_id=1,
                sender_name="Alice",
                sent_at=2_000,
                excerpt="Launch scope approved",
            )
        ],
        grounding_status="grounded",
        retrieval_summary={"retrievedCount": 1, "citationCount": 1},
    )

    completed = threads.get_thread(run.thread_id)["turns"][-1]
    assert completed["groundingStatus"] == "grounded"
    assert completed["retrievalSummary"]["citationCount"] == 1


def test_sql_scope_resolver_resolves_sorted_project_membership_and_empty_project(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import SqlKnowledgeScopeResolver
    from wechat_ai.services.agent_thread_service import (
        KnowledgeScopeValidationError,
        normalize_knowledge_scope,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "project-scope.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('conversation-beta', 'source-beta', 'Beta', 'direct')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('conversation-beta', 'message-beta', 'Bob',
                      3000, 'text', 'Beta evidence')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'Beta evidence')
            """,
            (message.lastrowid,),
        )
        connection.executemany(
            """
            INSERT INTO projects(id, name, status, created_at, updated_at)
            VALUES (?, ?, ?, 1000, 1000)
            """,
            [
                ("project-launch", "Launch", "active"),
                ("project-empty", "Empty", "active"),
                ("project-archived", "Archived", "archived"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO project_conversations(
                project_id, conversation_id, created_at, updated_at
            ) VALUES ('project-launch', ?, 1000, 1000)
            """,
            [("conversation-beta",), ("conversation-alpha",)],
        )
        connection.commit()
        assert part.lastrowid == 2
    finally:
        connection.close()

    resolver = SqlKnowledgeScopeResolver(
        create_connection_factory(database_path)
    )
    resolved = resolver.resolve(
        normalize_knowledge_scope(
            {"mode": "project", "projectId": "project-launch"}
        )
    )
    assert resolved.mode == "project"
    assert resolved.message_part_ids == (1, 2)
    assert resolved.scope_snapshot is not None
    assert resolved.scope_snapshot.to_dict() == {
        "mode": "project",
        "projectId": "project-launch",
        "conversationIds": ["conversation-alpha", "conversation-beta"],
    }

    empty = resolver.resolve(
        normalize_knowledge_scope(
            {"mode": "project", "projectId": "project-empty"}
        )
    )
    assert empty.message_part_ids == ()
    assert empty.scope_snapshot is not None
    assert empty.scope_snapshot.to_dict() == {
        "mode": "project",
        "projectId": "project-empty",
        "conversationIds": [],
    }

    with pytest.raises(KnowledgeScopeValidationError):
        resolver.resolve(
            normalize_knowledge_scope(
                {"mode": "project", "projectId": "project-archived"}
            )
        )


def test_project_turn_snapshots_membership_and_rejects_out_of_scope_evidence(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        AgentServiceValidationError,
        SqlKnowledgeScopeResolver,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "project-turn.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO projects(id, name, status, created_at, updated_at)
            VALUES (?, ?, 'active', 1000, 1000)
            """,
            [
                ("project-launch", "Launch"),
                ("project-other", "Other"),
            ],
        )
        connection.execute(
            """
            INSERT INTO project_conversations(
                project_id, conversation_id, created_at, updated_at
            ) VALUES ('project-launch', 'conversation-alpha', 1000, 1000)
            """
        )
        connection.commit()
    finally:
        connection.close()

    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    threads = _make_thread_service(database_path, prefix="project")
    retriever = _FixedRetriever(
        [
            AgentEvidence(
                message_id=1,
                part_id=1,
                conversation_id="conversation-alpha",
                sender_name="Alice",
                sent_at=2_000,
                excerpt="Launch scope approved",
            )
        ]
    )
    provider = _RecordingProvider(("Project answer",))
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(database_path, credentials),
        scope_resolver=SqlKnowledgeScopeResolver(connection_factory),
        retriever=retriever,
        provider_factory=lambda _profile, _credential: provider,
    )

    events = list(
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id="local-default",
            scope={"mode": "project", "projectId": "project-launch"},
            remote_send_confirmation=None,
        )
    )
    thread_id = str(events[-1]["threadId"])
    expected_snapshot = {
        "mode": "project",
        "projectId": "project-launch",
        "conversationIds": ["conversation-alpha"],
    }
    assert threads.get_thread(thread_id)["projectId"] == "project-launch"
    assert [
        turn["scope"] for turn in threads.get_thread(thread_id)["turns"]
    ] == [expected_snapshot, expected_snapshot]

    threads.update_thread(thread_id, {"projectId": "project-other"})
    assert [
        turn["scope"] for turn in threads.get_thread(thread_id)["turns"]
    ] == [expected_snapshot, expected_snapshot]

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('conversation-beta', 'source-beta', 'Beta', 'direct')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('conversation-beta', 'message-beta', 'Bob',
                      3000, 'text', 'Beta evidence')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'Beta evidence')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            DELETE FROM project_conversations
            WHERE project_id = 'project-launch'
            """
        )
        connection.execute(
            """
            INSERT INTO project_conversations(
                project_id, conversation_id, created_at, updated_at
            ) VALUES ('project-launch', 'conversation-beta', 2000, 2000)
            """
        )
        connection.commit()
        assert (message.lastrowid, part.lastrowid) == (2, 2)
    finally:
        connection.close()

    retriever.items = [
        AgentEvidence(
            message_id=2,
            part_id=2,
            conversation_id="conversation-beta",
            sender_name="Bob",
            sent_at=3_000,
            excerpt="Beta evidence",
        )
    ]
    changed_events = list(
        service.stream(
            thread_id=thread_id,
            question="What changed now?",
            model_profile_id="local-default",
            scope={"mode": "project", "projectId": "project-launch"},
            remote_send_confirmation=None,
        )
    )
    assert changed_events[-1]["type"] == "done"
    changed_snapshot = {
        "mode": "project",
        "projectId": "project-launch",
        "conversationIds": ["conversation-beta"],
    }
    assert [
        turn["scope"] for turn in threads.get_thread(thread_id)["turns"]
    ] == [
        expected_snapshot,
        expected_snapshot,
        changed_snapshot,
        changed_snapshot,
    ]

    retriever.items = [
        AgentEvidence(
            message_id=1,
            part_id=1,
            conversation_id="conversation-beta",
            sender_name="Alice",
            sent_at=2_000,
            excerpt="Launch scope approved",
        )
    ]
    with pytest.raises(AgentServiceValidationError):
        service.stream(
            thread_id=thread_id,
            question="Mismatched identity?",
            model_profile_id="local-default",
            scope={"mode": "project", "projectId": "project-launch"},
            remote_send_confirmation=None,
        )
    assert len(provider.calls) == 2
    assert len(threads.get_thread(thread_id)["turns"]) == 4

    retriever.items = [
        AgentEvidence(
            message_id=3,
            part_id=3,
            conversation_id="conversation-outside",
            sender_name="Mallory",
            sent_at=3_000,
            excerpt="Outside project",
        )
    ]
    with pytest.raises(AgentServiceValidationError):
        service.stream(
            thread_id=thread_id,
            question="Leak outside?",
            model_profile_id="local-default",
            scope={"mode": "project", "projectId": "project-launch"},
            remote_send_confirmation=None,
        )
    assert len(provider.calls) == 2
    assert len(threads.get_thread(thread_id)["turns"]) == 4


def test_project_run_rejects_message_identity_outside_snapshot(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import SqlKnowledgeScopeResolver
    from wechat_ai.services.agent_thread_service import (
        AgentCitation,
        KnowledgeScopeValidationError,
        normalize_knowledge_scope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "project-citation-boundary.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('conversation-beta', 'source-beta', 'Beta', 'direct')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('conversation-beta', 'message-beta', 'Bob',
                      3000, 'text', 'Outside evidence')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'Outside evidence')
            """,
            (message.lastrowid,),
        )
        connection.execute(
            """
            INSERT INTO projects(id, name, status, created_at, updated_at)
            VALUES ('project-launch', 'Launch', 'active', 1000, 1000)
            """
        )
        connection.execute(
            """
            INSERT INTO project_conversations(
                project_id, conversation_id, created_at, updated_at
            ) VALUES ('project-launch', 'conversation-alpha', 1000, 1000)
            """
        )
        connection.commit()
        assert (message.lastrowid, part.lastrowid) == (2, 2)
    finally:
        connection.close()

    connection_factory = create_connection_factory(database_path)
    resolved = SqlKnowledgeScopeResolver(connection_factory).resolve(
        normalize_knowledge_scope(
            {"mode": "project", "projectId": "project-launch"}
        )
    )
    assert resolved.scope_snapshot is not None
    threads = _make_thread_service(database_path, prefix="citation-boundary")
    run = threads.start_run(
        thread_id=None,
        question="What is the evidence?",
        model_profile_id="local-default",
        scope=resolved.scope_snapshot,
    )

    with pytest.raises(KnowledgeScopeValidationError):
        threads.complete_run(
            run,
            answer="Outside answer",
            citations=[
                AgentCitation(
                    conversation_id="conversation-alpha",
                    message_id=2,
                    part_id=2,
                    sender_name="Bob",
                    sent_at=3_000,
                    excerpt="Outside evidence",
                )
            ],
        )
    threads.cancel_run(run)


def test_empty_project_turn_never_calls_model_or_falls_back_to_global(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentService,
        SqlAgentEvidenceRetriever,
        SqlKnowledgeScopeResolver,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "empty-project-turn.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO projects(id, name, status, created_at, updated_at)
            VALUES ('project-empty', 'Empty', 'active', 1000, 1000)
            """
        )
        connection.commit()
    finally:
        connection.close()

    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="empty-project")
    provider_calls: list[object] = []
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=SqlKnowledgeScopeResolver(connection_factory),
        retriever=SqlAgentEvidenceRetriever(connection_factory),
        provider_factory=lambda profile, credential: provider_calls.append(
            (profile, credential)
        ),
    )

    events = list(
        service.stream(
            thread_id=None,
            question="Anything here?",
            model_profile_id="local-default",
            scope={"mode": "project", "projectId": "project-empty"},
            remote_send_confirmation=None,
        )
    )
    assert provider_calls == []
    assert [event["type"] for event in events] == [
        "status", "delta", "grounding", "done"
    ]
    thread = threads.get_thread(str(events[-1]["threadId"]))
    assert thread["turns"][0]["scope"] == {
        "mode": "project",
        "projectId": "project-empty",
        "conversationIds": [],
    }


def test_explicit_global_turn_on_project_thread_can_use_whole_library(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        SqlKnowledgeScopeResolver,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "project-global-override.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO projects(id, name, status, created_at, updated_at)
            VALUES ('project-empty', 'Empty', 'active', 1000, 1000)
            """
        )
        connection.commit()
    finally:
        connection.close()

    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="global-override")
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    provider = _RecordingProvider(("Global answer",))
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=SqlKnowledgeScopeResolver(connection_factory),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: provider,
    )

    events = list(
        service.stream(
            thread_id=None,
            question="Search everywhere",
            model_profile_id="local-default",
            scope={"mode": "global"},
            project_id="project-empty",
            remote_send_confirmation=None,
        )
    )
    assert events[-2] == {
        "type": "citation",
        "item": evidence.to_citation().to_dict(),
    }
    thread = threads.get_thread(str(events[-1]["threadId"]))
    assert thread["projectId"] == "project-empty"
    assert [turn["scope"] for turn in thread["turns"]] == [
        {"mode": "global"},
        {"mode": "global"},
    ]

def test_agent_orchestrator_refuses_without_evidence_without_calling_model(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-no-evidence.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="no-evidence")
    provider_calls = []
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([]),
        provider_factory=lambda *args: provider_calls.append(args),
    )

    events = list(
        service.stream(
            thread_id=None,
            question="Unknown?",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )

    assert [event["type"] for event in events] == [
        "status", "delta", "grounding", "done"
    ]
    assert events[1]["text"] == "根据本地微信记录，未查找到相关讨论。"
    assert provider_calls == []
    assistant = threads.get_thread(events[-1]["threadId"])["turns"][1]
    assert assistant["status"] == "complete"
    assert assistant["content"] == events[1]["text"]
    assert assistant["citations"] == []
    assert assistant["groundingStatus"] == "insufficient"
    assert assistant["retrievalSummary"] == {
        "retrievedCount": 0,
        "citationCount": 0,
    }


def test_remote_first_send_requires_exact_preview_before_credential_read(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        RemoteSendConfirmationRequired,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "agent-remote.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    model_profiles = _make_model_service(database_path, credentials)
    profile = model_profiles.create_profile(
        name="Remote",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    model_profiles.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )
    credentials.values[profile["id"]] = "synthetic-secret"
    first_evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    retriever = _FixedRetriever([first_evidence])
    provider = _RecordingProvider(("Remote answer",))
    provider_calls = []
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(database_path, prefix="remote"),
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=retriever,
        provider_factory=lambda model, secret: (
            provider_calls.append((model.id, secret)) or provider
        ),
    )

    with pytest.raises(RemoteSendConfirmationRequired) as first_error:
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    preview = first_error.value.preview.to_dict()
    assert preview["destinationHost"] == "models.example"
    assert preview["question"] == "What was approved?"
    assert preview["evidence"] == [first_evidence.to_citation().to_dict()]
    assert credentials.get_calls == []
    assert provider_calls == []
    assert "synthetic-secret" not in str(preview)

    changed_evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved after review",
    )
    retriever.items = [changed_evidence]
    with pytest.raises(RemoteSendConfirmationRequired) as changed_error:
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=preview["fingerprint"],
        )
    changed_preview = changed_error.value.preview.to_dict()
    assert changed_preview["fingerprint"] != preview["fingerprint"]
    assert credentials.get_calls == []

    events = list(
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=changed_preview["fingerprint"],
        )
    )
    assert events[-1]["type"] == "done"
    assert credentials.get_calls == [profile["id"]]
    assert provider_calls == [(profile["id"], "synthetic-secret")]
    assert provider.calls[0][2] == []
    follow_up = list(
        service.stream(
            thread_id=events[-1]["threadId"],
            question="What else?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )
    assert follow_up[-1]["type"] == "done"
    assert provider.calls[1][2] == [
        {"role": "user", "content": "What was approved?"},
        {"role": "assistant", "content": "Remote answer"},
    ]
    assert credentials.get_calls == [profile["id"], profile["id"]]
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            """
            SELECT profile_id FROM remote_model_send_consents
            WHERE profile_id = ?
            """,
            (profile["id"],),
        ).fetchone() == (profile["id"],)
    finally:
        connection.close()


def test_remote_confirmation_is_not_saved_before_request_is_ready(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        RemoteSendConfirmationRequired,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )
    from wechat_ai.services.model_profile_service import (
        ModelCredentialMissing,
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "agent-remote-consent-order.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    model_profiles = _make_model_service(database_path, credentials)
    profile = model_profiles.create_profile(
        name="Remote",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    model_profiles.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(
            database_path,
            prefix="remote-consent-order",
        ),
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _secret: _RecordingProvider(),
    )

    with pytest.raises(RemoteSendConfirmationRequired) as required:
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    with pytest.raises(ModelCredentialMissing):
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=required.value.preview.fingerprint,
        )

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM remote_model_send_consents
            WHERE profile_id = ?
            """,
            (profile["id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    credentials.values[profile["id"]] = "synthetic-secret"
    failing_service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(
            database_path,
            prefix="remote-pre-send-failure",
        ),
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _secret: (_ for _ in ()).throw(
            RuntimeError("request setup failed")
        ),
    )
    with pytest.raises(RuntimeError, match="request setup failed"):
        failing_service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=required.value.preview.fingerprint,
        )

    class PreRequestFailureProvider:
        def stream_answer(self, _question, _contexts, *, history=()):
            del history
            raise RuntimeError("request construction failed")

    pre_request_failure_service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(
            database_path,
            prefix="remote-request-construction-failure",
        ),
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _secret: PreRequestFailureProvider(),
    )
    failed_events = list(
        pre_request_failure_service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=required.value.preview.fingerprint,
        )
    )
    assert failed_events[-1]["type"] == "error"
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM remote_model_send_consents
            WHERE profile_id = ?
            """,
            (profile["id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    with pytest.raises(RemoteSendConfirmationRequired):
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )


def test_agent_stream_cancellation_closes_provider_and_discards_partial_text(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "agent-cancel.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="cancel")
    provider = _RecordingProvider(("partial", "unused"))
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever(
            [
                AgentEvidence(
                    message_id=1,
                    part_id=1,
                    conversation_id="conversation-alpha",
                    sender_name="Alice",
                    sent_at=2_000,
                    excerpt="Launch scope approved",
                )
            ]
        ),
        provider_factory=lambda _profile, _credential: provider,
    )

    events = service.stream(
        thread_id=None,
        question="What was approved?",
        model_profile_id="local-default",
        scope={"mode": "global"},
        remote_send_confirmation=None,
    )
    first_status = next(events)
    assert next(events)["type"] == "status"
    assert next(events) == {"type": "delta", "text": "partial"}
    events.close()

    assert provider.closed is True
    assistant = threads.get_thread(first_status["threadId"])["turns"][1]
    assert assistant["status"] == "cancelled"
    assert assistant["content"] == ""
    assert assistant["citations"] == []


def test_remote_profile_without_evidence_refuses_locally_before_any_ack_gate(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "remote-no-evidence.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    credentials = _CountingCredentials()
    model_profiles = _make_model_service(database_path, credentials)
    profile = model_profiles.create_profile(
        name="Remote unacknowledged",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    provider_calls = []
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=_make_thread_service(
            database_path,
            prefix="remote-empty",
        ),
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([]),
        provider_factory=lambda *args: provider_calls.append(args),
    )

    events = list(
        service.stream(
            thread_id=None,
            question="Unknown?",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )

    assert [event["type"] for event in events] == [
        "status", "delta", "grounding", "done"
    ]
    assert credentials.get_calls == []
    assert provider_calls == []


def test_evidence_retriever_expands_adjacent_context_without_leaving_scope(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        ResolvedKnowledgeScope,
        SqlAgentEvidenceRetriever,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "context.sqlite"
    _seed_agent_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversation_origins(
                conversation_id, source_account_id, external_conversation_id
            ) VALUES ('conversation-alpha', 'wechat:default', 'source-alpha')
            """
        )
        connection.execute(
            """
            INSERT INTO retrieval_chunks(
                message_part_id, message_id, conversation_id, kind,
                text_content, content_hash, index_version,
                sender_name, sent_at
            ) VALUES (1, 1, 'conversation-alpha', 'text',
                      'Launch scope approved', 'hit', 1, 'Alice', 2000)
            """
        )

        def add_context_message(dedupe_key, sender, sent_at, content):
            message = connection.execute(
                """
                INSERT INTO messages(
                    conversation_id, dedupe_key, sender_name,
                    sent_at, type, content
                ) VALUES ('conversation-alpha', ?, ?, ?, 'text', ?)
                """,
                (dedupe_key, sender, sent_at, content),
            )
            part = connection.execute(
                """
                INSERT INTO message_parts(
                    message_id, ordinal, kind, status, text_content
                ) VALUES (?, 0, 'text', 'resolved', ?)
                """,
                (message.lastrowid, content),
            )
            connection.execute(
                """
                INSERT INTO retrieval_chunks(
                    message_part_id, message_id, conversation_id, kind,
                    text_content, content_hash, index_version,
                    sender_name, sent_at
                ) VALUES (?, ?, 'conversation-alpha', 'text', ?, ?, 1, ?, ?)
                """,
                (
                    part.lastrowid,
                    message.lastrowid,
                    content,
                    dedupe_key,
                    sender,
                    sent_at,
                ),
            )
            return int(part.lastrowid)

        before_part = add_context_message(
            "before", "Bob", 1_000, "Before context"
        )
        after_part = add_context_message(
            "after", "Carol", 3_000, "After context"
        )
        connection.commit()
    finally:
        connection.close()

    retriever = SqlAgentEvidenceRetriever(
        create_connection_factory(database_path)
    )
    expanded = retriever.retrieve(
        "Launch scope",
        ResolvedKnowledgeScope(
            mode="selection",
            message_part_ids=(1, before_part, after_part),
        ),
        10,
    )
    assert expanded[0].message_id == 1
    assert expanded[0].part_id == 1
    assert expanded[0].excerpt == (
        "[前文] Bob: Before context\n"
        "[命中] Alice: Launch scope approved\n"
        "[后文] Carol: After context"
    )

    restricted = retriever.retrieve(
        "Launch scope",
        ResolvedKnowledgeScope(
            mode="selection",
            message_part_ids=(1,),
        ),
        10,
    )
    assert restricted[0].excerpt == "Launch scope approved"
    assert "Before context" not in restricted[0].excerpt
    assert "After context" not in restricted[0].excerpt


def test_remote_preview_and_provider_include_bounded_existing_thread_history(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        RemoteSendConfirmationRequired,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.agent_thread_service import AgentCitation
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "remote-existing-history.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    threads = _make_thread_service(database_path, prefix="privacy-history")
    prior = threads.start_run(
        thread_id=None,
        question="Local prior question",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )
    citation = AgentCitation(
        conversation_id="conversation-alpha",
        message_id=1,
        part_id=1,
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    threads.complete_run(
        prior,
        answer="Local prior answer",
        citations=[citation],
    )

    credentials = _CountingCredentials()
    model_profiles = _make_model_service(database_path, credentials)
    profile = model_profiles.create_profile(
        name="Remote",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    model_profiles.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )
    credentials.values[profile["id"]] = "synthetic-secret"
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )
    provider = _RecordingProvider(("Remote answer",))
    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=model_profiles,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: provider,
    )

    with pytest.raises(RemoteSendConfirmationRequired) as first_error:
        service.stream(
            thread_id=prior.thread_id,
            question="Remote question",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    first_fingerprint = first_error.value.preview.fingerprint

    extra = threads.start_run(
        thread_id=prior.thread_id,
        question="Another local question",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )
    threads.complete_run(
        extra,
        answer="Another local answer",
        citations=[citation],
    )
    with pytest.raises(RemoteSendConfirmationRequired) as second_error:
        service.stream(
            thread_id=prior.thread_id,
            question="Remote question",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    assert second_error.value.preview.fingerprint != first_fingerprint
    assert second_error.value.preview.history == (
        {"role": "user", "content": "Local prior question"},
        {"role": "assistant", "content": "Local prior answer"},
        {"role": "user", "content": "Another local question"},
        {"role": "assistant", "content": "Another local answer"},
    )

    events = list(
        service.stream(
            thread_id=prior.thread_id,
            question="Remote question",
            model_profile_id=profile["id"],
            scope={"mode": "global"},
            remote_send_confirmation=second_error.value.preview.fingerprint,
        )
    )
    assert events[-1]["type"] == "done"
    assert provider.calls[0][2] == list(second_error.value.preview.history)


def test_remote_history_keeps_latest_eight_complete_user_assistant_rounds(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.agent_thread_service import normalize_knowledge_scope
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "remote-history-rounds.sqlite"
    _seed_agent_library(database_path)
    threads = _make_thread_service(database_path, prefix="history-round")
    thread_id = None
    for index in range(10):
        run = threads.start_run(
            thread_id=thread_id,
            question=f"question-{index}",
            model_profile_id="local-default",
            scope={"mode": "global"},
        )
        thread_id = run.thread_id
        threads.complete_run(run, answer=f"answer-{index}", citations=[])
    failed = threads.start_run(
        thread_id=thread_id,
        question="failed-question",
        model_profile_id="local-default",
        scope={"mode": "global"},
    )
    threads.fail_run(failed, "MODEL_UNAVAILABLE")

    service = AgentService(
        connection_factory=create_connection_factory(database_path),
        thread_service=threads,
        model_profile_service=_make_model_service(
            database_path,
            _CountingCredentials(),
        ),
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([]),
    )

    history = service._remote_thread_history(  # noqa: SLF001
        thread_id,
        normalize_knowledge_scope({"mode": "global"}),
    )

    assert history == [
        item
        for index in range(2, 10)
        for item in (
            {"role": "user", "content": f"question-{index}"},
            {"role": "assistant", "content": f"answer-{index}"},
        )
    ]


def test_citation_revalidation_filters_sources_that_became_unresolved(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.agent_service import (
        AgentEvidence,
        AgentService,
        ResolvedKnowledgeScope,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "citation-revalidation.sqlite"
    _seed_agent_library(database_path)
    connection_factory = create_connection_factory(database_path)
    models = _make_model_service(database_path, _CountingCredentials())
    threads = _make_thread_service(database_path, prefix="revalidation")
    evidence = AgentEvidence(
        message_id=1,
        part_id=1,
        conversation_id="conversation-alpha",
        sender_name="Alice",
        sent_at=2_000,
        excerpt="Launch scope approved",
    )

    class InvalidatingProvider:
        def stream_answer(self, _question, _contexts, *, history=()):
            del history
            yield "Answer from stale evidence"
            with connection_factory() as connection:
                connection.execute(
                    "UPDATE message_parts SET status = 'unresolved' WHERE id = 1"
                )
                connection.commit()

    service = AgentService(
        connection_factory=connection_factory,
        thread_service=threads,
        model_profile_service=models,
        scope_resolver=_FixedScopeResolver(
            ResolvedKnowledgeScope(mode="global", message_part_ids=None)
        ),
        retriever=_FixedRetriever([evidence]),
        provider_factory=lambda _profile, _credential: InvalidatingProvider(),
    )

    events = list(
        service.stream(
            thread_id=None,
            question="What was approved?",
            model_profile_id="local-default",
            scope={"mode": "global"},
            remote_send_confirmation=None,
        )
    )

    assert "citation" not in [event["type"] for event in events]
    assert next(
        event for event in events if event["type"] == "grounding"
    )["status"] == "invalid"
    thread = threads.get_thread(events[-1]["threadId"])
    assert thread["turns"][1]["citations"] == []
    assert thread["turns"][1]["groundingStatus"] == "invalid"
    result = service.revalidate_citations(
        [evidence],
        ResolvedKnowledgeScope(mode="global", message_part_ids=None),
    )
    assert result.grounding_status == "invalid"
    assert result.evidence == ()
