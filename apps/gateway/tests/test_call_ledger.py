from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _database(tmp_path: Path) -> tuple[Path, str]:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_gateway_runtime import AgentRunService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "call-ledger.sqlite"
    initialize_database(database_path).close()
    factory = create_connection_factory(database_path)
    run = AgentRunService(
        factory,
        clock=lambda: 1_000,
        id_factory=lambda: "run-1",
    ).create_run(
        request={"question": "private question", "scope": {"mode": "global"}},
        source="http",
    )
    return database_path, str(run["id"])


def test_call_ledger_records_metadata_and_evidence_without_content(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.call_ledger_service import (
        CallEvidence,
        CallLedgerService,
    )
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path, run_id = _database(tmp_path)
    now = [2_000]
    ledger = CallLedgerService(
        create_connection_factory(database_path),
        clock=lambda: now[0],
        id_factory=lambda: "span-1",
    )

    span_id = ledger.start_span(
        run_id=run_id,
        kind="retrieval",
        operation="fts5-v1",
        metadata={"backend": "fts5-v1", "indexVersion": 1},
    )
    now[0] = 2_125
    ledger.finish_span(
        span_id,
        candidate_count=4,
        citation_count=1,
        evidence=(
            CallEvidence(
                message_id=11,
                part_id=12,
                role="candidate",
                rank=1,
                score=-0.8,
            ),
            CallEvidence(
                message_id=11,
                part_id=12,
                role="citation",
                rank=1,
                score=-0.8,
            ),
        ),
    )

    page = ledger.list_run_spans(run_id)
    assert page["retentionDays"] == 90
    assert page["items"] == [
        {
            "id": "span-1",
            "runId": run_id,
            "parentSpanId": None,
            "kind": "retrieval",
            "operation": "fts5-v1",
            "status": "completed",
            "modelProfileId": None,
            "startedAt": 2_000,
            "finishedAt": 2_125,
            "durationMs": 125,
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "candidateCount": 4,
            "citationCount": 1,
            "errorCode": None,
            "metadata": {"backend": "fts5-v1", "indexVersion": 1},
            "evidence": [
                {
                    "messageId": 11,
                    "partId": 12,
                    "role": "candidate",
                    "rank": 1,
                    "score": -0.8,
                },
                {
                    "messageId": 11,
                    "partId": 12,
                    "role": "citation",
                    "rank": 1,
                    "score": -0.8,
                },
            ],
        }
    ]
    serialized = json.dumps(page, ensure_ascii=False)
    assert "private question" not in serialized
    assert "excerpt" not in serialized


def test_call_ledger_rejects_non_allowlisted_metadata(tmp_path: Path) -> None:
    from wechat_ai.services.call_ledger_service import CallLedgerService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path, run_id = _database(tmp_path)
    ledger = CallLedgerService(create_connection_factory(database_path))

    span_id = ledger.start_span(
        run_id=run_id,
        kind="model",
        operation="chat.completions",
        metadata={"prompt": "must not persist", "endpoint": "https://example.test"},
    )

    assert span_id is None
    assert ledger.health()["status"] == "degraded"
    assert ledger.list_run_spans(run_id)["items"] == []


def test_call_ledger_prunes_only_expired_finished_spans(tmp_path: Path) -> None:
    from wechat_ai.services.call_ledger_service import CallLedgerService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path, run_id = _database(tmp_path)
    now = [1_000]
    identifiers = iter(("expired", "active", "fresh"))
    ledger = CallLedgerService(
        create_connection_factory(database_path),
        clock=lambda: now[0],
        id_factory=lambda: next(identifiers),
        retention_days=1,
    )
    expired = ledger.start_span(
        run_id=run_id,
        kind="model",
        operation="chat.completions",
    )
    ledger.finish_span(expired)
    active = ledger.start_span(
        run_id=run_id,
        kind="retrieval",
        operation="fts5-v1",
    )
    now[0] = 86_401_001
    fresh = ledger.start_span(
        run_id=run_id,
        kind="model",
        operation="chat.completions",
    )
    ledger.finish_span(fresh)

    assert ledger.prune_expired() == 1
    assert [item["id"] for item in ledger.list_run_spans(run_id)["items"]] == [
        "active",
        "fresh",
    ]


def test_call_ledger_runs_retention_cleanup_at_most_once_per_day(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.call_ledger_service import CallLedgerService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path, run_id = _database(tmp_path)
    now = [200_000_000]
    identifiers = iter(("old-1", "old-2"))
    ledger = CallLedgerService(
        create_connection_factory(database_path),
        clock=lambda: now[0],
        id_factory=lambda: next(identifiers),
    )

    first = ledger.start_span(
        run_id=run_id,
        kind="model",
        operation="chat.completions",
    )
    ledger.finish_span(first)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agent_call_spans SET expires_at = 1 WHERE id = ?",
            (first,),
        )
    assert ledger.prune_if_due() == 1

    second = ledger.start_span(
        run_id=run_id,
        kind="model",
        operation="chat.completions",
    )
    ledger.finish_span(second)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agent_call_spans SET expires_at = 1 WHERE id = ?",
            (second,),
        )
    assert ledger.prune_if_due() == 0
    now[0] += 24 * 60 * 60 * 1000
    assert ledger.prune_if_due() == 1
