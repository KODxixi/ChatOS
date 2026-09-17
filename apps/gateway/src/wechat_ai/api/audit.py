"""Audit statistics API (ChatOS v0.3.0).

Aggregates message/conversation data for the audit dashboard charts:
message trend, domain distribution, top conversations, attention metrics,
and kind distribution.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import Any

from fastapi import APIRouter

from ..services.activity_filter import detect_user_wxid
from ..services.conversation_service import ConnectionFactory


def create_audit_router(
    connection_factory: ConnectionFactory,
) -> APIRouter:
    router = APIRouter()

    @router.get("/audit/stats")
    def audit_stats() -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        day_ms = 86_400_000

        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            # 1) messageTrend — last 30 days, one bucket per day
            trend_cutoff = now_ms - 30 * day_ms
            trend_rows = connection.execute(
                """
                SELECT (sent_at / ?) AS day_bucket, COUNT(*) AS cnt
                FROM messages
                WHERE sent_at >= ?
                GROUP BY day_bucket
                ORDER BY day_bucket
                """,
                (day_ms, trend_cutoff),
            ).fetchall()
            bucket_map = {r["day_bucket"]: r["cnt"] for r in trend_rows}
            today_bucket = now_ms // day_ms
            message_trend = []
            for i in range(29, -1, -1):
                b = today_bucket - i
                day_start = b * day_ms
                message_trend.append({
                    "date": time.strftime("%Y-%m-%d", time.localtime(day_start / 1000)),
                    "count": bucket_map.get(b, 0),
                })

            # 2) domainDistribution — per-domain conversation + message counts
            domain_rows = connection.execute(
                """
                SELECT COALESCE(c.domain, 'uncategorized') AS domain,
                       COUNT(DISTINCT c.id) AS conversation_count,
                       COALESCE(SUM(c.message_count), 0) AS message_count
                FROM conversations c
                GROUP BY c.domain
                ORDER BY message_count DESC
                """
            ).fetchall()
            domain_distribution = [
                {
                    "domain": r["domain"],
                    "conversationCount": r["conversation_count"],
                    "messageCount": r["message_count"],
                }
                for r in domain_rows
            ]

            # 3) topConversations — top 10 by message count
            top_rows = connection.execute(
                """
                SELECT id, title, kind, message_count, last_message_at
                FROM conversations
                ORDER BY message_count DESC
                LIMIT 10
                """
            ).fetchall()
            top_conversations = [
                {
                    "id": r["id"],
                    "title": r["title"] or r["id"],
                    "kind": r["kind"],
                    "messageCount": r["message_count"],
                    "lastMessageAt": r["last_message_at"],
                }
                for r in top_rows
            ]

            # 4) attentionMetrics — user engagement per conversation
            user_wxid = detect_user_wxid(connection)
            week_cutoff = now_ms - 7 * day_ms
            attention: list[dict[str, Any]] = []

            conv_rows = connection.execute(
                """
                SELECT id, title, kind, message_count
                FROM conversations
                WHERE kind IN ('group', 'direct')
                  AND message_count > 0
                ORDER BY message_count DESC
                LIMIT 200
                """
            ).fetchall()

            # batch per-conversation counts (total, user count, 7d interactions)
            # to avoid 3 queries per conversation.
            ids = [c["id"] for c in conv_rows]
            stats_map: dict[str, tuple[int, int, int]] = {}
            if ids:
                ph = ",".join("?" for _ in ids)
                for r in connection.execute(
                    f"""
                    SELECT conversation_id,
                           COUNT(*) AS total,
                           SUM(CASE WHEN sender_id = ? THEN 1 ELSE 0 END) AS user_cnt,
                           SUM(CASE WHEN sent_at >= ? THEN 1 ELSE 0 END) AS interact_7d
                    FROM messages
                    WHERE conversation_id IN ({ph})
                    GROUP BY conversation_id
                    """,
                    (user_wxid, week_cutoff, *ids),
                ).fetchall():
                    stats_map[r["conversation_id"]] = (
                        int(r["total"] or 0), int(r["user_cnt"] or 0), int(r["interact_7d"] or 0),
                    )

            for conv in conv_rows:
                total, user_count, interact_7d = stats_map.get(conv["id"], (0, 0, 0))
                if total == 0:
                    continue
                user_ratio = round(user_count / total, 3)

                # median reply minutes: time from a non-user message to the
                # next user message in the same conversation.
                reply_rows = connection.execute(
                    """
                    SELECT sender_id, sent_at FROM messages
                    WHERE conversation_id = ?
                    ORDER BY sent_at
                    """,
                    (conv["id"],),
                ).fetchall()
                gaps: list[float] = []
                prev_other_at: int | None = None
                for m in reply_rows:
                    if m["sender_id"] == user_wxid:
                        if prev_other_at is not None:
                            gaps.append((m["sent_at"] - prev_other_at) / 60000.0)
                        prev_other_at = None
                    else:
                        prev_other_at = m["sent_at"]
                if gaps:
                    gaps.sort()
                    median_gap = gaps[len(gaps) // 2]
                else:
                    median_gap = None

                # attentionScore: weighted blend (higher = more attention)
                score = (
                    user_ratio * 40
                    + min(interact_7d / 50.0, 1.0) * 40
                    + (20.0 if median_gap is None or median_gap < 60
                       else max(0.0, 20.0 - median_gap / 60.0))
                )
                attention.append({
                    "id": conv["id"],
                    "title": conv["title"] or conv["id"],
                    "userMessageRatio": user_ratio,
                    "interactionCount7d": interact_7d,
                    "avgReplyMinutes": round(median_gap, 1) if median_gap is not None else None,
                    "attentionScore": round(score, 1),
                })

            attention.sort(key=lambda x: -x["attentionScore"])
            attention_metrics = attention[:10]

            # 5) kindDistribution
            kind_rows = connection.execute(
                """
                SELECT kind, COUNT(*) AS conversation_count,
                       COALESCE(SUM(message_count), 0) AS message_count
                FROM conversations
                GROUP BY kind
                """
            ).fetchall()
            kind_distribution = [
                {
                    "kind": r["kind"],
                    "conversationCount": r["conversation_count"],
                    "messageCount": r["message_count"],
                }
                for r in kind_rows
            ]

        return {
            "messageTrend": message_trend,
            "domainDistribution": domain_distribution,
            "topConversations": top_conversations,
            "attentionMetrics": attention_metrics,
            "kindDistribution": kind_distribution,
        }

    return router
