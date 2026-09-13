from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError

from ..openai_compatible import OpenAICompatibleClient
from ..rag_prompt import _format_sent_at
from .model_profile_service import (
    ModelProfile,
    ModelProfileService,
)


SEMANTIC_GRAPH_VERSION = 1
MAX_LABELS_PER_KIND = 8
MAX_LABEL_LENGTH = 64
MAX_RESPONSE_CHARACTERS = 65_536
DEFAULT_BATCH_SIZE = 20
# Group chats whose distinct speaker count exceeds this are treated as noise:
# their chunks are marked semantically complete up front with no model call,
# so a few huge rooms cannot dominate a full-library enrichment run.
LARGE_GROUP_MIN_SPEAKERS = 30

_SEMANTIC_SYSTEM_PROMPT = (
    "You are a strict JSON information-extraction engine running locally on "
    "private WeChat chat history. Your ONLY output is a single JSON document; "
    "never add prose, markdown fences, explanations, or greetings. For every "
    "evidence block you are given, identify its exact part id from the block "
    "header and echo that value verbatim as messagePartId."
)

_SEMANTIC_EXTRACTION_TASK = (
    "Extract concise topics and named entities from every supplied chat "
    "evidence item. Return only strict JSON with this exact shape: "
    '{"items":[{"messagePartId":1,"topics":["..."],'
    '"entities":["..."]}]}. The messagePartId of each item MUST equal the '
    "integer shown in that block's header (for example a block headed "
    "`part_id: 39 | part=39` must be reported as messagePartId 39). "
    'Never use the block\'s list position as messagePartId. '
    "Omit an item only when its block has no topic or entity. "
    "Hard limits that must never be exceeded: at most 8 topics per item, "
    "at most 8 entities per item, each label at most 64 characters. "
    "When a block contains more entities than the limit, keep only the "
    "8 most relevant and drop the rest; never emit a ninth topic or entity."
)


def _build_semantic_user_prompt(
    question: str,
    contexts: Sequence[object],
) -> str:
    blocks: list[str] = []
    for index, item in enumerate(contexts, start=1):
        part_id = getattr(item, "part_id", None)
        part_label = (
            f" | part_id: {part_id} | part={part_id}"
            if part_id is not None
            else ""
        )
        sent_at = getattr(item, "sent_at", None)
        sender = getattr(item, "sender_name", "未知")
        excerpt = getattr(item, "excerpt", "")
        blocks.append(
            f"[Block {index}] 时间: {_format_sent_at(sent_at)}"
            f" | 发送者: {sender}{part_label}\n"
            f"内容: {excerpt}"
        )
    context_text = "\n\n".join(blocks) or "（无检索结果）"
    return (
        "------------------- [语义抽取上下文开始] -------------------\n"
        f"{context_text}\n"
        "------------------- [语义抽取上下文结束] -------------------\n\n"
        f"任务：{question}\n"
        "只输出一个 JSON 文档。messagePartId 必须逐字等于块头 part_id 的整数。"
    )


Clock = Callable[[], int]


class SemanticGraphProvider(Protocol):
    def stream_answer(
        self,
        question: str,
        contexts: Sequence[object],
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> Iterator[str]: ...


ProviderFactory = Callable[
    [ModelProfile, str | None],
    SemanticGraphProvider,
]


@dataclass(frozen=True, slots=True)
class SemanticGraphResult:
    processed_chunks: int
    ready_accounts: int
    degraded_accounts: int
    skipped: bool


@dataclass(frozen=True, slots=True)
class _Chunk:
    id: int
    message_part_id: int
    message_id: int
    conversation_id: str
    source_account_id: str
    content_hash: str
    text_content: str
    sender_name: str
    sent_at: int


@dataclass(frozen=True, slots=True)
class _ChunkEvidence:
    message_id: int
    part_id: int
    conversation_id: str
    sender_name: str
    sent_at: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class _SemanticItem:
    message_part_id: int
    topics: tuple[tuple[str, str], ...]
    entities: tuple[tuple[str, str], ...]


class _InvalidModelOutput(ValueError):
    pass


class _SemanticBatchSkip(RuntimeError):
    """Raised when a batch cannot be parsed after retries; the batch is
    marked complete without graph facts and enrichment continues so a single
    noisy batch never blocks full-library convergence."""


def _is_context_overflow(exc: BaseException) -> bool:
    """True when the model rejected the request as exceeding its context window.

    Walks the exception cause chain: an HTTP error body or a wrapped server
    message both carry the LM Studio text "exceeds the available context".
    """
    texts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPError):
            try:
                texts.append(current.read().decode("utf-8", "replace"))
            except Exception:
                pass
        else:
            texts.append(str(current))
        current = current.__cause__
    return "context" in "\n".join(texts).casefold()


