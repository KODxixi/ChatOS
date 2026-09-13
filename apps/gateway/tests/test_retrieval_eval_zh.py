from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from wechat_ai.database import initialize_database
from wechat_ai.services.agent_service import (
    ResolvedKnowledgeScope,
    SqlAgentEvidenceRetriever,
)
from wechat_ai.services.conversation_service import create_connection_factory


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "retrieval_eval_zh.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _build_library(path: Path, fixture: dict[str, object]) -> dict[str, int]:
    connection = initialize_database(path)
    connection.row_factory = sqlite3.Row
    part_ids: dict[str, int] = {}
    try:
        for conversation_id in ("conversation-alpha", "conversation-beta"):
            connection.execute(
                """
                INSERT INTO conversations(
                    id, source_conversation_id, title, kind
                ) VALUES (?, ?, ?, 'group')
                """,
                (conversation_id, conversation_id, conversation_id),
            )
        for raw_message in fixture["messages"]:
            message = dict(raw_message)
            sent_at = int(datetime.fromisoformat(str(message["sentAt"])).timestamp() * 1000)
            inserted = connection.execute(
                """
                INSERT INTO messages(
                    conversation_id, dedupe_key, sender_name,
                    sent_at, type, content
                ) VALUES (?, ?, 'Synthetic', ?, 'text', ?)
                """,
                (
                    message["conversation"],
                    message["id"],
                    sent_at,
                    message["content"],
                ),
            )
            part = connection.execute(
                """
                INSERT INTO message_parts(
                    message_id, ordinal, kind, status, text_content
                ) VALUES (?, 0, 'text', 'resolved', ?)
                """,
                (inserted.lastrowid, message["content"]),
            )
            connection.execute(
                """
                INSERT INTO retrieval_chunks(
                    message_part_id, message_id, conversation_id, kind,
                    text_content, content_hash, index_version,
                    sender_name, sent_at
                ) VALUES (?, ?, ?, 'text', ?, ?, 1, 'Synthetic', ?)
                """,
                (
                    part.lastrowid,
                    inserted.lastrowid,
                    message["conversation"],
                    message["content"],
                    message["id"],
                    sent_at,
                ),
            )
            part_ids[str(message["id"])] = int(part.lastrowid)
        connection.commit()
        return part_ids
    finally:
        connection.close()


def test_committed_chinese_retrieval_evaluation_meets_beta_gates(
    tmp_path: Path,
) -> None:
    fixture = _load_fixture()
    database_path = tmp_path / "retrieval-eval.sqlite"
    part_ids = _build_library(database_path, fixture)
    ids_by_part = {value: key for key, value in part_ids.items()}
    now_ms = int(datetime.fromisoformat(str(fixture["now"])).timestamp() * 1000)
    retriever = SqlAgentEvidenceRetriever(
        create_connection_factory(database_path),
        clock=lambda: now_ms,
    )

    relevant_retrieved = 0
    total_retrieved = 0
    refusal_total = 0
    refusal_correct = 0
    exact_cases = 0
    mismatches: list[tuple[str, list[str], set[str]]] = []
    cases = list(fixture["cases"])
    for raw_case in cases:
        case = dict(raw_case)
        scope_ids = tuple(part_ids[item] for item in case["scope"])
        result = retriever.retrieve(
            str(case["question"]),
            ResolvedKnowledgeScope(
                mode="selection",
                message_part_ids=scope_ids,
            ),
            12,
        )
        result_ids = [ids_by_part[item.part_id] for item in result]
        expected = set(case["relevant"])
        if not expected:
            refusal_total += 1
            refusal_correct += int(not result_ids)
        relevant_retrieved += sum(item in expected for item in result_ids)
        total_retrieved += len(result_ids)
        exact_cases += int(set(result_ids) == expected)
        if set(result_ids) != expected:
            mismatches.append((str(case["id"]), result_ids, expected))

    precision = relevant_retrieved / max(total_retrieved, 1)
    refusal_rate = refusal_correct / max(refusal_total, 1)
    exact_rate = exact_cases / len(cases)

    assert exact_rate == 1.0, mismatches
    assert refusal_rate >= 0.95
    assert precision >= 0.80
