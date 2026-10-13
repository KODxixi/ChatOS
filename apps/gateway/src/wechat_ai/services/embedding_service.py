"""Embedding generation + index management for semantic search / RAG."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BASE = os.environ.get("LOCAL_EMBEDDING_BASE_URL", "http://localhost:9123/v1")
from ..config import DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMENSIONS, validate_embedding_config
from .reranker_service import _validated_loopback_url

MODEL = os.environ.get("LOCAL_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
BATCH = 1024
_DIM = int(os.environ.get("LOCAL_EMBEDDING_DIMENSIONS", DEFAULT_EMBEDDING_DIMENSIONS))
_CONTEXT_LENGTH = 2048
_MIN_TEXT_LENGTH = 4
# 降级链已于 2026-09-21 删除（两个降级源的维度都与索引不符，只会写坏数据）。
# 若将来要加降级，**必须**是同维度的服务 —— 且保留下面那条维度校验。
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




_SYNC_INTERVAL_MIN = 15
_SYNC_ENABLED = False
# periodic sync 等待索引线程收尾时的 stall 超时：连续这么多秒无进度
# （_JOB["done"] 不再变化）就判定卡死，告警并强制清掉 running 标志，
# 避免后台周期线程被一个永不退出的索引线程挂死。
_SYNC_STALL_TIMEOUT_SEC = 900.0
# 进入索引候选集所需的最小会话消息数（与 _get_indexable_conversations 的
# message_count > 10 口径保持一致；单独抽出以便版本哈希引用同一常量）。
_INDEX_MIN_MESSAGE_COUNT = 10

def configure_from_settings(settings) -> None:
    """Push indexing config from Settings into module-level runtime globals."""
    global BASE, MODEL, _DIM, BATCH, _CONTEXT_LENGTH, _MIN_TEXT_LENGTH
    global GROUP_SIZE_THRESHOLD, NOISE_GROUP_KEYWORDS
    global _SYNC_ENABLED, _SYNC_INTERVAL_MIN
    m = getattr(settings, "indexing_model", None)
    if m:
        MODEL = str(m)
    _DIM = int(getattr(settings, "indexing_dimensions", 1024))
    BASE = _validated_loopback_url(os.environ.get("LOCAL_EMBEDDING_BASE_URL") or settings.retrieval_base_url)
    validate_embedding_config(MODEL, _DIM)
    BATCH = int(getattr(settings, "indexing_batch_size", BATCH))
    _CONTEXT_LENGTH = int(getattr(settings, "indexing_context_length", _CONTEXT_LENGTH))
    _MIN_TEXT_LENGTH = int(getattr(settings, "indexing_min_text_length", _MIN_TEXT_LENGTH))
    GROUP_SIZE_THRESHOLD = int(getattr(settings, "indexing_group_size_threshold", GROUP_SIZE_THRESHOLD))
    kws = getattr(settings, "indexing_noise_group_keywords", None)
    if kws:
        NOISE_GROUP_KEYWORDS = list(kws)
    _SYNC_ENABLED = bool(getattr(settings, "indexing_sync_enabled", False))
    _SYNC_INTERVAL_MIN = int(getattr(settings, "indexing_sync_interval_minutes", 15))
    logger.info("embedding_service configured: model=%s batch=%d ctx=%d minlen=%d group_thr=%d sync=%s/%dm",
                MODEL, BATCH, _CONTEXT_LENGTH, _MIN_TEXT_LENGTH, GROUP_SIZE_THRESHOLD, _SYNC_ENABLED, _SYNC_INTERVAL_MIN)


def _is_noise_group(title: str) -> bool:
    """Check if a group title matches noise keywords."""
    if not title:
        return False
    return any(kw in title for kw in NOISE_GROUP_KEYWORDS)


def _filter_rule_version() -> str:
    """稳定哈希：标识当前索引过滤规则集。

    任一阈值/关键词/最小长度变化都会改变这个值，状态接口据此标注
    "这套完成度是按哪套规则算出来的"，避免运维拿不同口径的数字比较。
    """
    payload = {
        "groupSizeThreshold": GROUP_SIZE_THRESHOLD,
        "noiseKeywords": NOISE_GROUP_KEYWORDS,
        "minTextLength": _MIN_TEXT_LENGTH,
        "minMessageCount": _INDEX_MIN_MESSAGE_COUNT,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _indexable_ids_and_reasons(
    conn: sqlite3.Connection,
) -> tuple[list[str], dict[str, int], set[str]]:
    """Apply the unified index filter to conversations on an open connection.

    Single source of truth shared by ``_get_indexable_conversations`` and
    ``get_index_status`` so the status denominator uses the *exact* same rules
    as the actual indexing pass.

    Filter order (per group/direct conversation):
      1. ``message_count <= _INDEX_MIN_MESSAGE_COUNT``            -> too_few_messages
      2. effective switch off (override first, then c.is_indexing_enabled, default 1)
                                                                 -> disabled_by_switch
      3. group with ``conversation_overrides.is_indexing_enabled = 1``
         (human explicitly turned it on)                          -> explicit_enabled (KEEP,
            bypassing both sender-count and noise-keyword rules)
      4. group with > GROUP_SIZE_THRESHOLD unique senders        -> too_many_senders (skip)
      5. group whose title matches NOISE_GROUP_KEYWORDS          -> noise_keyword (skip)
      6. otherwise                                               -> eligible

    Direct (1-on-1) conversations are never subject to sender-count/noise rules.

    Returns (eligible_ids_ordered_by_message_count_desc, reason_counts, skip_group_ids).
    """
    group_rows = conn.execute(
        """
        SELECT c.id, c.title, c.message_count, c.is_indexing_enabled,
               COUNT(DISTINCT m.sender_id) AS unique_senders,
               (SELECT o.is_indexing_enabled FROM conversation_overrides o
                WHERE o.conversation_id = c.id) AS explicit_override
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.kind = 'group'
        GROUP BY c.id
        """
    ).fetchall()
    direct_rows = conn.execute(
        """
        SELECT c.id, c.message_count, c.is_indexing_enabled,
               (SELECT o.is_indexing_enabled FROM conversation_overrides o
                WHERE o.conversation_id = c.id) AS explicit_override
        FROM conversations c
        WHERE c.kind = 'direct'
        """
    ).fetchall()

    reasons = {
        "eligible": 0,
        "explicit_enabled": 0,
        "too_many_senders": 0,
        "noise_keyword": 0,
        "disabled_by_switch": 0,
        "too_few_messages": 0,
    }
    eligible: list[tuple[str, int]] = []
    skip_ids: set[str] = set()

    def _effective(explicit, base_enabled) -> int:
        if explicit is not None:
            return int(explicit)
        if base_enabled is not None:
            return int(base_enabled)
        return 1

    for gid, title, msg_count, base_enabled, senders, explicit in group_rows:
        mc = int(msg_count or 0)
        if mc <= _INDEX_MIN_MESSAGE_COUNT:
            reasons["too_few_messages"] += 1
            continue
        if _effective(explicit, base_enabled) != 1:
            reasons["disabled_by_switch"] += 1
            continue
        # 人工显式开启的群：绕过成员数阈值与噪音关键词（2026-10-13 裁决）。
        if explicit == 1:
            reasons["explicit_enabled"] += 1
            eligible.append((gid, mc))
            continue
        if (senders or 0) > GROUP_SIZE_THRESHOLD:
            reasons["too_many_senders"] += 1
            skip_ids.add(gid)
            continue
        if _is_noise_group(title or ""):
            reasons["noise_keyword"] += 1
            skip_ids.add(gid)
            continue
        reasons["eligible"] += 1
        eligible.append((gid, mc))

    for did, msg_count, base_enabled, explicit in direct_rows:
        mc = int(msg_count or 0)
        if mc <= _INDEX_MIN_MESSAGE_COUNT:
            reasons["too_few_messages"] += 1
            continue
        if _effective(explicit, base_enabled) != 1:
            reasons["disabled_by_switch"] += 1
            continue
        reasons["eligible"] += 1
        eligible.append((did, mc))

    eligible.sort(key=lambda row: row[1], reverse=True)
    return [cid for cid, _ in eligible], reasons, skip_ids


def _get_indexable_conversations(db_path: str, *, limit: int | None = None) -> list[str]:
    """Return conversation IDs that should be indexed, applying group filters.

    Filters:
    - Skip group chats with > GROUP_SIZE_THRESHOLD unique senders
    - Skip group chats whose title matches NOISE_GROUP_KEYWORDS
    - Human-explicit ``conversation_overrides.is_indexing_enabled=1`` always wins
    - Always include direct (1-on-1) conversations
    """
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        ids, reasons, skip_ids = _indexable_ids_and_reasons(conn)
        if limit:
            ids = ids[:limit]
        logger.info(
            "indexable conversations: %d (eligible=%d explicit=%d skipped_large=%d "
            "skipped_noise=%d disabled=%d few_msgs=%d)",
            len(ids), reasons["eligible"], reasons["explicit_enabled"],
            reasons["too_many_senders"], reasons["noise_keyword"],
            reasons["disabled_by_switch"], reasons["too_few_messages"],
        )
        return ids


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingClient:
    """Minimal OpenAI-compatible embedding client (seam used by app.py)."""

    def __init__(self, base_url: str, *, model: str | None = None, api_key: str | None = None,
                 timeout: float = 30.0, dimensions: int | None = None, opener=None) -> None:
        self.base_url = _validated_loopback_url(base_url)
        self.model = model or MODEL
        self.dimensions = dimensions or _DIM
        validate_embedding_config(self.model, self.dimensions)
        self.opener = opener or urllib.request.urlopen
        self.api_key = api_key or "not-needed"
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        req = urllib.request.Request(f"{self.base_url}/embeddings", data=body, headers=hdrs, method="POST")
        try:
            with closing(self.opener(req, timeout=self.timeout)) as r:
                data = json.loads(r.read())
            rows = data["data"]
            if sorted(d["index"] for d in rows) != list(range(len(texts))):
                raise ValueError("embedding response indices/count mismatch")
            arr = np.asarray([d["embedding"] for d in sorted(rows, key=lambda d: d["index"])], dtype=np.float32)
            if arr.shape != (len(texts), self.dimensions) or not np.isfinite(arr).all():
                raise ValueError(f"embedding shape/values invalid: {arr.shape}, expected {(len(texts), self.dimensions)}")
            if np.any(np.linalg.norm(arr, axis=1) == 0):
                raise ValueError("zero embedding vector")
            return arr.tolist()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingUnavailable(str(exc)) from exc

    def similarities(self, query: str, documents) -> list[float]:
        if not documents:
            return []
        vectors = np.asarray(self.embed([query, *documents]), dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return (vectors[1:] @ vectors[0]).tolist()


def is_available() -> bool:
    """Whether a local endpoint is configured; not a health probe."""
    return bool(BASE and MODEL)


def _check_index_space(connection: sqlite3.Connection) -> None:
    mismatch = connection.execute(
        "SELECT 1 FROM message_embeddings WHERE status='indexed' "
        "AND (model_version IS NULL OR model_version != ? OR embedding IS NULL OR length(embedding) != ?) LIMIT 1",
        (MODEL, _DIM * 4),
    ).fetchone()
    if mismatch:
        raise EmbeddingUnavailable("Existing index uses a different embedding space; rebuild a shadow database from source before switching configuration")


def _embed_request(texts: list[str]) -> np.ndarray:
    r"""Embed texts via llama-swap (9123) ONLY —— 2026-09-21 起**没有降级链**。

    原先的两级降级源**维度都与索引不符**（768 / 1536 维 vs 4096 维）：
    在没有维度校验时，降级成功 = 静默写入维度不一致的向量（检索乱掉而不报错）；
    且 `_use_local` 一旦置 False 就永久跳过本地，一个瞬时故障会永久推向错维度源。

    现在：9123 不可达就**明确失败**，并在写入前校验维度。
    宁可索引停下，也不要写进不一致的向量。

    唯一真相见 C:\AI\memory\_canonical\LOCAL-MODELS.md 第六节。
    """
    global _use_local
    validate_embedding_config(MODEL, _DIM)
    tries = [
        (BASE, MODEL, "llama-swap"),
    ]
    last = None
    for base, model, tag in tries:
        url = f"{_validated_loopback_url(base)}/embeddings"
        hdrs = {"Content-Type": "application/json"}
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
                rows = data["data"]
                if sorted(d["index"] for d in rows) != list(range(len(texts))):
                    raise EmbeddingUnavailable("embedding response indices/count mismatch")
                arr = np.array([d["embedding"] for d in sorted(rows, key=lambda d: d["index"])], dtype=np.float32)
                # 维度必须与索引一致，否则写进去就是**静默的向量空间不一致**（检索乱掉而不报错）。
                # 注意 RuntimeError 不在下面的 except 元组里，所以它会直接抛出、不会被重试吞掉。
                if arr.shape != (len(texts), _DIM) or not np.isfinite(arr).all() or np.any(np.linalg.norm(arr, axis=1) == 0):
                    raise RuntimeError(
                        f"embedding 无效：{tag} ({base}/{model}) 返回 shape={arr.shape}，"
                        f"而索引是 {_DIM} 维 —— 已拒绝写入。检查 LOCAL_EMBEDDING_BASE_URL 指向的服务。"
                    )
                return arr
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
                last = e
                if attempt < 4:
                    wait = 2 ** (attempt + 1)
                    logger.info("embedding attempt %d failed (%s), retrying in %ds", attempt + 1, e, wait)
                    time.sleep(wait)
        # 不把 llama-swap 置为永久不可用 —— 模型的 TTL 卸载是瞬时的。
        # 2026-09-21：降级链已删，所以这里**没有** fallback 可走，只能向上抛。
        if tag == "llama-swap":
            logger.warning("llama-swap unreachable after 5 retries (no fallback configured)")
    if isinstance(last, urllib.error.HTTPError) and last.code == 401:
        raise RuntimeError(
            "embedding failed: HTTP 401 Unauthorized（检查 llama-swap 9123 是否配置并要求 API key）"
        ) from last
    raise RuntimeError(f"embedding failed: {last}") from last


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
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
    return t[:_CONTEXT_LENGTH]


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
    _check_index_space(connection)
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

    # P2-1: 分母口径与 _get_indexable_conversations 完全一致 ——
    # eligible = 通过过滤规则（成员阈值/噪音词/会话开关/最小长度）的会话里、
    # 长度达标的文本消息数。这才是"真正该被索引"的集合，不再是全部文本消息。
    eligible_ids, reasons, _skip = _indexable_ids_and_reasons(connection)
    if eligible_ids:
        ph = ",".join("?" for _ in eligible_ids)
        eligible = connection.execute(
            f"""
            SELECT COUNT(*) FROM messages m
            WHERE m.conversation_id IN ({ph})
              AND m.type='text' AND m.content IS NOT NULL
              AND length(m.content)>=?
            """,
            [*eligible_ids, _MIN_TEXT_LENGTH],
        ).fetchone()[0]
    else:
        eligible = 0
    completion_pct = round(100.0 * indexed / eligible, 1) if eligible else None

    with _job_lock:
        job = dict(_JOB)
    spaces = connection.execute(
        "SELECT model_version, length(embedding)/4, COUNT(*) FROM message_embeddings "
        "WHERE status='indexed' GROUP BY model_version, length(embedding)"
    ).fetchall()
    return {
        "configuredModel": MODEL,
        "configuredDimensions": _DIM,
        "indexSpaces": [{"model": r[0], "dimensions": r[1], "count": r[2]} for r in spaces],
        # 全量文本消息（历史口径，保留兼容）
        "totalMessages": total,
        # ★ 真正的分母：通过过滤规则的可索引消息数
        "eligibleMessages": eligible,
        # 因过滤规则被排除的文本消息数（分母之外）
        "skippedByRule": max(total - eligible, 0),
        "indexed": indexed,
        "failed": failed,
        "conversationsIndexed": convs,
        "eligibleConversations": len(eligible_ids),
        # ★ 完成度 = indexed / eligible（eligible 为 0 时给 None，不报假 0%）
        "completionPct": completion_pct,
        # 过滤规则版本：阈值/关键词/最小长度变化即变，便于跨次比对口径
        "filterRule": {
            "version": _filter_rule_version(),
            "groupSizeThreshold": GROUP_SIZE_THRESHOLD,
            "noiseKeywordCount": len(NOISE_GROUP_KEYWORDS),
            "minTextLength": _MIN_TEXT_LENGTH,
        },
        # 被过滤原因明细（会话数口径），便于审计与纠偏
        "filterBreakdown": reasons,
        "jobRunning": job["running"],
        "jobProgress": f"{job['done']}/{job['total']}",
        "jobConversation": job["conversation"],
    }


def _can_index_conversation(connection: sqlite3.Connection, conversation_id: str) -> bool:
    row = connection.execute(
        """SELECT c.kind, c.title,
                  COALESCE(o.is_indexing_enabled, c.is_indexing_enabled, 1) AS enabled,
                  o.is_indexing_enabled AS explicit_override
           FROM conversations c
           LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
           WHERE c.id = ?""",
        (conversation_id,),
    ).fetchone()
    if row is None or row[0] not in ("direct", "group"):
        return False
    if row[0] == "direct":
        return True
    # 人工显式开启（conversation_overrides.is_indexing_enabled=1）优先级最高：
    # 绕过噪音词与成员数阈值（2026-10-13 用户裁决：点名要入库的群必须入库）。
    if row[3] == 1:
        return True
    if not row[2]:
        return False
    if _is_noise_group(row[1] or ""):
        return False
    senders = connection.execute(
        "SELECT COUNT(DISTINCT sender_id) FROM messages WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()[0]
    return senders <= GROUP_SIZE_THRESHOLD


def index_conversation(db_path: str, conversation_id: str) -> dict:
    """Index one conversation (runs in a background thread). Batched for GPU throughput."""
    INDEX_BATCH = BATCH
    # NOTE: _JOB["running"] is managed by the caller (index_all / index_top),
    # not here — this function may be called in a loop across many conversations.
    try:
        with sqlite3.connect(db_path, timeout=60) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=60000")
            conn.execute("PRAGMA synchronous=NORMAL")
            _check_index_space(conn)
            if not _can_index_conversation(conn, conversation_id):
                return {"conversationId": conversation_id, "messages": 0}
            # Only index messages not yet embedded
            rows = conn.execute(
                """SELECT m.id, m.content FROM messages m
                   WHERE m.conversation_id=? AND m.type='text'
                     AND m.content IS NOT NULL AND length(m.content)>=?
                     AND NOT EXISTS (SELECT 1 FROM message_embeddings me WHERE me.message_id=m.id AND me.status='indexed')
                   ORDER BY m.sent_at DESC""",
                (conversation_id, _MIN_TEXT_LENGTH),
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
                    if len(ct) >= _MIN_TEXT_LENGTH:
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
    _check_index_space(connection)
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



def verify_index_coverage(db_path: str) -> dict:
    """Post-index verification: compare eligible text messages vs indexed.

    Returns dict with totals and gap. Gap > 0 means some messages failed or
    were skipped silently. Call this after any index run.
    """
    import sqlite3 as _sq
    conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = _sq.Row
    # eligible = text, len>=minlen, in indexable conversations (direct or small non-noise groups)
    cids = _get_indexable_conversations(db_path)
    ph = ",".join("?" * len(cids))
    eligible = conn.execute(f"""
        SELECT COUNT(*) FROM messages m
        WHERE m.conversation_id IN ({ph})
          AND m.type='text' AND m.content IS NOT NULL AND length(m.content)>=?
    """, cids + [_MIN_TEXT_LENGTH]).fetchone()[0]
    indexed = conn.execute(
        "SELECT COUNT(*) FROM message_embeddings WHERE status='indexed'"
    ).fetchone()[0]
    conn.close()
    gap = eligible - indexed
    result = {"eligible": eligible, "indexed": indexed, "gap": gap, "conversations": len(cids)}
    if gap > 0:
        logger.warning("index coverage gap: eligible=%d indexed=%d gap=%d", eligible, indexed, gap)
    else:
        logger.info("index coverage OK: eligible=%d indexed=%d", eligible, indexed)
    return result


def _wait_for_job_to_finish(
    *, timeout_sec: float = _SYNC_STALL_TIMEOUT_SEC, poll_sec: float = 5.0
) -> bool:
    """Block until ``_JOB["running"]`` clears, with stall detection.

    Returns ``True`` if the index thread finished on its own.  If ``running``
    stays ``True`` while ``_JOB["done"]`` stops advancing for ``timeout_sec``,
    the index thread is considered wedged: we log an error, force-clear the
    ``running`` flag (so the next sync round can start), and return ``False``
    instead of hanging the periodic thread forever.
    """
    last_done: Any = -1
    stalled = 0.0
    while True:
        with _job_lock:
            running = _JOB.get("running")
            done = _JOB.get("done")
        if not running:
            return True
        if done != last_done:
            last_done = done
            stalled = 0.0
        else:
            stalled += poll_sec
            if stalled >= timeout_sec:
                logger.error(
                    "periodic sync stall: index job made no progress for %.0fs "
                    "(done=%s), forcing running=False to unblock next sync",
                    stalled, done,
                )
                with _job_lock:
                    _JOB["running"] = False
                return False
        time.sleep(poll_sec)


def start_periodic_sync(settings) -> None:
    """Start a background thread that indexes new messages every N minutes.

    Reads indexing_sync_enabled / indexing_sync_interval_minutes from settings.
    No-op if sync is disabled. Safe to call at app startup.
    """
    global _SYNC_ENABLED, _SYNC_INTERVAL_MIN
    configure_from_settings(settings)
    if not _SYNC_ENABLED:
        logger.info("periodic sync disabled (indexing.syncEnabled=false)")
        return

    db_path = settings.library_db
    interval = _SYNC_INTERVAL_MIN * 60

    def _loop():
        import time as _t
        # wait 30s after startup before first run
        _t.sleep(30)
        while True:
            try:
                logger.info("periodic sync: starting index run")
                index_all_conversations(db_path)
                # wait for the background thread to finish, with stall detection
                # so a wedged index thread can't hang the periodic loop forever.
                _wait_for_job_to_finish()
                v = verify_index_coverage(db_path)
                logger.info("periodic sync done: %s", v)
            except Exception as e:
                logger.warning("periodic sync error: %s", e)
            _t.sleep(interval)

    import threading
    t = threading.Thread(target=_loop, daemon=True, name="index-sync")
    t.start()
    logger.info("periodic sync started: every %d min", _SYNC_INTERVAL_MIN)


def self_check(db_path: str) -> bool:
    """Startup self-check: verify config reads and SQL bindings work.

    Catches parameter-mismatch bugs at startup instead of mid-index.
    Returns True if all checks pass.
    """
    import sqlite3 as _sq
    errors = []
    try:
        conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        # check 1: _can_index_conversation SQL binding
        cid = conn.execute("SELECT id FROM conversations LIMIT 1").fetchone()
        if cid:
            try:
                _can_index_conversation(conn, cid[0])
            except Exception as e:
                errors.append(f"_can_index_conversation SQL: {e}")
        # check 2: index query binding
        try:
            conn.execute(
                "SELECT m.id FROM messages m WHERE m.conversation_id=? AND m.type='text' AND length(m.content)>=? LIMIT 1",
                (cid[0] if cid else "x", _MIN_TEXT_LENGTH),
            ).fetchone()
        except Exception as e:
            errors.append(f"index query SQL: {e}")
        # check 3: message_embeddings table exists
        try:
            conn.execute("SELECT COUNT(*) FROM message_embeddings LIMIT 1").fetchone()
        except Exception as e:
            errors.append(f"message_embeddings table: {e}")
        conn.close()
    except Exception as e:
        errors.append(f"db connect: {e}")

    if errors:
        for e in errors:
            logger.error("self-check FAIL: %s", e)
        return False
    logger.info("self-check OK: model=%s batch=%d minlen=%d sync=%s/%dm",
                MODEL, BATCH, _MIN_TEXT_LENGTH, _SYNC_ENABLED, _SYNC_INTERVAL_MIN)
    return True
