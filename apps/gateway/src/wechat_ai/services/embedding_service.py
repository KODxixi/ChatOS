"""Embedding generation + index management for semantic search / RAG."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BASE = os.environ.get("LOCAL_EMBEDDING_BASE_URL", "http://localhost:9123/v1")
MODEL = os.environ.get("LOCAL_EMBEDDING_MODEL", "text-embedding-qwen3-embedding-8b")
KEY = os.environ.get("APIYI_API_KEY") or os.environ.get("CC_APIYI_KEY")
BATCH = 32
_DIM = 4096
# fallback config (used if llama-swap unreachable)
_FALLBACK_BASE = os.environ.get("LOCAL_EMBEDDING_FALLBACK_URL", "http://localhost:1234/v1")
_FALLBACK_MODEL = os.environ.get("LOCAL_EMBEDDING_FALLBACK_MODEL", "text-embedding-nomic-embed-text-v1.5")
_FALLBACK_KEY = os.environ.get("APIYI_API_KEY")
_use_local = True

# background index job state
_JOB: dict[str, Any] = {
    "running": False,
    "total": 0,
    "done": 0,
    "conversation": "",
    "started_at": 0,
}
_job_lock = threading.Lock()

# === Index filtering rules ===
# Skip group chats with more than this many unique senders (large noise groups)
GROUP_SIZE_THRESHOLD = 30
# Noise group title keywords: chatty/family/marketing/sales/promotional groups
NOISE_GROUP_KEYWORDS = [
    "家人", "家庭", "闲聊", "聊天", "唠嗑", "营销", "广告", "推广",
    "销售", "客户", "用户群", "交流群", "福利", "优惠", "团购", "秒杀",
    "拼单", "代购", "微商", "打卡", "签到", "投票", "点赞", "砍价",
    "助力", "红包", "抽奖", "活动群", "业主群", "邻居", "同学群",
    "校友", "老乡", "亲友", "闺蜜", "兄弟", "姐妹", "一家人",
    "相亲相爱", "吃喝玩乐", "美食", "旅游", "购物", "淘宝", "拼多多",
    "京东", "快递", "外卖", "神劵", "立减", "新户", "亲友团", "团购群",
]


def _is_noise_group(title: str) -> bool:
    """Check if a group title matches noise keywords."""
    if not title:
        return False
    return any(kw in title for kw in NOISE_GROUP_KEYWORDS)


def _get_indexable_conversations(db_path: str, *, limit: int | None = None) -> list[str]:
    """Return conversation IDs that should be indexed, applying group filters.

    Filters:
    - Skip group chats with > GROUP_SIZE_THRESHOLD unique senders
    - Skip group chats whose title matches NOISE_GROUP_KEYWORDS
    - Always include direct (1-on-1) conversations
    """
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        # Get all group conversations with their unique sender counts
        group_rows = conn.execute(
            """
            SELECT c.id, c.title, COUNT(DISTINCT m.sender_id) as unique_senders
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.kind = 'group'
            GROUP BY c.id
            """
        ).fetchall()
        skip_ids: set[str] = set()
        for gid, title, senders in group_rows:
            if senders > GROUP_SIZE_THRESHOLD:
                skip_ids.add(gid)
            elif _is_noise_group(title):
                skip_ids.add(gid)
        # Get direct conversations + non-skipped group conversations, ordered by message count
        placeholders = ",".join("?" * len(skip_ids)) if skip_ids else ""
        query = """
            SELECT id FROM conversations
            WHERE kind IN ('group','direct')
              AND message_count > 10
              AND COALESCE(
                  (SELECT o.is_indexing_enabled FROM conversation_overrides o
                   WHERE o.conversation_id = conversations.id),
                  conversations.is_indexing_enabled, 1) = 1
        """
        params: list[Any] = []
        if skip_ids:
            query += f" AND id NOT IN ({placeholders})"
            params.extend(skip_ids)
        query += " ORDER BY message_count DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        logger.info(
            "indexable conversations: %d (skipped %d noise/large groups)",
            len(rows), len(skip_ids),
        )
        return [r[0] for r in rows]


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingClient:
    """Minimal OpenAI-compatible embedding client (seam used by app.py)."""

    def __init__(self, base_url: str, *, model: str = MODEL, api_key: str | None = None,
                 timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or "not-needed"
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        req = urllib.request.Request(f"{self.base_url}/embeddings", data=body, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return [d["embedding"] for d in data["data"]]


def is_available() -> bool:
    return bool(KEY)


def _embed_request(texts: list[str]) -> np.ndarray:
    """Embed texts with fallback chain: llama-swap (9123) → LM Studio (1234) → external API."""
    global _use_local
    tries = [
        (BASE, MODEL, "llama-swap"),
        (_FALLBACK_BASE, _FALLBACK_MODEL, "lm-studio"),
    ]
    if _FALLBACK_KEY:
        tries.append(("https://api.apiyi.com/v1", "text-embedding-3-small", "apiyi"))
    last = None
    for base, model, tag in tries:
        url = f"{base}/embeddings"
        hdrs = {"Content-Type": "application/json"}
        if "localhost" not in base and "127.0.0.1" not in base:
            hdrs["Authorization"] = f"Bearer {_FALLBACK_KEY}"
        body = json.dumps({"model": model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        # 5 retries with exponential backoff (2/4/8/16/32s) — llama-swap model
        # TTL-unload needs ~12s cold reload, so short retries always fail.
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                if tag != "llama-swap":
                    logger.warning("embedding via %s (llama-swap unavailable)", tag)
                return np.array([d["embedding"] for d in data["data"]], dtype=np.float32)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
                last = e
                if attempt < 4:
                    wait = 2 ** (attempt + 1)
                    logger.info("embedding attempt %d failed (%s), retrying in %ds", attempt + 1, e, wait)
                    time.sleep(wait)
        # Do NOT permanently disable llama-swap — model TTL-unload is transient.
        if tag == "llama-swap":
            logger.warning("llama-swap unreachable after 5 retries, falling back this batch")
    raise RuntimeError(f"embedding failed: {last}")


def embed_texts(texts: list[str]) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), BATCH):
        out.append(_embed_request(texts[i : i + BATCH]))
        time.sleep(0.1)
    return np.vstack(out).tolist()


def clean_text(t: str) -> str:
    t = re.sub(r"^wxid_[a-z0-9_]+:?\s*", "", t or "")
    t = re.sub(r"^[a-zA-Z0-9_-]+:\n", "", t)
    t = re.sub(r"\[[^\]]{1,8}\]", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:8192]


# Recheck the override atomically at the write, including after a slow model call.
_STORE_ENABLED_VECTOR = """
    INSERT OR REPLACE INTO message_embeddings
        (message_id, conversation_id, embedding, model_version, status, created_at, updated_at)
    SELECT ?, ?, ?, ?, ?, ?, ?
    WHERE EXISTS (
        SELECT 1 FROM conversations c
        LEFT JOIN conversation_overrides o ON o.conversation_id=c.id
        WHERE c.id=? AND COALESCE(o.is_indexing_enabled,c.is_indexing_enabled,1)=1
    )