class SemanticGraphService:
    def __init__(
        self,
        *,
        model_profile_service: ModelProfileService,
        provider_factory: ProviderFactory | None = None,
        clock: Clock | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_retries: int = 0,
        large_group_min_speakers: int = LARGE_GROUP_MIN_SPEAKERS,
    ) -> None:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size < 1
            or batch_size > 100
        ):
            raise ValueError("semantic batch size is invalid")
        if (
            not isinstance(batch_retries, int)
            or isinstance(batch_retries, bool)
            or batch_retries < 0
            or batch_retries > 10
        ):
            raise ValueError("semantic batch retry count is invalid")
        if (
            not isinstance(large_group_min_speakers, int)
            or isinstance(large_group_min_speakers, bool)
            or large_group_min_speakers < 0
        ):
            raise ValueError("semantic large-group threshold is invalid")
        self._models = model_profile_service
        self._provider_factory = (
            provider_factory or _default_provider_factory
        )
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._batch_size = batch_size
        self._batch_retries = batch_retries
        self._large_group_min_speakers = large_group_min_speakers

    def enrich(
        self,
        connection: sqlite3.Connection,
    ) -> SemanticGraphResult:
        now = self._clock()
        self._skip_large_group_chats(connection)
        pending_by_account = _pending_chunks_by_account(connection)
        all_account_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM source_accounts ORDER BY id"
            )
        ]
        if not pending_by_account:
            _set_account_statuses(
                connection,
                all_account_ids,
                status="ready",
                error_code=None,
                updated_at=now,
            )
            connection.commit()
            return SemanticGraphResult(
                processed_chunks=0,
                ready_accounts=len(all_account_ids),
                degraded_accounts=0,
                skipped=False,
            )

        pending_account_ids = set(pending_by_account)
        ready_without_work = [
            account_id
            for account_id in all_account_ids
            if account_id not in pending_account_ids
        ]
        try:
            resolved = self._models.resolve_local_profile_for_background()
        except Exception:
            _set_account_statuses(
                connection,
                ready_without_work,
                status="ready",
                error_code=None,
                updated_at=now,
            )
            _set_account_statuses(
                connection,
                sorted(pending_account_ids),
                status="degraded",
                error_code="MODEL_UNAVAILABLE",
                updated_at=now,
            )
            connection.commit()
            return SemanticGraphResult(
                processed_chunks=0,
                ready_accounts=len(ready_without_work),
                degraded_accounts=len(pending_account_ids),
                skipped=False,
            )

        if resolved is None:
            _set_account_statuses(
                connection,
                ready_without_work,
                status="ready",
                error_code=None,
                updated_at=now,
            )
            _set_account_statuses(
                connection,
                sorted(pending_account_ids),
                status="skipped",
                error_code=None,
                updated_at=now,
            )
            connection.commit()
            return SemanticGraphResult(
                processed_chunks=0,
                ready_accounts=len(ready_without_work),
                degraded_accounts=0,
                skipped=True,
            )

        _set_account_statuses(
            connection,
            ready_without_work,
            status="ready",
            error_code=None,
            updated_at=now,
        )
        _set_account_statuses(
            connection,
            sorted(pending_account_ids),
            status="indexing",
            error_code=None,
            updated_at=now,
        )
        connection.commit()

        try:
            provider = self._provider_factory(
                resolved.profile,
                resolved.credential,
            )
        except Exception:
            _set_account_statuses(
                connection,
                sorted(pending_account_ids),
                status="degraded",
                error_code="MODEL_UNAVAILABLE",
                updated_at=self._clock(),
            )
            connection.commit()
            return SemanticGraphResult(
                processed_chunks=0,
                ready_accounts=len(ready_without_work),
                degraded_accounts=len(pending_account_ids),
                skipped=False,
            )

        processed_chunks = 0
        ready_accounts = len(ready_without_work)
        degraded_accounts = 0
        for account_id, chunks in pending_by_account.items():
            account_failed = False
            for offset in range(0, len(chunks), self._batch_size):
                batch = chunks[offset : offset + self._batch_size]
                try:
                    items = self._process_batch(provider, batch)
                except _SemanticBatchSkip:
                    # Unparseable after retries: mark the batch complete with
                    # no semantic edges and keep going, so one noisy batch can
                    # never block full-library convergence. The batch is still
                    # counted as processed (no graph facts are written).
                    _mark_batch_complete(connection, batch)
                    connection.commit()
                    processed_chunks += len(batch)
                    continue
                except _InvalidModelOutput:
                    _set_account_statuses(
                        connection,
                        [account_id],
                        status="degraded",
                        error_code="INVALID_MODEL_OUTPUT",
                        updated_at=self._clock(),
                    )
                    connection.commit()
                    account_failed = True
                    break
                except Exception:
                    _set_account_statuses(
                        connection,
                        [account_id],
                        status="degraded",
                        error_code="MODEL_UNAVAILABLE",
                        updated_at=self._clock(),
                    )
                    connection.commit()
                    account_failed = True
                    break
                try:
                    _replace_batch_semantics(connection, batch, items)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    _set_account_statuses(
                        connection,
                        [account_id],
                        status="degraded",
                        error_code="SEMANTIC_WRITE_FAILED",
                        updated_at=self._clock(),
                    )
                    connection.commit()
                    account_failed = True
                    break
                processed_chunks += len(batch)

            if account_failed:
                degraded_accounts += 1
                continue
            _set_account_statuses(
                connection,
                [account_id],
                status="ready",
                error_code=None,
                updated_at=self._clock(),
            )
            connection.commit()
            ready_accounts += 1

        return SemanticGraphResult(
            processed_chunks=processed_chunks,
            ready_accounts=ready_accounts,
            degraded_accounts=degraded_accounts,
            skipped=False,
        )

    def _skip_large_group_chats(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Mark pending chunks from very large group chats as complete.

        WeChat group rosters are not imported into this library, so "large" is
        judged by distinct speakers among imported messages. Distinct speakers
        can never exceed true membership, so skipping rooms above the threshold
        never drops a genuinely small group; it only forgoes per-message graph
        facts inside big noisy rooms. Marking mirrors :func:`_mark_batch_complete`
        (version/hash set, no edges) so the rows stay out of future runs.
        """
        large_group_ids = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT m.conversation_id
                FROM messages AS m
                JOIN conversations AS c ON c.id = m.conversation_id
                WHERE c.kind = 'group'
                  AND TRIM(m.sender_name) <> ''
                GROUP BY m.conversation_id
                HAVING COUNT(DISTINCT m.sender_name) > ?
                """,
                (self._large_group_min_speakers,),
            )
        ]
        if not large_group_ids:
            return
        placeholders = ", ".join("?" for _conversation in large_group_ids)
        connection.execute(
            f"""
            UPDATE retrieval_chunks
            SET semantic_content_hash = content_hash,
                semantic_version = ?
            WHERE (
                    semantic_content_hash IS NULL
                    OR semantic_content_hash <> content_hash
                    OR semantic_version IS NULL
                    OR semantic_version <> ?
                  )
              AND conversation_id IN ({placeholders})
            """,
            (
                SEMANTIC_GRAPH_VERSION,
                SEMANTIC_GRAPH_VERSION,
                *large_group_ids,
            ),
        )

    def _process_batch(
        self,
        provider: SemanticGraphProvider,
        batch: Sequence[_Chunk],
    ) -> tuple[_SemanticItem, ...]:
        """Collect and parse one batch, retrying transient failures.

        Full-library enrichment runs for hours against a local model that
        occasionally glitches: a truncated/malformed response (parse-level) or
        a transient timeout/connection error (transport-level). Both are
        retried up to ``batch_retries`` times. A batch that is still
        unparseable is surfaced as ``_SemanticBatchSkip`` (marked complete,
        enrichment continues); a batch that still hits a transport error
        re-raises so a genuinely broken model degrades the account.

        A request that the model rejects for exceeding its context window is
        not transient: the batch is halved and each half retried recursively,
        so a dense group of long chunks is split into fitting pieces instead of
        degrading the whole run. A single chunk that alone overflows the window
        surfaces as ``_SemanticBatchSkip``.
        """
        attempts = self._batch_retries + 1
        last_exc: BaseException | None = None
        for _attempt in range(attempts):
            try:
                response = _collect_model_response(provider, batch)
                return _parse_model_response(response, batch)
            except _InvalidModelOutput as exc:
                last_exc = exc
            except Exception as exc:  # transport noise (timeout, conn, context)
                if _is_context_overflow(exc):
                    if len(batch) > 1:
                        midpoint = len(batch) // 2
                        return (
                            self._process_batch(provider, batch[:midpoint])
                            + self._process_batch(provider, batch[midpoint:])
                        )
                    raise _SemanticBatchSkip(
                        "semantic chunk exceeds the model context window"
                    ) from exc
                last_exc = exc
                continue
        if isinstance(last_exc, _InvalidModelOutput):
            raise _SemanticBatchSkip(
                f"semantic batch unparseable after {attempts} attempts"
            ) from last_exc
        assert last_exc is not None
        raise last_exc


