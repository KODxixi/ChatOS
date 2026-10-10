"""Message search: keyword (FTS5 BM25) + vector (cosine) dual-channel, RRF
fusion, optional cross-encoder rerank.

Two entry modes:

* **legacy** (``embed_fn is None``): the historical LIKE/FTS time-ordered
  path.  Cursor pagination works.  Used by tests and when the gateway is
  started without retrieval dependencies.
* **relevance** (``embed_fn`` provided): keyword BM25 recall + vector cosine
  recall, RRF-fused, optionally reranked by ``vl-reranker-2b``.  Returns a
  single relevance-ranked page (``nextCursor is None``) because rank order is
  not the time-ordered (sent_at, id) cursor.

Every external dependency (embedding endpoint, reranker endpoint) is wrapped
so an outage degrades to whatever channel is still alive instead of erroring
the HTTP endpoint.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from .conversation_service import (
    InvalidCursor,
    decode_cursor,
    encode_cursor,
    indexing_enabled_predicate,
)

logger = logging.getLogger(__name__)

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# trigram needs at least 3 chars per term to produce a window; shorter CJK
# terms fall back to LIKE (substring, time-ordered).
_MIN_TRIGRAM_CHARS = 3
_RRF_K = 60
_VECTOR_TOP_K = 100
_RERANK_CANDIDATES = 30
_KEYWORD_RECALL_LIMIT = 200


class InvalidSearchQuery(ValueError):
    pass


EmbedFn = Callable[[str], list[float]]
"""Embed a single query string, return a 1024-dim normalized vector."""


def search_messages(
    connection: sqlite3.Connection,
    *,
    query: str,
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None = None,
    sender_name: str | None,
    sent_at_from: int | None,
    sent_at_to: int | None,
    limit: int,
    cursor: str | None,
    embed_fn: EmbedFn | None = None,
    reranker: Any | None = None,
    rerank_enabled: bool = False,
) -> dict[str, Any]:
    terms = query.split()
    if not terms:
        raise InvalidSearchQuery

    if embed_fn is None:
        return _legacy_search(
            connection,
            query=query,
            terms=terms,
            conversation_id=conversation_id,
            conversation_ids=conversation_ids,
            sender_name=sender_name,
            sent_at_from=sent_at_from,
            sent_at_to=sent_at_to,
            limit=limit,
            cursor=cursor,
        )

    return _relevance_search(
        connection,
        query=query,
        terms=terms,
        conversation_id=conversation_id,
        conversation_ids=conversation_ids,
        sender_name=sender_name,
        sent_at_from=sent_at_from,
        sent_at_to=sent_at_to,
        limit=limit,
        embed_fn=embed_fn,
        reranker=reranker,
        rerank_enabled=rerank_enabled,
    )


# ---------------------------------------------------------------------------
# legacy (time-ordered, cursor-paginated) path
# ---------------------------------------------------------------------------


def _legacy_search(
    connection: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None,
    sender_name: str | None,
    sent_at_from: int | None,
    sent_at_to: int | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    if _CJK_PATTERN.search(query):
        where = [
            "messages.content LIKE ? ESCAPE '\\'"
            for _term in terms
        ]
        parameters: list[object] = [
            f"%{_escape_like(term)}%" for term in terms
        ]
    else:
        where = ["message_fts MATCH ?"]
        parameters = [_to_fts_query(query)]

    where.append(f"{indexing_enabled_predicate()} = 1")

    if conversation_id is not None:
        where.append("messages.conversation_id = ?")
        parameters.append(conversation_id)
    if conversation_ids is not None:
        if conversation_ids:
            placeholders = ", ".join("?" for _item in conversation_ids)
            where.append(f"messages.conversation_id IN ({placeholders})")
            parameters.extend(conversation_ids)
        else:
            where.append("0")
    if sender_name is not None:
        where.append("messages.sender_name = ?")
        parameters.append(sender_name)
    if sent_at_from is not None:
        where.append("messages.sent_at >= ?")
        parameters.append(sent_at_from)
    if sent_at_to is not None:
        where.append("messages.sent_at <= ?")
        parameters.append(sent_at_to)
    if cursor is not None:
        cursor_sent_at, cursor_id = decode_cursor(cursor, expected_kind="search")
        if not isinstance(cursor_id, int):
            raise InvalidCursor
        where.append(
            """
            (
                messages.sent_at < ?
                OR (messages.sent_at = ? AND messages.id < ?)
            )
            """
        )
        parameters.extend((cursor_sent_at, cursor_sent_at, cursor_id))

    parameters.append(limit + 1)
    try:
        rows = connection.execute(
            f"""
            SELECT
                messages.id,
                messages.conversation_id,
                messages.source_message_id,
                messages.sender_id,
                messages.sender_name,
                messages.sent_at,
                messages.type,
                messages.content,
                conversations.title AS conversation_title
            FROM message_fts
            JOIN messages ON messages.id = message_fts.rowid
            JOIN conversations ON conversations.id = messages.conversation_id
            WHERE {" AND ".join(where)}
            ORDER BY messages.sent_at DESC, messages.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise InvalidSearchQuery from exc

    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            "search",
            int(last_row["sent_at"]),
            int(last_row["id"]),
        )
    return {
        "items": [_search_item_from_row(row) for row in page_rows],
        "nextCursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# relevance (dual-channel + RRF + optional rerank) path
# ---------------------------------------------------------------------------


def _relevance_search(
    connection: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None,
    sender_name: str | None,
    sent_at_from: int | None,
    sent_at_to: int | None,
    limit: int,
    embed_fn: EmbedFn,
    reranker: Any | None,
    rerank_enabled: bool,
) -> dict[str, Any]:
    is_cjk = bool(_CJK_PATTERN.search(query))

    # 1) keyword recall -> ordered list of message ids (best first)
    keyword_ids, keyword_via = _keyword_recall(
        connection,
        query=query,
        terms=terms,
        is_cjk=is_cjk,
        conversation_id=conversation_id,
        conversation_ids=conversation_ids,
    )

    # 2) vector recall -> ordered list of message ids (best first)
    vector_ids: list[int] = []
    vector_error: str | None = None
    try:
        vector_ids = _vector_recall(
            connection,
            query=query,
            embed_fn=embed_fn,
            conversation_id=conversation_id,
            conversation_ids=conversation_ids,
            limit=_VECTOR_TOP_K,
        )
    except Exception as exc:  # noqa: BLE001 - any embedding failure degrades
        vector_error = type(exc).__name__
        logger.warning("vector recall degraded: %s", exc)

    # 3) RRF fusion
    if keyword_ids and vector_ids:
        fused_ids = _rrf_fuse(keyword_ids, vector_ids)
        mode = "fusion"
    elif keyword_ids:
        fused_ids = keyword_ids
        mode = "keyword" if vector_error is None else f"keyword (vector down: {vector_error})"
    elif vector_ids:
        fused_ids = vector_ids
        mode = "vector"
    else:
        fused_ids = []
        mode = "empty"

    # 4) load full rows for fused candidates, applying sender/sent_at filters
    rows_by_id = _load_candidate_rows(
        connection,
        message_ids=fused_ids[: _KEYWORD_RECALL_LIMIT],
        sender_name=sender_name,
        sent_at_from=sent_at_from,
        sent_at_to=sent_at_to,
    )
    # preserve fused order, drop rows that got filtered out
    ordered_rows = [rows_by_id[mid] for mid in fused_ids if mid in rows_by_id]

    # 5) optional rerank on top-N
    rerank_error: str | None = None
    if rerank_enabled and reranker is not None and len(ordered_rows) > 1:
        candidates = ordered_rows[:_RERANK_CANDIDATES]
        try:
            docs = [row["content"] or "" for row in candidates]
            scores = reranker.score_many(query, docs)
            ranked, dropped = _apply_rerank_scores(candidates, scores)
            # tail (not reranked) keeps fused order
            ordered_rows = ranked + ordered_rows[len(ranked):]
            mode = "rerank"
            if dropped:
                rerank_error = f"{dropped} candidates had no rerank score"
        except Exception as exc:  # noqa: BLE001 - reranker down degrades
            rerank_error = type(exc).__name__
            logger.warning("rerank degraded: %s", exc)

    items = [_search_item_from_row(row) for row in ordered_rows[:limit]]

    note_parts: list[str] = []
    note_parts.append(f"mode={mode}")
    note_parts.append(f"keyword_via={keyword_via}")
    if vector_error:
        note_parts.append(f"vector_down={vector_error}")
    if rerank_error:
        note_parts.append(f"rerank_down={rerank_error}")

    return {
        "items": items,
        "nextCursor": None,
        "mode": mode,
        "note": "; ".join(note_parts),
    }


def _keyword_recall(
    connection: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    is_cjk: bool,
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None,
) -> tuple[list[int], str]:
    """Return (ordered message ids best-first, channel tag)."""
    extra_where: list[str] = [f"{indexing_enabled_predicate('c')} = 1"]
    params: list[object] = []
    if conversation_id is not None:
        extra_where.append("m.conversation_id = ?")
        params.append(conversation_id)
    if conversation_ids is not None:
        if conversation_ids:
            ph = ",".join("?" for _ in conversation_ids)
            extra_where.append(f"m.conversation_id IN ({ph})")
            params.extend(conversation_ids)
        else:
            return [], "none"

    if is_cjk:
        # All terms must be >= 3 chars for trigram; otherwise LIKE AND.
        cjk_terms = [t for t in terms if _CJK_PATTERN.search(t)]
        use_trigram = bool(cjk_terms) and all(
            len(t) >= _MIN_TRIGRAM_CHARS for t in cjk_terms
        ) and all(not _CJK_PATTERN.search(t) or len(t) >= _MIN_TRIGRAM_CHARS for t in terms)
        if use_trigram:
            fts_query = " AND ".join(f'"{t}"' for t in terms)
            try:
                rows = connection.execute(
                    f"""
                    SELECT m.id
                    FROM (
                        SELECT rowid, bm25(message_fts_trigram) AS score
                        FROM message_fts_trigram
                        WHERE message_fts_trigram MATCH ?
                        ORDER BY score
                        LIMIT ?
                    ) hit
                    JOIN messages m ON m.id = hit.rowid
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE {" AND ".join(extra_where)}
                    ORDER BY hit.score
                    """,
                    [fts_query, _KEYWORD_RECALL_LIMIT, *params],
                ).fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning("trigram recall failed, falling back to LIKE: %s", exc)
                use_trigram = False
            else:
                return [int(r["id"]) for r in rows], "trigram-bm25"

        # LIKE fallback (short CJK terms or trigram failure)
        like_clauses = ["m.content LIKE ? ESCAPE '\\'" for _ in terms]
        like_params = [f"%{_escape_like(t)}%" for t in terms]
        try:
            rows = connection.execute(
                f"""
                SELECT m.id
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE {" AND ".join(extra_where + like_clauses)}
                ORDER BY m.sent_at DESC, m.id DESC
                LIMIT ?
                """,
                [*params, *like_params, _KEYWORD_RECALL_LIMIT],
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise InvalidSearchQuery from exc
        return [int(r["id"]) for r in rows], "like"

    # ASCII path: message_fts (unicode61) + bm25
    fts_query = _to_fts_query(query)
    try:
        rows = connection.execute(
            f"""
            SELECT m.id
            FROM (
                SELECT rowid, bm25(message_fts) AS score
                FROM message_fts
                WHERE message_fts MATCH ?
                ORDER BY score
                LIMIT ?
            ) hit
            JOIN messages m ON m.id = hit.rowid
            JOIN conversations c ON c.id = m.conversation_id
            WHERE {" AND ".join(extra_where)}
            ORDER BY hit.score
            """,
            [fts_query, _KEYWORD_RECALL_LIMIT, *params],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise InvalidSearchQuery from exc
    return [int(r["id"]) for r in rows], "fts-bm25"


def _vector_recall(
    connection: sqlite3.Connection,
    *,
    query: str,
    embed_fn: EmbedFn,
    conversation_id: str | None,
    conversation_ids: Sequence[str] | None,
    limit: int,
) -> list[int]:
    # Cheap table-existence check first: if the derived table is absent (e.g.
    # a fresh test DB or a not-yet-migrated vault), skip the HTTP embedding
    # call entirely.
    try:
        connection.execute(
            "SELECT 1 FROM message_embeddings LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("message_embeddings table missing") from exc

    sql = """
        SELECT e.message_id, e.embedding
        FROM message_embeddings e
        JOIN conversations c ON c.id = e.conversation_id
        LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
        WHERE e.status = 'indexed'
          AND COALESCE(o.is_indexing_enabled, c.is_indexing_enabled, 1) = 1
    """
    params: list[object] = []
    if conversation_id is not None:
        sql += " AND e.conversation_id = ?"
        params.append(conversation_id)
    if conversation_ids is not None:
        if conversation_ids:
            ph = ",".join("?" for _ in conversation_ids)
            sql += f" AND e.conversation_id IN ({ph})"
            params.extend(conversation_ids)
        else:
            return []

    raw = embed_fn(query)
    qv = np.asarray(raw, dtype=np.float32)
    qv = qv / max(float(np.linalg.norm(qv)), 1e-9)

    rows = connection.execute(sql, params).fetchall()
    if not rows:
        return []
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    sims = mat @ qv
    order = np.argsort(-sims)[:limit]
    return [int(rows[i]["message_id"]) for i in order]


def _rrf_fuse(a: Sequence[int], b: Sequence[int], *, k: int = _RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for rank, mid in enumerate(a, start=1):
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
    for rank, mid in enumerate(b, start=1):
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda m: (-scores[m], m))


def _apply_rerank_scores(
    candidates: Sequence[sqlite3.Row],
    scores: Sequence[float | None],
) -> tuple[list[sqlite3.Row], int]:
    """Return (reranked candidates, count of None scores)."""
    pairs = list(zip(candidates, scores, strict=False))
    none_count = sum(1 for s in scores if s is None)
    # higher score = better; None goes last
    pairs.sort(key=lambda p: (-(p[1] if p[1] is not None else -1e9), p[0]["id"]))
    return [p[0] for p in pairs], none_count


def _load_candidate_rows(
    connection: sqlite3.Connection,
    *,
    message_ids: Sequence[int],
    sender_name: str | None,
    sent_at_from: int | None,
    sent_at_to: int | None,
) -> dict[int, sqlite3.Row]:
    if not message_ids:
        return {}
    ph = ",".join("?" for _ in message_ids)
    sql = f"""
        SELECT
            m.id,
            m.conversation_id,
            m.source_message_id,
            m.sender_id,
            m.sender_name,
            m.sent_at,
            m.type,
            m.content,
            c.title AS conversation_title
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN conversation_overrides o ON o.conversation_id = m.conversation_id
        WHERE m.id IN ({ph})
          AND COALESCE(o.is_indexing_enabled, c.is_indexing_enabled, 1) = 1    """
    params: list[object] = [int(mid) for mid in message_ids]
    if sender_name is not None:
        sql += " AND m.sender_name = ?"
        params.append(sender_name)
    if sent_at_from is not None:
        sql += " AND m.sent_at >= ?"
        params.append(sent_at_from)
    if sent_at_to is not None:
        sql += " AND m.sent_at <= ?"
        params.append(sent_at_to)
    rows = connection.execute(sql, params).fetchall()
    return {int(r["id"]): r for r in rows}


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _to_fts_query(query: str) -> str:
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        raise InvalidSearchQuery
    return " AND ".join(f'"{term}"' for term in terms)


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _search_item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "sourceMessageId": row["source_message_id"],
        "senderId": row["sender_id"],
        "senderName": row["sender_name"],
        "sentAt": row["sent_at"],
        "type": row["type"],
        "content": row["content"],
        "conversationTitle": row["conversation_title"],
    }
