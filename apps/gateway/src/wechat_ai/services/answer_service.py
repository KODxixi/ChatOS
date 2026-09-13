from __future__ import annotations

import sqlite3
import re
import math
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from typing import Protocol

from ..lm_studio import LmStudioUnavailable
from .conversation_service import ConnectionFactory
from .embedding_service import EmbeddingUnavailable
from .reranker_service import RerankerUnavailable
from .search_service import InvalidSearchQuery, search_messages


_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_MAX_FALLBACK_CANDIDATES = 16
_MAX_RERANKED_EVIDENCE = 12
_MAX_RERANK_CANDIDATES = 20
_QUESTION_CHARACTERS = frozenset("请问是什么怎么为何哪多少否吗呢的了")
_ENGLISH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "did",
    "do",
    "does",
    "for",
    "how",
    "is",
    "of",
    "the",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
}


@dataclass(frozen=True, slots=True)
class AnswerQuery:
    question: str
    conversation_ids: tuple[str, ...] = ()
    sender_name: str | None = None
    sent_after: int | None = None
    sent_before: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    message_id: int
    conversation_id: str
    sender_name: str
    sent_at: int
    excerpt: str
    relevance_score: float | None = None

    @property
    def score(self) -> float | None:
        return self.relevance_score

    def citation(self) -> dict[str, object]:
        return {
            "messageId": self.message_id,
            "conversationId": self.conversation_id,
            "senderName": self.sender_name,
            "sentAt": self.sent_at,
            "excerpt": self.excerpt,
            "score": self.relevance_score,
        }


class AnswerProvider(Protocol):
    def stream_answer(
        self,
        question: str,
        contexts: Sequence[Evidence],
    ) -> Iterator[str]: ...


EvidenceRetriever = Callable[[AnswerQuery, int], Sequence[Evidence]]


class EvidenceReranker(Protocol):
    def score_many(
        self,
        query: str,
        documents: Sequence[str],
    ) -> Sequence[float | None]: ...


class EvidenceEmbedding(Protocol):
    def similarities(
        self,
        query: str,
        documents: Sequence[str],
    ) -> Sequence[float]: ...


class AnswerService:
    def __init__(
        self,
        retriever: EvidenceRetriever,
        provider: AnswerProvider | None,
    ) -> None:
        self._retriever = retriever
        self._provider = provider

    def stream(self, query: AnswerQuery) -> Iterator[dict[str, object]]:
        evidence = list(self._retriever(query, 40))[:12]
        if not evidence:
            yield {
                "type": "delta",
                "text": "根据本地微信记录，未查找到相关讨论。",
            }
            yield {"type": "citations", "items": []}
            yield {"type": "done"}
            return

        upstream: Iterator[str] | None = None
        try:
            upstream = iter(
                self._provider.stream_answer(query.question, evidence)
            )
            for delta in upstream:
                if delta:
                    yield {"type": "delta", "text": delta}
        except LmStudioUnavailable:
            yield {
                "type": "error",
                "error": {
                    "code": "LM_OFFLINE",
                    "message": "本地模型当前不可用。",
                    "retryable": True,
                    "details": {},
                },
            }
            return
        finally:
            close = getattr(upstream, "close", None)
            if callable(close):
                close()

        yield {
            "type": "citations",
            "items": [item.citation() for item in evidence],
        }
        yield {"type": "done"}

    def stream_evidence(
        self,
        query: AnswerQuery,
    ) -> Iterator[dict[str, object]]:
        """Stream ranked evidence for an external answer generator.

        Gchat/OpenClaw owns final language generation.  This path deliberately
        never touches a local generation model, so retrieval cannot accidentally
        select an embedding, reranker, or VL model as an answer provider.
        """
        evidence = list(self._retriever(query, 40))[:12]
        yield {
            "type": "citations",
            "items": [item.citation() for item in evidence],
        }
        yield {"type": "done"}