def _pending_chunks_by_account(
    connection: sqlite3.Connection,
) -> dict[str, list[_Chunk]]:
    rows = connection.execute(
        """
        SELECT
            retrieval_chunks.id,
            retrieval_chunks.message_part_id,
            retrieval_chunks.message_id,
            retrieval_chunks.conversation_id,
            conversation_origins.source_account_id,
            retrieval_chunks.content_hash,
            retrieval_chunks.text_content,
            retrieval_chunks.sender_name,
            retrieval_chunks.sent_at
        FROM retrieval_chunks
        JOIN conversations
          ON conversations.id = retrieval_chunks.conversation_id
        JOIN conversation_origins
          ON conversation_origins.conversation_id =
             retrieval_chunks.conversation_id
        WHERE conversations.is_indexing_enabled = 1
          AND (
                retrieval_chunks.semantic_content_hash IS NULL
                OR retrieval_chunks.semantic_content_hash
                   <> retrieval_chunks.content_hash
                OR retrieval_chunks.semantic_version IS NULL
                OR retrieval_chunks.semantic_version <> ?
          )
        ORDER BY conversation_origins.source_account_id,
                 retrieval_chunks.id
        """,
        (SEMANTIC_GRAPH_VERSION,),
    ).fetchall()
    result: dict[str, list[_Chunk]] = {}
    for row in rows:
        chunk = _Chunk(
            id=int(row[0]),
            message_part_id=int(row[1]),
            message_id=int(row[2]),
            conversation_id=str(row[3]),
            source_account_id=str(row[4]),
            content_hash=str(row[5]),
            text_content=str(row[6]),
            sender_name=str(row[7]),
            sent_at=int(row[8]),
        )
        result.setdefault(chunk.source_account_id, []).append(chunk)
    return result


