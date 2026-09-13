"""Activity-based conversation filtering.

Marks conversations where the user has not sent any message in the last
``inactive_days`` days as ``is_indexing_enabled = 0``, so they are excluded
from retrieval, search, and knowledge projection.

The user's own wxid is auto-detected from the messages table (the sender with
the most outgoing messages) unless explicitly configured.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Only evaluate these conversation kinds.  System conversations (filehelper,
# newsapp, gh_* official accounts) are always left untouched.
_EVALUATED_KINDS = frozenset({"group", "direct"})


@dataclass(frozen=True, slots=True)
class ActivityEvaluationResult:
    user_wxid: str
    inactive_days: int
    evaluated: int
    marked_inactive: int
    already_inactive: int
    active: int
    marked_ids: tuple[str, ...]


def detect_user_wxid(connection: sqlite3.Connection) -> str | None:
    """Return the sender_id with the highest message count.

    This is almost certainly the account owner: in a WeChat message database
    the user's own messages appear under their own wxid across every
    conversation, so they dominate the global sender ranking.
    """
    row = connection.execute(
        """
        SELECT sender_id
        FROM messages
        WHERE sender_id IS NOT NULL AND sender_id != ''
        GROUP BY sender_id
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0] if row else None


def evaluate_activity(
    connection: sqlite3.Connection,
    *,
    user_wxid: str | None = None,
    inactive_days: int = 30,
    conversation_ids: tuple[str, ...] | None = None,
) -> ActivityEvaluationResult:
    """Evaluate conversation activity and mark inactive ones.

    A conversation is marked ``is_indexing_enabled = 0`` when the user has
    sent **zero** messages in the last *inactive_days* days.  Conversations
    that are already disabled are left as-is (we never auto-re-enable).

    Parameters
    ----------
    connection:
        Open SQLite connection to the library database.
    user_wxid:
        The user's own wxid.  Auto-detected when *None*.
    inactive_days:
        Rolling window in days.  Defaults to 30.
    conversation_ids:
        Restrict evaluation to these conversation ids.  When *None*, every
        group/direct conversation is evaluated.
    """
    if user_wxid is None:
        user_wxid = detect_user_wxid(connection)
    if not user_wxid:
        logger.warning("activity filter: cannot determine user wxid, skipping")
        return ActivityEvaluationResult(
            user_wxid="",
            inactive_days=inactive_days,
            evaluated=0,
            marked_inactive=0,
            already_inactive=0,
            active=0,
            marked_ids=(),
        )

    cutoff_ms = int((time.time() - inactive_days * 86400) * 1000)

    # Build the list of conversations to evaluate.
    if conversation_ids:
        placeholders = ", ".join("?" for _ in conversation_ids)
        conv_rows = connection.execute(
            f"""
            SELECT id, title, kind, is_indexing_enabled
            FROM conversations
            WHERE id IN ({placeholders})
              AND kind IN ('group', 'direct')
            """,
            conversation_ids,
        ).fetchall()
    else:
        conv_rows = connection.execute(
            """
            SELECT id, title, kind, is_indexing_enabled
            FROM conversations
            WHERE kind IN ('group', 'direct')
            """
        ).fetchall()

    marked_ids: list[str] = []
    already_inactive = 0
    active = 0

    for conv in conv_rows:
        conv_id = conv["id"]
        if not conv["is_indexing_enabled"]:
            already_inactive += 1
            continue

        # Count user's own messages in the rolling window.
        user_recent = connection.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE conversation_id = ?
              AND sender_id = ?
              AND sent_at >= ?
            """,
            (conv_id, user_wxid, cutoff_ms),
        ).fetchone()[0]

        if user_recent == 0:
            connection.execute(
                "UPDATE conversations SET is_indexing_enabled = 0 WHERE id = ?",
                (conv_id,),
            )
            # Immediately drop retrieval chunks so search/RAG stops returning
            # results from this conversation without waiting for the next
            # projection run.
            connection.execute(
                "DELETE FROM retrieval_chunks WHERE conversation_id = ?",
                (conv_id,),
            )
            marked_ids.append(conv_id)
            logger.info(
                "activity filter: marked inactive (no user message in %dd): %s",
                inactive_days,
                conv["title"] or conv_id,
            )
        else:
            active += 1

    connection.commit()

    result = ActivityEvaluationResult(
        user_wxid=user_wxid,
        inactive_days=inactive_days,
        evaluated=len(conv_rows),
        marked_inactive=len(marked_ids),
        already_inactive=already_inactive,
        active=active,
        marked_ids=tuple(marked_ids),
    )
    logger.info(
        "activity filter: evaluated=%d marked_inactive=%d already_inactive=%d active=%d (user=%s, window=%dd)",
        result.evaluated,
        result.marked_inactive,
        result.already_inactive,
        result.active,
        user_wxid,
        inactive_days,
    )
    return result
