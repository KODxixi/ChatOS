"""Tests for the dual-channel (keyword BM25 + vector cosine) search pipeline.

These exercise ``search_messages`` in relevance mode (``embed_fn`` provided),
covering trigram recall, vector recall, RRF fusion, rerank, graceful
degradation, and the relevance-mode cursor contract (``nextCursor=None``).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest


def _build_db(path: Path, *, with_embeddings: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            source_conversation_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            last_message_at INTEGER,
            message_count INTEGER NOT NULL DEFAULT 0,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            is_indexing_enabled INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            source_message_id TEXT,
            dedupe_key TEXT NOT NULL UNIQUE,
            sender_id TEXT,
            sender_name TEXT NOT NULL DEFAULT '',
            sent_at INTEGER NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            raw_payload_json TEXT
        );
        CREATE VIRTUAL TABLE message_fts USING fts5(
            content, sender_name, conversation_title, tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE message_fts_trigram USING fts5(
            content, tokenize='trigram'
        );
        CREATE TABLE conversation_overrides (
            conversation_id TEXT PRIMARY KEY,
            is_indexing_enabled INTEGER
        );
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO app_meta(key, value) VALUES ('schema_version', '23');
        INSERT INTO conversations
            (id, source_conversation_id, title, kind, last_message_at, message_count)
        VALUES
            ('c1', 'wx_c1', '项目讨论', 'group', 100, 4),
            ('c2', 'wx_c2', '私聊 Gary', 'direct', 200, 2);
        INSERT INTO messages
            (conversation_id, dedupe_key, sender_name, sent_at, type, content)
        VALUES
            ('c1', 'm1', '甲方', 10, 'text', '咏滨名邸报价单你发我一下'),
            ('c1', 'm2', 'Gary', 20, 'text', '水泥标号用 425 明天到货'),
            ('c1', 'm3', 'Gary', 30, 'text', '太原项目的总图今天要出'),
            ('c1', 'm4', '甲方', 40, 'text', '咏滨名邸价格再谈一谈'),
            ('c2', 'm5', 'Gary', 50, 'text', '今晚吃什么'),
            ('c2', 'm6', 'Gary', 60, 'text', '咏滨名邸样板间照片');
        INSERT INTO message_fts(rowid, content, sender_name, conversation_title)
        SELECT m.id, m.content, m.sender_name, c.title
        FROM messages m JOIN conversations c ON c.id = m.conversation_id;
        INSERT INTO message_fts_trigram(rowid, content)
        SELECT m.id, CASE WHEN m.type='text' THEN m.content ELSE '' END FROM messages m;
        """
    )
    if with_embeddings:
        # 1024-dim float32 embeddings; m1/m4/m6 are "near" a unit-anchored
        # query vector, others are random noise.
        rng = np.random.default_rng(0)
        anchor = np.zeros(1024, dtype=np.float32)
        anchor[:8] = 1.0
        anchor /= np.linalg.norm(anchor)
        near_ids = {1, 4, 6}
        emb_rows = []
        for mid in range(1, 7):
            if mid in near_ids:
                v = anchor + rng.normal(scale=0.05, size=1024).astype(np.float32)
            else:
                v = rng.normal(size=1024).astype(np.float32)
            v /= np.linalg.norm(v)
            conv = "c1" if mid <= 4 else "c2"
            emb_rows.append(
                (str(mid), conv, v.tobytes(), "text-embedding-qwen3-embedding-0.6b", "indexed", 0, 0)
            )
        conn.execute(
            """
            CREATE TABLE message_embeddings (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                model_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO message_embeddings VALUES (?,?,?,?,?,?,?)", emb_rows
        )
    conn.commit()
    conn.close()


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _fake_embed(query: str) -> list[float]:
    """Return the anchor vector used to seed m1/m4/m6."""
    v = np.zeros(1024, dtype=np.float32)
    v[:8] = 1.0
    v /= np.linalg.norm(v)
    return v.tolist()


class _FakeReranker:
    """Assigns a deterministic score per content substring."""

    def __init__(self, boost: dict[str, float] | None = None) -> None:
        self.boost = boost or {}

    def score_many(self, query: str, documents):
        out = []
        for doc in documents:
            score = 0.0
            for needle, val in self.boost.items():
                if needle in doc:
                    score = max(score, val)
            out.append(score)
        return out


def test_trigram_keyword_recall_hits_chinese_substring(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=False)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    # trigram should hit m1, m4, m6 (all contain "咏滨名邸")
    assert set(ids) == {1, 4, 6}
    assert page["nextCursor"] is None
    assert page["note"].startswith("mode=")
    assert "trigram-bm25" in page["note"]


def test_short_cjk_query_falls_back_to_like(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=False)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨",  # 2 chars -> below trigram min
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    assert set(ids) == {1, 4, 6}
    assert "keyword_via=like" in page["note"]


def test_vector_recall_ranks_anchor_neighbors(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    # m1/m4/m6 are near the anchor; fusion should put them ahead of m2/m3/m5
    assert set(ids) == {1, 2, 3, 4, 5, 6}
    assert ids[0] in {1, 4, 6}


def test_rerank_reorders_by_score(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    reranker = _FakeReranker(boost={"样板间": 0.9, "报价": 0.5})
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=reranker,
            rerank_enabled=True,
        )
    finally:
        conn.close()
    items = page["items"]
    # m6 ("样板间照片") should be top due to boost 0.9
    assert items[0]["id"] == 6
    assert "mode=rerank" in page["note"]


def test_vector_channel_degrades_gracefully(tmp_path: Path) -> None:
    """When embed_fn raises, keyword-only results still come back."""
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)

    def bad_embed(query: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=bad_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    assert set(ids) == {1, 4, 6}
    assert "vector_down=RuntimeError" in page["note"]


def test_rerank_degrades_gracefully(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)

    class _BoomReranker:
        def score_many(self, query, documents):
            raise RuntimeError("reranker offline")

    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=_BoomReranker(),
            rerank_enabled=True,
        )
    finally:
        conn.close()
    assert page["items"], "should still return fused results"
    assert "rerank_down=RuntimeError" in page["note"]


def test_relevance_mode_returns_single_page_no_cursor(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=2,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    assert page["nextCursor"] is None
    assert len(page["items"]) <= 2


def test_legacy_path_keeps_time_order_and_cursor(tmp_path: Path) -> None:
    """When embed_fn is None, legacy time-ordered + cursor path is intact."""
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=2,
            cursor=None,
            embed_fn=None,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    # CJK LIKE path is time-ordered desc: m6 (60), m4 (40), m1 (10)
    assert ids == [6, 4]
    assert page["nextCursor"] is not None


def test_sender_filter_applied_in_relevance_mode(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id=None,
            conversation_ids=None,
            sender_name="甲方",
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    # Only m1 and m4 are from 甲方
    assert set(ids) == {1, 4}


def test_conversation_id_filter_in_vector_channel(tmp_path: Path) -> None:
    from wechat_ai.services.search_service import search_messages

    db = tmp_path / "t.sqlite"
    _build_db(db, with_embeddings=True)
    conn = _open(db)
    try:
        page = search_messages(
            conn,
            query="咏滨名邸",
            conversation_id="c2",
            conversation_ids=None,
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=10,
            cursor=None,
            embed_fn=_fake_embed,
            reranker=None,
            rerank_enabled=False,
        )
    finally:
        conn.close()
    ids = [item["id"] for item in page["items"]]
    # All results must be in c2 (no c1 leak); m6 is trigram hit and vector
    # anchor neighbor; m5 may surface as vector recall runner-up.
    assert set(ids) <= {5, 6}
    assert ids[0] == 6