def _collect_model_response(
    provider: SemanticGraphProvider,
    batch: Sequence[_Chunk],
) -> str:
    question = _SEMANTIC_EXTRACTION_TASK
    contexts = [
        _ChunkEvidence(
            message_id=chunk.message_id,
            part_id=chunk.message_part_id,
            conversation_id=chunk.conversation_id,
            sender_name=chunk.sender_name,
            sent_at=chunk.sent_at,
            excerpt=chunk.text_content,
        )
        for chunk in batch
    ]
    parts: list[str] = []
    length = 0
    for delta in provider.stream_answer(
        question,
        contexts,
        history=[],
    ):
        if not isinstance(delta, str):
            raise _InvalidModelOutput("semantic response is invalid")
        length += len(delta)
        if length > MAX_RESPONSE_CHARACTERS:
            raise _InvalidModelOutput("semantic response is too large")
        parts.append(delta)
    return "".join(parts)


def _parse_model_response(
    response: str,
    batch: Sequence[_Chunk],
) -> tuple[_SemanticItem, ...]:
    try:
        payload = json.loads(response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _InvalidModelOutput("semantic response is not JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"items"}:
        raise _InvalidModelOutput("semantic response shape is invalid")
    raw_items = payload["items"]
    if not isinstance(raw_items, list):
        raise _InvalidModelOutput("semantic items are invalid")
    # A local model may emit a few extra items (hallucinated part ids) beyond
    # the batch size; those are dropped per-item below rather than failing the
    # whole batch.

    allowed_part_ids = {chunk.message_part_id for chunk in batch}
    seen_part_ids: set[int] = set()
    items: list[_SemanticItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            # Structural noise from a local model; drop the item rather than
            # failing the whole batch (dropped items write no graph facts).
            continue
        fields = set(raw_item)
        if not fields <= {"messagePartId", "topics", "entities"}:
            continue
        if "messagePartId" not in fields:
            continue
        part_id = raw_item["messagePartId"]
        if (
            not isinstance(part_id, int)
            or isinstance(part_id, bool)
            or part_id not in allowed_part_ids
            or part_id in seen_part_ids
        ):
            # Hallucinated/duplicate/out-of-batch part id: drop the item. This
            # never writes graph facts, so dropping is as safe as rejecting
            # the whole batch while letting valid items through.
            continue
        seen_part_ids.add(part_id)
        try:
            topics = _normalize_labels(raw_item.get("topics") or [])
            entities = _normalize_labels(raw_item.get("entities") or [])
        except _InvalidModelOutput:
            # An explicitly malformed labels payload (e.g. a string instead of
            # a list) on this item: drop the item; valid neighbours survive.
            continue
        items.append(
            _SemanticItem(
                message_part_id=part_id,
                topics=topics,
                entities=entities,
            )
        )
    return tuple(items)


def _normalize_labels(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise _InvalidModelOutput("semantic labels are invalid")
    # Local models emit noisy labels at a low but non-zero rate (labels longer
    # than the cap, odd control characters, occasional duplicates). A single
    # malformed label must not fail the whole account: skip just that label and
    # keep the valid ones. Structural damage (non-list, wrong item shape,
    # forged part ids) is still rejected wholesale upstream.
    limited = value[:MAX_LABELS_PER_KIND]
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in limited:
        if not isinstance(item, str):
            continue
        display = " ".join(unicodedata.normalize("NFKC", item).split())
        if (
            not display
            or len(display) > MAX_LABEL_LENGTH
            or any(unicodedata.category(character) == "Cc" for character in display)
        ):
            continue
        key = display.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append((key, display))
    return tuple(normalized)


def _mark_batch_complete(
    connection: sqlite3.Connection,
    batch: Sequence[_Chunk],
) -> None:
    """Mark a batch as semantically processed without writing graph facts.

    Used when a batch cannot be parsed after retries (noisy local model): the
    chunks are recorded as seen (semantic hash/version set) so they never block
    enrichment again, but no topic/entity edges are created for them. The
    retrieval projection remains intact; only semantic-graph edges are absent.
    """
    connection.executemany(
        """
        UPDATE retrieval_chunks
        SET semantic_content_hash = ?, semantic_version = ?
        WHERE id = ?
        """,
        (
            (
                chunk.content_hash,
                SEMANTIC_GRAPH_VERSION,
                chunk.id,
            )
            for chunk in batch
        ),
    )


def _replace_batch_semantics(
    connection: sqlite3.Connection,
    batch: Sequence[_Chunk],
    items: Sequence[_SemanticItem],
) -> None:
    part_ids = [chunk.message_part_id for chunk in batch]
    placeholders = ", ".join("?" for _item in part_ids)
    affected_edge_ids = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT graph_edge_evidence.edge_id
            FROM graph_edge_evidence
            JOIN graph_edges
              ON graph_edges.id = graph_edge_evidence.edge_id
            WHERE graph_edges.id LIKE 'semantic_mentions:%'
              AND graph_edge_evidence.evidence_kind = 'message_part'
              AND graph_edge_evidence.evidence_id IN ({placeholders})
            """,
            tuple(str(part_id) for part_id in part_ids),
        )
    ]
    connection.execute(
        f"""
        DELETE FROM graph_edge_evidence
        WHERE evidence_kind = 'message_part'
          AND evidence_id IN ({placeholders})
          AND edge_id LIKE 'semantic_mentions:%'
        """,
        tuple(str(part_id) for part_id in part_ids),
    )
    if affected_edge_ids:
        edge_placeholders = ", ".join("?" for _item in affected_edge_ids)
        connection.execute(
            f"""
            DELETE FROM graph_edges
            WHERE id IN ({edge_placeholders})
              AND NOT EXISTS (
                    SELECT 1 FROM graph_edge_evidence
                    WHERE graph_edge_evidence.edge_id = graph_edges.id
              )
            """,
            affected_edge_ids,
        )
        connection.execute(
            f"""
            UPDATE graph_edges
            SET weight = (
                SELECT COUNT(*)
                FROM graph_edge_evidence
                WHERE graph_edge_evidence.edge_id = graph_edges.id
            )
            WHERE id IN ({edge_placeholders})
            """,
            affected_edge_ids,
        )

    chunks_by_part = {
        chunk.message_part_id: chunk
        for chunk in batch
    }
    new_edge_ids: set[str] = set()
    for item in items:
        chunk = chunks_by_part[item.message_part_id]
        conversation_node = connection.execute(
            """
            SELECT id
            FROM graph_nodes
            WHERE kind = 'conversation'
              AND trace_kind = 'conversation'
              AND trace_id = ?
            """,
            (chunk.conversation_id,),
        ).fetchone()
        if conversation_node is None:
            raise RuntimeError("deterministic conversation node is missing")
        conversation_node_id = str(conversation_node[0])
        for kind, labels in (
            ("topic", item.topics),
            ("entity", item.entities),
        ):
            trace_kind = f"semantic_{kind}"
            for key, display in labels:
                target_node_id = _stable_id(trace_kind, key)
                connection.execute(
                    """
                    INSERT INTO graph_nodes(
                        id, kind, label, trace_kind, trace_id
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        target_node_id,
                        kind,
                        display,
                        trace_kind,
                        key,
                    ),
                )
                edge_id = _stable_id(
                    "semantic_mentions",
                    conversation_node_id,
                    target_node_id,
                )
                new_edge_ids.add(edge_id)
                connection.execute(
                    """
                    INSERT INTO graph_edges(
                        id, source_node_id, target_node_id, kind, weight
                    ) VALUES (?, ?, ?, 'mentions', 1)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        edge_id,
                        conversation_node_id,
                        target_node_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO graph_edge_evidence(
                        edge_id, evidence_kind, evidence_id
                    ) VALUES (?, 'message_part', ?)
                    """,
                    (edge_id, str(item.message_part_id)),
                )

    if new_edge_ids:
        edge_placeholders = ", ".join("?" for _item in new_edge_ids)
        connection.execute(
            f"""
            UPDATE graph_edges
            SET weight = (
                SELECT COUNT(*)
                FROM graph_edge_evidence
                WHERE graph_edge_evidence.edge_id = graph_edges.id
            )
            WHERE id IN ({edge_placeholders})
            """,
            tuple(sorted(new_edge_ids)),
        )

    connection.executemany(
        """
        UPDATE retrieval_chunks
        SET semantic_content_hash = ?, semantic_version = ?
        WHERE id = ?
        """,
        (
            (
                chunk.content_hash,
                SEMANTIC_GRAPH_VERSION,
                chunk.id,
            )
            for chunk in batch
        ),
    )
    connection.execute(
        """
        DELETE FROM graph_nodes
        WHERE trace_kind IN ('semantic_topic', 'semantic_entity')
          AND NOT EXISTS (
                SELECT 1 FROM graph_edges
                WHERE graph_edges.source_node_id = graph_nodes.id
                   OR graph_edges.target_node_id = graph_nodes.id
          )
        """
    )


def _set_account_statuses(
    connection: sqlite3.Connection,
    account_ids: Sequence[str],
    *,
    status: str,
    error_code: str | None,
    updated_at: int,
) -> None:
    if not account_ids:
        return
    placeholders = ", ".join("?" for _item in account_ids)
    connection.execute(
        f"""
        UPDATE source_accounts
        SET semantic_graph_status = ?,
            semantic_graph_error_code = ?,
            semantic_graph_updated_at = ?
        WHERE id IN ({placeholders})
        """,
        (status, error_code, updated_at, *account_ids),
    )


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"


def _default_provider_factory(
    profile: ModelProfile,
    credential: str | None,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        profile.base_url,
        model=profile.model,
        credential=credential,
        system_prompt=_SEMANTIC_SYSTEM_PROMPT,
        user_prompt_builder=_build_semantic_user_prompt,
        temperature=0,
        timeout=180,
        max_tokens=4096,
    )
