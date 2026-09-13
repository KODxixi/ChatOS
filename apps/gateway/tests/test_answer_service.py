from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest


def test_answer_stream_orders_deltas_citations_and_done() -> None:
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        AnswerService,
        Evidence,
    )

    evidence = [
        Evidence(
            message_id=12,
            conversation_id="synthetic-room",
            sender_name="Synthetic Sender",
            sent_at=1_700_000_000_000,
            excerpt="Synthetic source text",
        )
    ]
    observed_queries: list[AnswerQuery] = []

    def retrieve(query: AnswerQuery, limit: int) -> list[Evidence]:
        observed_queries.append(query)
        assert limit == 40
        return evidence

    class Provider:
        def stream_answer(
            self,
            question: str,
            contexts: Sequence[Evidence],
        ) -> Iterator[str]:
            assert question == "What was decided?"
            assert list(contexts) == evidence
            yield "The "
            yield "decision."

    query = AnswerQuery(
        question="What was decided?",
        conversation_ids=("synthetic-room",),
    )
    events = list(AnswerService(retrieve, Provider()).stream(query))

    assert observed_queries == [query]
    assert events == [
        {"type": "delta", "text": "The "},
        {"type": "delta", "text": "decision."},
        {
            "type": "citations",
            "items": [
                {
                    "messageId": 12,
                    "conversationId": "synthetic-room",
                    "senderName": "Synthetic Sender",
                    "sentAt": 1_700_000_000_000,
                    "excerpt": "Synthetic source text",
                    "score": None,
                }
            ],
        },
        {"type": "done"},
    ]


def test_answer_without_evidence_does_not_call_model() -> None:
    from wechat_ai.services.answer_service import AnswerQuery, AnswerService

    class Provider:
        def stream_answer(self, *_args: object) -> Iterator[str]:
            raise AssertionError("model must not be called without evidence")
            yield

    events = list(
        AnswerService(lambda _query, _limit: [], Provider()).stream(
            AnswerQuery(question="Unknown question")
        )
    )

    assert events == [
        {
            "type": "delta",
            "text": "根据本地微信记录，未查找到相关讨论。",
        },
        {"type": "citations", "items": []},
        {"type": "done"},
    ]


def test_answer_limits_model_context_and_citations_to_twelve_items() -> None:
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        AnswerService,
        Evidence,
    )

    evidence = [
        Evidence(
            message_id=index,
            conversation_id="synthetic-room",
            sender_name="Sender",
            sent_at=index,
            excerpt=f"Evidence {index}",
        )
        for index in range(13)
    ]
    observed_context_count = 0

    class Provider:
        def stream_answer(
            self,
            _question: str,
            contexts: Sequence[Evidence],
        ) -> Iterator[str]:
            nonlocal observed_context_count
            observed_context_count = len(contexts)
            yield "answer"

    events = list(
        AnswerService(
            lambda _query, limit: evidence[:limit],
            Provider(),
        ).stream(AnswerQuery(question="Question"))
    )

    assert observed_context_count == 12
    citations = next(event for event in events if event["type"] == "citations")
    assert len(citations["items"]) == 12


def test_answer_stream_closes_provider_on_browser_cancellation() -> None:
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        AnswerService,
        Evidence,
    )

    closed = False
    evidence = [
        Evidence(
            message_id=1,
            conversation_id="synthetic-room",
            sender_name="Sender",
            sent_at=1,
            excerpt="Evidence",
        )
    ]

    class Provider:
        def stream_answer(
            self,
            _question: str,
            _contexts: Sequence[Evidence],
        ) -> Iterator[str]:
            nonlocal closed
            try:
                yield "first"
                yield "must-not-arrive"
            finally:
                closed = True

    stream = AnswerService(
        lambda _query, _limit: evidence,
        Provider(),
    ).stream(AnswerQuery(question="Question"))

    assert next(stream) == {"type": "delta", "text": "first"}
    stream.close()

    assert closed is True


