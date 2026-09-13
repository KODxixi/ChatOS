from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

from wechat_ai.database import initialize_database
from wechat_ai.services.agent_service import (
    Fts5RetrieverBackend,
    RetrievalRequest,
    ResolvedKnowledgeScope,
    SqlAgentEvidenceRetriever,
)
from wechat_ai.services.conversation_service import create_connection_factory


def _create_library(path: Path) -> None:
    connection = initialize_database(path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('conversation-alpha', 'source-alpha', 'Alpha', 'group')
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_retrieval_fact(
    connection: sqlite3.Connection,
    *,
    dedupe_key: str,
    content: str,
    sent_at: int,
    kind: str = "text",
) -> int:
    message = connection.execute(
        """
        INSERT INTO messages(
            conversation_id, dedupe_key, sender_name,
            sent_at, type, content
        ) VALUES ('conversation-alpha', ?, 'Synthetic', ?, ?, ?)
        """,
        (dedupe_key, sent_at, kind, content),
    )
    part = connection.execute(
        """
        INSERT INTO message_parts(
            message_id, ordinal, kind, status, text_content
        ) VALUES (?, 0, ?, 'resolved', ?)
        """,
        (message.lastrowid, kind, content),
    )
    connection.execute(
        """
        INSERT INTO retrieval_chunks(
            message_part_id, message_id, conversation_id, kind,
            text_content, content_hash, index_version,
            sender_name, sent_at
        ) VALUES (?, ?, 'conversation-alpha', ?, ?, ?, 1, 'Synthetic', ?)
        """,
        (
            part.lastrowid,
            message.lastrowid,
            kind,
            content,
            dedupe_key,
            sent_at,
        ),
    )
    return int(part.lastrowid)


def test_fts_relevance_beats_recency_and_respects_resolved_scope(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "fts-ranking.sqlite"
    _create_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        recent_noise = _insert_retrieval_fact(
            connection,
            dedupe_key="recent-noise",
            content="launch routine status update",
            sent_at=9_000,
        )
        older_relevant = _insert_retrieval_fact(
            connection,
            dedupe_key="older-relevant",
            content="Atlas budget settled after launch decision approved",
            sent_at=1_000,
        )
        newer_relevant = _insert_retrieval_fact(
            connection,
            dedupe_key="newer-relevant",
            content="Atlas budget settled after launch decision reviewed",
            sent_at=2_000,
        )
        excluded_strongest = _insert_retrieval_fact(
            connection,
            dedupe_key="excluded-strongest",
            content="Atlas Atlas launch decision launch decision",
            sent_at=10_000,
        )
        connection.commit()
    finally:
        connection.close()

    evidence = SqlAgentEvidenceRetriever(
        create_connection_factory(database_path)
    ).retrieve(
        "launch atlas decision",
        ResolvedKnowledgeScope(
            mode="selection",
            message_part_ids=(
                recent_noise,
                older_relevant,
                newer_relevant,
            ),
        ),
        3,
    )

    assert [item.part_id for item in evidence] == [
        newer_relevant,
        older_relevant,
        recent_noise,
    ]
    assert excluded_strongest not in {item.part_id for item in evidence}


def test_fts_backend_returns_ranked_retrieval_contract(tmp_path: Path) -> None:
    database_path = tmp_path / "fts-contract.sqlite"
    _create_library(database_path)
    with sqlite3.connect(database_path) as connection:
        relevant = _insert_retrieval_fact(
            connection,
            dedupe_key="contract-relevant",
            content="Atlas budget decision approved",
            sent_at=1_000,
        )
        connection.commit()

    backend = Fts5RetrieverBackend(
        SqlAgentEvidenceRetriever(create_connection_factory(database_path))
    )
    result = backend.retrieve(
        RetrievalRequest(
            question="Atlas budget decision",
            scope=ResolvedKnowledgeScope(
                mode="selection",
                message_part_ids=(relevant,),
            ),
            limit=40,
        )
    )

    assert result.backend == "fts5-v1"
    assert result.index_version == 1
    assert result.candidate_count == 1
    assert [(hit.rank, hit.evidence.part_id) for hit in result.hits] == [(1, relevant)]
    assert result.hits[0].score is not None


def test_cjk_fallback_aggregates_matches_and_keeps_no_evidence_empty(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cjk-ranking.sqlite"
    _create_library(database_path)
    connection = sqlite3.connect(database_path)
    try:
        recent_noise = _insert_retrieval_fact(
            connection,
            dedupe_key="cjk-recent-noise",
            content="项目日常更新",
            sent_at=9_000,
        )
        relevant = _insert_retrieval_fact(
            connection,
            dedupe_key="cjk-relevant",
            content="项目讨论后确认预算与结论",
            sent_at=1_000,
        )
        connection.commit()
    finally:
        connection.close()

    retriever = SqlAgentEvidenceRetriever(
        create_connection_factory(database_path)
    )
    scope = ResolvedKnowledgeScope(
        mode="selection",
        message_part_ids=(recent_noise, relevant),
    )

    evidence = retriever.retrieve("项目预算结论", scope, 2)

    assert [item.part_id for item in evidence] == [relevant]
    assert retriever.retrieve("不存在的火星代号", scope, 2) == []


def test_relative_day_filter_uses_asia_shanghai_calendar_boundaries(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "shanghai-time.sqlite"
    _create_library(database_path)
    shanghai = timezone(timedelta(hours=8), name="Asia/Shanghai")
    now_ms = int(
        datetime(2026, 8, 2, 0, 30, tzinfo=shanghai).timestamp() * 1000
    )
    yesterday_ms = int(
        datetime(2026, 8, 1, 23, 59, tzinfo=shanghai).timestamp() * 1000
    )
    today_ms = int(
        datetime(2026, 8, 2, 0, 1, tzinfo=shanghai).timestamp() * 1000
    )
    connection = sqlite3.connect(database_path)
    try:
        yesterday = _insert_retrieval_fact(
            connection,
            dedupe_key="yesterday-budget",
            content="预算昨天已讨论",
            sent_at=yesterday_ms,
        )
        today = _insert_retrieval_fact(
            connection,
            dedupe_key="today-budget",
            content="预算今天已确认",
            sent_at=today_ms,
        )
        connection.commit()
    finally:
        connection.close()

    evidence = SqlAgentEvidenceRetriever(
        create_connection_factory(database_path),
        clock=lambda: now_ms,
    ).retrieve(
        "今天预算有什么变化？",
        ResolvedKnowledgeScope(
            mode="selection",
            message_part_ids=(yesterday, today),
        ),
        10,
    )

    assert [item.part_id for item in evidence] == [today]