"""


def embed_and_store(connection: sqlite3.Connection, message_id: int, conversation_id: str, text: str) -> bool:
    if not _can_index_conversation(connection, conversation_id):
        return False
    body = clean_text(text)
    if len(body) < 4:
        return False
    now = int(time.time() * 1000)
    try:
        vec = _embed_request([body])[0]
        vec = vec / max(np.linalg.norm(vec), 1e-9)
        result = connection.execute(
            _STORE_ENABLED_VECTOR,
            (str(message_id), conversation_id, vec.astype(np.float32).tobytes(), MODEL, "indexed", now, now, conversation_id),
        )
        return result.rowcount == 1
    except Exception as e:
        logger.warning("embed msg %s failed: %s", message_id, e)
        connection.execute(
            _STORE_ENABLED_VECTOR,
            (str(message_id), conversation_id, b"", MODEL, "failed", now, now, conversation_id),
        )
        return False


def get_index_status(connection: sqlite3.Connection) -> dict[str, Any]:
    total = connection.execute("SELECT COUNT(*) FROM messages WHERE type='text'").fetchone()[0]
    indexed = connection.execute(
        "SELECT COUNT(*) FROM message_embeddings WHERE status='indexed'"
    ).fetchone()[0]
    failed = connection.execute(
        "SELECT COUNT(*) FROM message_embeddings WHERE status='failed'"
    ).fetchone()[0]
    convs = connection.execute(
        "SELECT COUNT(DISTINCT conversation_id) FROM message_embeddings WHERE status='indexed'"
    ).fetchone()[0]
    with _job_lock:
        job = dict(_JOB)
    return {
        "totalMessages": total,
        "indexed": indexed,
        "failed": failed,
        "conversationsIndexed": convs,
        "jobRunning": job["running"],
        "jobProgress": f"{job['done']}/{job['total']}",
        "jobConversation": job["conversation"],
    }


def _can_index_conversation(connection: sqlite3.Connection, conversation_id: str) -> bool:
    row = connection.execute(
        """SELECT c.kind, c.title FROM conversations c
           LEFT JOIN conversation_overrides o ON o.conversation_id=c.id
           WHERE c.id=? AND COALESCE(o.is_indexing_enabled,c.is_indexing_enabled,1)=1""",
        (conversation_id,),
    ).fetchone()
    if row is None or row[0] not in ("direct", "group"):
        return False
    if row[0] == "direct":
        return True
    if _is_noise_group(row[1] or ""):
        return False
    senders = connection.execute(
        "SELECT COUNT(DISTINCT sender_id) FROM messages WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()[0]
    return senders <= GROUP_SIZE_THRESHOLD


def index_conversation(db_path: str, conversation_id: str) -> dict:
    """Index one conversation (runs in a background thread). Batched for GPU throughput."""
    INDEX_BATCH = 128
    # NOTE: _JOB["running"] is managed by the caller (index_all / index_top),
    # not here — this function may be called in a loop across many conversations.
    try:
        with sqlite3.connect(db_path, timeout=60) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=60000")
            conn.execute("PRAGMA synchronous=NORMAL")
            if not _can_index_conversation(conn, conversation_id):
                return {"conversationId": conversation_id, "messages": 0}
            # Only index messages not yet embedded
            rows = conn.execute(
                """SELECT m.id, m.content FROM messages m
                   WHERE m.conversation_id=? AND m.type='text'
                     AND m.content IS NOT NULL AND length(m.content)>2
                     AND NOT EXISTS (SELECT 1 FROM message_embeddings me WHERE me.message_id=m.id AND me.status='indexed')
                   ORDER BY m.sent_at DESC""",
                (conversation_id,),
            ).fetchall()
            total = len(rows)
            with _job_lock:
                _JOB["total"] = total
                _JOB["done"] = 0
                _JOB["conversation"] = conversation_id

            done = 0
            for start in range(0, total, INDEX_BATCH):
                if not _can_index_conversation(conn, conversation_id):
                    break
                batch = rows[start : start + INDEX_BATCH]
                texts = []
                ids = []
                for r in batch:
                    ct = clean_text(r["content"] or "")
                    if len(ct) >= 4:
                        texts.append(ct)
                        ids.append(r["id"])
                if not texts:
                    done += len(batch)
                    with _job_lock:
                        _JOB["done"] = done
                    continue
                try:
                    vecs = _embed_request(texts)
                    # Normalize
                    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                    norms = np.maximum(norms, 1e-9)
                    vecs = vecs / norms
                    now = int(time.time() * 1000)
                    records = [
                        (str(mid), conversation_id, vecs[i].astype(np.float32).tobytes(), MODEL, "indexed", now, now, conversation_id)
                        for i, mid in enumerate(ids)
                    ]
                    conn.executemany(
                        _STORE_ENABLED_VECTOR,
                        records,
                    )
                    conn.commit()
                except Exception as e:
                    logger.warning("batch embed failed at offset %d: %s", start, e)
                    # Fall back to per-message to isolate failures
                    for i, r in enumerate(batch):
                        embed_and_store(conn, r["id"], conversation_id, r["content"] or "")
                    conn.commit()
                done += len(batch)
                with _job_lock:
                    _JOB["done"] = done
        return {"conversationId": conversation_id, "messages": total}
    finally:
        # Clean up per-conversation job state. Running flag is owned by caller.
        with _job_lock:
            _JOB["conversation"] = ""


def index_top_conversations(db_path: str, limit: int = 20) -> None:
    """Background thread: index top conversations by message volume (group-filtered)."""
    def _run():
        with _job_lock:
            _JOB["running"] = True
            _JOB["done"] = 0
        try:
            cids = _get_indexable_conversations(db_path, limit=limit)
            for cid in cids:
                try:
                    index_conversation(db_path, cid)
                except Exception as e:
                    logger.warning("index conversation %s failed: %s", cid, e)
        except Exception as e:
            logger.warning("background index failed: %s", e)
        finally:
            with _job_lock:
                _JOB["running"] = False
    threading.Thread(target=_run, daemon=True).start()


def index_all_conversations(db_path: str) -> None:
    """Background thread: index all indexable conversations (group-filtered)."""
    def _run():
        with _job_lock:
            _JOB["running"] = True
            _JOB["done"] = 0
        try:
            cids = _get_indexable_conversations(db_path)
            logger.info("full index: %d conversations to process", len(cids))
            for cid in cids:
                try:
                    index_conversation(db_path, cid)
                except Exception as e:
                    logger.warning("index conversation %s failed: %s", cid, e)
        except Exception as e:
            logger.warning("full index failed: %s", e)
        finally:
            with _job_lock:
                _JOB["running"] = False
    threading.Thread(target=_run, daemon=True).start()


def search(connection: sqlite3.Connection, query: str, *, conversation_id: str | None = None,
           limit: int = 10, threshold: float = 0.3) -> list[dict[str, Any]]:
    """Semantic search. Returns ranked message rows with similarity."""
    if not KEY:
        return []
    qv = _embed_request([clean_text(query)])[0]
    qv = qv / max(np.linalg.norm(qv), 1e-9)
    rows = connection.execute(
        """SELECT e.message_id, e.conversation_id, e.embedding
           FROM message_embeddings e
           JOIN conversations c ON c.id=e.conversation_id
           LEFT JOIN conversation_overrides o ON o.conversation_id=c.id
           WHERE e.status='indexed'
             AND COALESCE(o.is_indexing_enabled,c.is_indexing_enabled,1)=1"""
        + (" AND e.conversation_id=?" if conversation_id is not None else ""),
        (conversation_id,) if conversation_id is not None else (),
    ).fetchall()
    if not rows:
        return []
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    sims = mat @ qv
    order = np.argsort(-sims)[:limit]
    out = []
    now_ms = int(time.time() * 1000)
    for i in order:
        if sims[i] < threshold:
            continue
        r = rows[i]
        m = connection.execute(
            "SELECT content, sender_name, sent_at FROM messages WHERE id=?", (r["message_id"],)
        ).fetchone()
        if not m:
            continue
        c = connection.execute(
            "SELECT title FROM conversations WHERE id=?", (r["conversation_id"],)
        ).fetchone()
        # time decay (7-day half-life)
        age_days = (now_ms - (m["sent_at"] or now_ms)) / 86400000.0
        decay = 2.0 ** (-age_days / 7.0)
        score = float(sims[i]) * 0.7 + decay * 0.3
        out.append({
            "messageId": r["message_id"],
            "conversationId": r["conversation_id"],
            "conversationTitle": c["title"] if c else r["conversation_id"],
            "senderName": m["sender_name"],
            "content": clean_text(m["content"] or "")[:200],
            "createdAt": m["sent_at"],
            "similarityScore": round(float(sims[i]), 3),
            "combinedScore": round(score, 3),
        })
    return out