def create_evidence_retriever(
    connection_factory: ConnectionFactory,
    *,
    embedding: EvidenceEmbedding | None = None,
    reranker: EvidenceReranker | None = None,
) -> EvidenceRetriever:
    def retrieve(query: AnswerQuery, limit: int) -> list[Evidence]:
        items_by_id: dict[int, dict[str, object]] = {}
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            for retrieval_query in _retrieval_queries(query.question):
                candidate_items: dict[int, dict[str, object]] = {}
                try:
                    page = search_messages(
                        connection,
                        query=retrieval_query,
                        conversation_id=None,
                        conversation_ids=query.conversation_ids or None,
                        sender_name=query.sender_name,
                        sent_at_from=query.sent_after,
                        sent_at_to=query.sent_before,
                        limit=limit,
                        cursor=None,
                    )
                    for item in page["items"]:
                        candidate_items[int(item["id"])] = item
                except InvalidSearchQuery:
                    continue
                if candidate_items:
                    items_by_id = candidate_items
                    break
            for item in _conversation_title_items(connection, query, limit):
                items_by_id[int(item["id"])] = item

        ranked = sorted(
            items_by_id.values(),
            key=lambda item: (int(item["sentAt"]), int(item["id"])),
            reverse=True,
        )[:limit]
        time_ranked = [
            Evidence(
                message_id=int(item["id"]),
                conversation_id=str(item["conversationId"]),
                sender_name=str(item["senderName"]),
                sent_at=int(item["sentAt"]),
                excerpt=str(item["content"])[:500],
            )
            for item in ranked
        ]
        if not time_ranked:
            return time_ranked[:_MAX_RERANKED_EVIDENCE]

        documents = [
            f"发送者: {item.sender_name}\n内容: {item.excerpt}"
            for item in time_ranked
        ]
        embedding_ranked = time_ranked
        if embedding is not None:
            try:
                similarities = list(
                    embedding.similarities(query.question, documents)
                )
            except EmbeddingUnavailable:
                similarities = []
            if len(similarities) == len(time_ranked) and all(
                not isinstance(score, bool)
                and isinstance(score, (int, float))
                and math.isfinite(float(score))
                for score in similarities
            ):
                embedding_ranked = [
                    item
                    for item, _score in sorted(
                        zip(time_ranked, similarities, strict=True),
                        key=lambda pair: float(pair[1]),
                        reverse=True,
                    )
                ]

        if reranker is None:
            return embedding_ranked[:_MAX_RERANKED_EVIDENCE]

        rerank_candidates = embedding_ranked[:_MAX_RERANK_CANDIDATES]
        try:
            scores = list(
                reranker.score_many(
                    query.question,
                    [
                        f"发送者: {item.sender_name}\n内容: {item.excerpt}"
                        for item in rerank_candidates
                    ],
                )
            )
        except RerankerUnavailable:
            return time_ranked[:_MAX_RERANKED_EVIDENCE]

        if len(scores) != len(rerank_candidates) or any(
            score is not None
            and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            )
            for score in scores
        ):
            return time_ranked[:_MAX_RERANKED_EVIDENCE]
        if not any(score is not None for score in scores):
            return time_ranked[:_MAX_RERANKED_EVIDENCE]

        scored = [
            replace(
                item,
                relevance_score=(
                    float(score) if score is not None else None
                ),
            )
            for item, score in zip(rerank_candidates, scores, strict=True)
        ]
        return sorted(
            scored,
            key=lambda item: (
                item.relevance_score is not None,
                float(item.relevance_score or 0.0),
            ),
            reverse=True,
        )[:_MAX_RERANKED_EVIDENCE]

    return retrieve


def _conversation_title_items(
    connection: sqlite3.Connection,
    query: AnswerQuery,
    limit: int,
) -> list[dict[str, object]]:
    title_where = [
        "LENGTH(TRIM(title)) >= 2",
        "INSTR(?, TRIM(title)) > 0",
        "is_indexing_enabled = 1",
    ]
    title_parameters: list[object] = [query.question]
    if query.conversation_ids:
        placeholders = ", ".join("?" for _item in query.conversation_ids)
        title_where.append(f"id IN ({placeholders})")
        title_parameters.extend(query.conversation_ids)
    try:
        conversation_rows = connection.execute(
            f"""
            SELECT id
            FROM conversations
            WHERE {" AND ".join(title_where)}
            ORDER BY LENGTH(TRIM(title)) DESC, id ASC
            LIMIT 4
            """,
            title_parameters,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    conversation_ids = [str(row["id"]) for row in conversation_rows]
    if not conversation_ids:
        return []

    placeholders = ", ".join("?" for _item in conversation_ids)
    message_where = [f"conversation_id IN ({placeholders})"]
    message_parameters: list[object] = list(conversation_ids)
    if query.sender_name is not None:
        message_where.append("sender_name = ?")
        message_parameters.append(query.sender_name)
    if query.sent_after is not None:
        message_where.append("sent_at >= ?")
        message_parameters.append(query.sent_after)
    if query.sent_before is not None:
        message_where.append("sent_at <= ?")
        message_parameters.append(query.sent_before)
    message_parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT id, conversation_id, sender_name, sent_at, content
        FROM messages
        WHERE {" AND ".join(message_where)}
        ORDER BY sent_at DESC, id DESC
        LIMIT ?
        """,
        message_parameters,
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "conversationId": str(row["conversation_id"]),
            "senderName": str(row["sender_name"]),
            "sentAt": int(row["sent_at"]),
            "content": str(row["content"]),
        }
        for row in rows
    ]


def _retrieval_queries(question: str) -> list[str]:
    candidates = [question]
    cjk_characters = "".join(_CJK_CHARACTER.findall(question))
    if cjk_characters:
        seen: set[str] = set()
        fallback_count = 0
        for index in range(max(0, len(cjk_characters) - 1)):
            bigram = cjk_characters[index : index + 2]
            if (
                len(bigram) == 2
                and not any(
                    character in _QUESTION_CHARACTERS
                    for character in bigram
                )
                and bigram not in seen
            ):
                candidates.append(bigram)
                seen.add(bigram)
                fallback_count += 1
                if fallback_count >= _MAX_FALLBACK_CANDIDATES:
                    break
        return candidates

    fallback_count = 0
    for term in re.findall(r"[A-Za-z0-9_]+", question.casefold()):
        if (
            len(term) >= 3
            and term not in _ENGLISH_STOP_WORDS
            and term not in candidates
        ):
            candidates.append(term)
            fallback_count += 1
            if fallback_count >= _MAX_FALLBACK_CANDIDATES:
                break
    return candidates