def test_model_failure_becomes_sanitized_error_event() -> None:
    from wechat_ai.lm_studio import LmStudioUnavailable
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        AnswerService,
        Evidence,
    )

    evidence = [
        Evidence(
            message_id=1,
            conversation_id="synthetic-room",
            sender_name="Sender",
            sent_at=1,
            excerpt="Evidence",
        )
    ]

    class Provider:
        def stream_answer(self, *_args: object) -> Iterator[str]:
            raise LmStudioUnavailable(
                r"C:\private\model.log synthetic secret"
            )
            yield

    events = list(
        AnswerService(
            lambda _query, _limit: evidence,
            Provider(),
        ).stream(AnswerQuery(question="Question"))
    )

    assert events == [
        {
            "type": "error",
            "error": {
                "code": "LM_OFFLINE",
                "message": "本地模型当前不可用。",
                "retryable": True,
                "details": {},
            },
        }
    ]
    assert "private" not in str(events)
    assert "secret" not in str(events)


def test_sqlite_retriever_applies_scope_and_maps_exact_citations(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            (
                ("alpha", "source-alpha", "Alpha Room", "group"),
                ("beta", "source-beta", "Beta Room", "group"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "alpha",
                    "alpha-1",
                    "Alice",
                    2_000,
                    "text",
                    "launch scope approved",
                ),
                (
                    "alpha",
                    "alpha-2",
                    "Bob",
                    3_000,
                    "text",
                    "launch scope changed",
                ),
                (
                    "beta",
                    "beta-1",
                    "Alice",
                    4_000,
                    "text",
                    "launch scope elsewhere",
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    retrieve = create_evidence_retriever(
        create_connection_factory(database_path)
    )
    evidence = retrieve(
        AnswerQuery(
            question="launch scope",
            conversation_ids=("alpha",),
            sender_name="Alice",
            sent_after=1_500,
            sent_before=2_500,
        ),
        40,
    )

    assert [item.citation() for item in evidence] == [
        {
            "messageId": 1,
            "conversationId": "alpha",
            "senderName": "Alice",
            "sentAt": 2_000,
            "excerpt": "launch scope approved",
            "score": None,
        }
    ]


def test_sqlite_retriever_reranks_candidates_and_exposes_score(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            ("alpha", "source-alpha", "Alpha Room", "group"),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "old", "Alice", 1_000, "text", "launch old"),
                ("alpha", "middle", "Bob", 2_000, "text", "launch middle"),
                ("alpha", "new", "Carol", 3_000, "text", "launch new"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    class Reranker:
        def score_many(
            self,
            query: str,
            documents: Sequence[str],
        ) -> list[float | None]:
            assert query == "launch"
            assert list(documents) == [
                "发送者: Bob\n内容: launch middle",
                "发送者: Alice\n内容: launch old",
                "发送者: Carol\n内容: launch new",
            ]
            return [None, 0.95, 0.10]

    class Embedding:
        def similarities(
            self,
            query: str,
            documents: Sequence[str],
        ) -> list[float]:
            assert query == "launch"
            assert list(documents) == [
                "发送者: Carol\n内容: launch new",
                "发送者: Bob\n内容: launch middle",
                "发送者: Alice\n内容: launch old",
            ]
            return [0.10, 0.90, 0.50]

    evidence = create_evidence_retriever(
        create_connection_factory(database_path),
        embedding=Embedding(),
        reranker=Reranker(),
    )(AnswerQuery(question="launch"), 40)

    assert [item.message_id for item in evidence] == [1, 3, 2]
    assert [item.relevance_score for item in evidence] == [0.95, 0.10, None]
    assert evidence[0].citation()["score"] == pytest.approx(0.95)


def test_sqlite_retriever_falls_back_to_time_order_when_reranker_fails(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )
    from wechat_ai.services.reranker_service import RerankerUnavailable
    from wechat_ai.services.embedding_service import EmbeddingUnavailable

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            ("alpha", "source-alpha", "Alpha Room", "group"),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "old", "Alice", 1_000, "text", "launch old"),
                ("alpha", "middle", "Bob", 2_000, "text", "launch middle"),
                ("alpha", "new", "Carol", 3_000, "text", "launch new"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    class FailingEmbedding:
        def similarities(
            self,
            _query: str,
            _documents: Sequence[str],
        ) -> list[float]:
            raise EmbeddingUnavailable("synthetic failure")

    class FailingReranker:
        def score_many(self, _query: str, _documents: Sequence[str]) -> list[float]:
            raise RerankerUnavailable("synthetic failure")

    evidence = create_evidence_retriever(
        create_connection_factory(database_path),
        embedding=FailingEmbedding(),
        reranker=FailingReranker(),
    )(AnswerQuery(question="launch"), 40)

    assert [item.message_id for item in evidence] == [3, 2, 1]
    assert [item.relevance_score for item in evidence] == [None, None, None]
    assert [item.citation()["score"] for item in evidence] == [None, None, None]


def test_sqlite_retriever_recalls_direct_conversation_named_in_question(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            (
                ("cai", "source-cai", "蔡敏", "direct"),
                ("other", "source-other", "其他人", "direct"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("cai", "cai-1", "蔡敏", 2_000, "text", "想请你帮我装软件"),
                ("cai", "cai-2", "Me", 3_000, "text", "明天给你装"),
                ("other", "other-1", "其他人", 4_000, "text", "无关消息"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    class Reranker:
        def score_many(self, _query: str, documents: Sequence[str]) -> list[float]:
            assert list(documents) == [
                "发送者: Me\n内容: 明天给你装",
                "发送者: 蔡敏\n内容: 想请你帮我装软件",
            ]
            return [0.80, 0.95]

    evidence = create_evidence_retriever(
        create_connection_factory(database_path),
        reranker=Reranker(),
    )(AnswerQuery(question="蔡敏昨天找我干嘛"), 40)

    assert [item.message_id for item in evidence] == [1, 2]
    assert [item.relevance_score for item in evidence] == [0.95, 0.80]


def test_sqlite_retriever_merges_multiple_conversations_in_time_order(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            (
                ("alpha", "source-alpha", "Alpha Room", "group"),
                ("beta", "source-beta", "Beta Room", "group"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "alpha-1", "Alice", 2_000, "text", "launch alpha"),
                ("beta", "beta-1", "Bob", 4_000, "text", "launch beta"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = create_evidence_retriever(
        create_connection_factory(database_path)
    )(
        AnswerQuery(
            question="launch",
            conversation_ids=("alpha", "beta"),
        ),
        1,
    )

    assert [(item.conversation_id, item.sent_at) for item in evidence] == [
        ("beta", 4_000)
    ]


def test_sqlite_retriever_falls_back_to_ground_chinese_question(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.answer_service import (
        AnswerQuery,
        create_evidence_retriever,
    )
    from wechat_ai.services.conversation_service import (
        create_connection_factory,
    )

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            ("alpha", "source-alpha", "Alpha Room", "group"),
        )
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "alpha",
                "alpha-zh",
                "Alice",
                5_000,
                "text",
                "首版先做会话浏览和引用提问",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = create_evidence_retriever(
        create_connection_factory(database_path)
    )(
        AnswerQuery(
            question="首版范围是什么？",
            conversation_ids=("alpha",),
        ),
        40,
    )

    assert [(item.message_id, item.excerpt) for item in evidence] == [
        (1, "首版先做会话浏览和引用提问")
    ]


def test_sqlite_retriever_bounds_searches_for_maximum_cjk_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wechat_ai.services import answer_service

    question = "".join(chr(0x4E00 + index) for index in range(4_000))
    conversation_ids = tuple(
        f"synthetic-conversation-{index}"
        for index in range(50)
    )
    observed_calls: list[dict[str, object]] = []
    maximum_calls = 17

    def counted_search(
        _connection: sqlite3.Connection,
        **kwargs: object,
    ) -> dict[str, object]:
        observed_calls.append(kwargs)
        assert len(observed_calls) <= maximum_calls, (
            f"search_messages exceeded {maximum_calls} calls"
        )
        return {"items": [], "nextCursor": None}

    monkeypatch.setattr(answer_service, "search_messages", counted_search)
    retrieve = answer_service.create_evidence_retriever(
        lambda: sqlite3.connect(":memory:")
    )

    evidence = retrieve(
        answer_service.AnswerQuery(
            question=question,
            conversation_ids=conversation_ids,
        ),
        40,
    )

    assert evidence == []
    assert 1 <= len(observed_calls) <= maximum_calls
    assert all(
        call["conversation_ids"] == conversation_ids
        for call in observed_calls
    )
    assert all(
        call["conversation_id"] is None
        for call in observed_calls
    )
