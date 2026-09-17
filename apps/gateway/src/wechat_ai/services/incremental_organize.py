"""Incremental organize pipeline (ChatOS v0.4.1).

Replaces the old full-rebuild ``POST /tasks/extract`` (which did
``DELETE FROM tasks``) with a true incremental pipeline:

  activity -> classify -> tasks -> projects -> finish

Each stage only touches conversations/messages that are new since the
last run, tracked per-conversation in ``conversation_watermarks``.

Hard guarantees
---------------
* WeChat raw tables (conversations/messages/message_parts/assets) are
  read-only.  Activity and classification results go to
  ``conversation_overrides`` (derived table).  All writes go to derived
  tables (tasks, conversation_watermarks, conversation_overrides,
  background_jobs.result_json).
* Task identity is deterministic: ``task_id = sha1(conversation_id +
  source_message_id + content)``.  Re-running never duplicates.
* Existing tasks are never overwritten.  ``completed`` / ``ignored`` /
  user-modified tasks are permanently preserved.
* LLM calls are cached (``llm_cache``) and never block the HTTP layer;
  they run only inside this background job.  Local LM Studio is
  preferred; unavailability degrades to rule-only results and marks
  the job ``degraded``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .job_worker import JobResult
from .operations_service import BackgroundJobService

logger = logging.getLogger(__name__)

# Stages reported in job.stage / progress.
_STAGES = ("activity", "classify", "tasks", "projects", "finish")

# Statuses that must never be revived or overwritten.
_PROTECTED_STATUSES = frozenset({"completed", "ignored", "done"})


@dataclass
class OrganizeStats:
    conversationsScanned: int = 0
    messagesScanned: int = 0
    newTasks: int = 0
    duplicatesSkipped: int = 0
    protectedTasksPreserved: int = 0
    classified: int = 0
    projectsUpdated: int = 0
    watermarksAdvanced: int = 0
    llmDegraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversationsScanned": self.conversationsScanned,
            "messagesScanned": self.messagesScanned,
            "newTasks": self.newTasks,
            "duplicatesSkipped": self.duplicatesSkipped,
            "protectedTasksPreserved": self.protectedTasksPreserved,
            "classified": self.classified,
            "projectsUpdated": self.projectsUpdated,
            "watermarksAdvanced": self.watermarksAdvanced,
            "llmDegraded": self.llmDegraded,
        }


# ---------------------------------------------------------------------------
# Watermark helpers
# ---------------------------------------------------------------------------

def _get_watermark(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> tuple[int, int]:
    """Return (last_message_sent_at, last_message_id) for a conversation.

    Returns (0, 0) when no watermark exists yet (first run).
    """
    row = connection.execute(
        "SELECT last_message_sent_at, last_message_id FROM conversation_watermarks WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return 0, 0
    return int(row[0]), int(row[1])


def _set_watermark(
    connection: sqlite3.Connection,
    conversation_id: str,
    last_sent_at: int,
    last_message_id: int,
) -> None:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    connection.execute(
        """
        INSERT INTO conversation_watermarks(conversation_id, last_message_sent_at, last_message_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            last_message_sent_at = excluded.last_message_sent_at,
            last_message_id = excluded.last_message_id,
            updated_at = excluded.updated_at
        """,
        (conversation_id, last_sent_at, last_message_id, now_iso),
    )


# ---------------------------------------------------------------------------
# Task id / idempotency
# ---------------------------------------------------------------------------

def _task_id(conversation_id: str, source_message_id: int, content: str) -> str:
    raw = f"{conversation_id}:{source_message_id}:{content}".encode("utf-8")
    return "tsk_" + hashlib.sha1(raw).hexdigest()[:16]


def _count_protected_tasks(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE conversation_id = ? AND status IN ('completed', 'ignored', 'done')",
        (conversation_id,),
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _stage_activity(
    connection: sqlite3.Connection,
    stats: OrganizeStats,
) -> None:
    """Re-evaluate activity.  Writes to conversation_overrides, NOT conversations."""
    from .activity_filter import evaluate_activity_overrides

    result = evaluate_activity_overrides(connection, inactive_days=30)
    logger.info(
        "organize.activity: evaluated=%d marked_inactive=%d active=%d",
        result.evaluated, result.marked_inactive, result.active,
    )


def _stage_classify(
    connection: sqlite3.Connection,
    stats: OrganizeStats,
) -> None:
    """Classify only conversations that still need review.
    Writes to conversation_overrides, NOT conversations."""
    from .conversation_classifier import classify_all_overrides

    result = classify_all_overrides(connection, only_needs_review=True)
    stats.classified = result.classified
    logger.info("organize.classify: classified=%d", stats.classified)


def _stage_tasks(
    connection: sqlite3.Connection,
    stats: OrganizeStats,
    jobs_service: BackgroundJobService,
    job_id: str,
) -> set[str]:
    """Extract todos from messages after each conversation's watermark.

    Only INSERTs new tasks.  Existing tasks (by deterministic task_id or
    source_message_id) are skipped.  Protected statuses are counted but
    never touched.

    Returns the set of conversation_ids that had new tasks added.
    """
    from . import message_cleaning as mc
    from ..api.tasks import (
        _clean_todo_body,
        _due_date_for,
        _is_excluded,
        _looks_like_todo,
        _priority_for,
    )

    # Active conversations only — COALESCE override with base table.
    conv_rows = connection.execute(
        """
        SELECT c.id, c.title
        FROM conversations c
        LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
        WHERE COALESCE(o.is_indexing_enabled, c.is_indexing_enabled, 1) = 1
          AND c.kind IN ('group', 'direct')
        ORDER BY c.id
        """
    ).fetchall()

    total_convs = len(conv_rows)
    affected_conversations: set[str] = set()

    for idx, conv in enumerate(conv_rows):
        conv_id = conv["id"]
        conv_title = conv["title"] or ""

        # Progress update every 25 conversations (commit first to release write lock).
        if idx % 25 == 0:
            connection.commit()
            jobs_service.transition(
                job_id,
                status="running",
                stage="tasks",
                progress_current=idx,
                progress_total=total_convs,
            )

        wm_sent_at, wm_msg_id = _get_watermark(connection, conv_id)

        # Count protected tasks before we do anything.
        stats.protectedTasksPreserved += _count_protected_tasks(connection, conv_id)

        # Query messages after watermark.
        if wm_msg_id > 0:
            msg_rows = connection.execute(
                """
                SELECT id, sender_name, sent_at, content
                FROM messages
                WHERE conversation_id = ?
                  AND type = 'text'
                  AND content IS NOT NULL AND length(content) > 0
                  AND (sent_at > ? OR (sent_at = ? AND id > ?))
                ORDER BY sent_at ASC, id ASC
                LIMIT 500
                """,
                (conv_id, wm_sent_at, wm_sent_at, wm_msg_id),
            ).fetchall()
        else:
            # First run: scan recent 200 messages (same as old extract).
            msg_rows = connection.execute(
                """
                SELECT id, sender_name, sent_at, content
                FROM messages
                WHERE conversation_id = ?
                  AND type = 'text'
                  AND content IS NOT NULL AND length(content) > 0
                ORDER BY sent_at DESC
                LIMIT 200
                """,
                (conv_id,),
            ).fetchall()

        if not msg_rows:
            continue

        stats.conversationsScanned += 1
        stats.messagesScanned += len(msg_rows)

        # Extract todos from these messages.
        now_ms = int(time.time() * 1000)
        new_tasks: list[dict[str, Any]] = []

        for r in msg_rows:
            body = _clean_todo_body(r["content"] or "")
            if mc.is_filler(body):
                continue
            if _is_excluded(body):
                continue
            if not _looks_like_todo(body):
                continue
            if len(body) < 10:
                continue
            body = mc.truncate(body, 50)
            tid = _task_id(conv_id, int(r["id"]), body)
            new_tasks.append({
                "taskId": tid,
                "conversationId": conv_id,
                "conversationTitle": conv_title,
                "content": body,
                "senderName": r["sender_name"],
                "sentAt": r["sent_at"],
                "sourceMessageId": int(r["id"]),
                "status": "todo",
                "priority": _priority_for(body),
                "dueDate": _due_date_for(body),
            })

        # LLM secondary filter (cached, graceful fallback).
        if new_tasks:
            new_tasks = _llm_filter_todos(connection, new_tasks, stats)

        # Idempotent insert: skip existing by task_id or source_message_id.
        conv_had_new = False
        for todo in new_tasks:
            existing = connection.execute(
                "SELECT task_id, status FROM tasks WHERE task_id = ? OR (conversation_id = ? AND source_message_id = ?)",
                (todo["taskId"], conv_id, todo["sourceMessageId"]),
            ).fetchone()
            if existing is not None:
                stats.duplicatesSkipped += 1
                if existing["status"] in _PROTECTED_STATUSES:
                    stats.protectedTasksPreserved += 1
                continue
            connection.execute(
                """
                INSERT INTO tasks
                    (task_id, conversation_id, conversation_title,
                     content, sender_name, sent_at, status, priority,
                     due_date, source_message_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    todo["taskId"], todo["conversationId"],
                    todo["conversationTitle"], todo["content"],
                    todo["senderName"], todo["sentAt"],
                    todo["status"], todo["priority"],
                    todo["dueDate"], todo["sourceMessageId"],
                    now_ms, now_ms,
                ),
            )
            stats.newTasks += 1
            conv_had_new = True

        if conv_had_new:
            affected_conversations.add(conv_id)

        # Advance watermark to the latest message we scanned (max sent_at, then max id).
        latest = max(msg_rows, key=lambda r: (int(r["sent_at"]), int(r["id"])))
        _set_watermark(connection, conv_id, int(latest["sent_at"]), int(latest["id"]))
        stats.watermarksAdvanced += 1

    connection.commit()
    return affected_conversations


def _llm_filter_todos(
    connection: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    stats: OrganizeStats,
) -> list[dict[str, Any]]:
    """Run LLM todo filter with cache + graceful degradation.

    Returns the filtered list.  On LLM unavailability, returns all
    candidates unchanged and marks stats.llmDegraded = True.
    Uses llm_cache for repeated inputs.
    """
    try:
        from . import llm_service as llm
    except ImportError:
        stats.llmDegraded = True
        return candidates

    if not llm.is_available():
        stats.llmDegraded = True
        return candidates

    try:
        # Only filter top 60 most recent for cost/speed.
        sorted_cands = sorted(candidates, key=lambda t: -t["sentAt"])
        cutoff = min(60, len(sorted_cands))
        head = sorted_cands[:cutoff]
        filtered: list[dict[str, Any]] = []
        for i in range(0, len(head), 20):
            batch = head[i : i + 20]
            items = [{"index": j, "text": t["content"]} for j, t in enumerate(batch)]

            # Check cache keyed on batch content.
            cache_ref = f"batch_{i}"
            cache_content = json.dumps(items, ensure_ascii=False, sort_keys=True)
            cached = llm.get_cached(connection, "filter_todos", cache_ref, cache_content)
            if cached is not None:
                try:
                    keep_map = json.loads(cached)
                    for j, t in enumerate(batch):
                        if str(j) in keep_map:
                            t["content"] = keep_map[str(j)]
                            filtered.append(t)
                    continue
                except (json.JSONDecodeError, TypeError):
                    pass  # cache corrupt, fall through to live call

            try:
                res = llm.filter_todos(items)
                keep_idx = {r["index"]: r["text"] for r in res}
                # Store in cache.
                llm.put_cached(
                    connection, "filter_todos", cache_ref, cache_content,
                    json.dumps({str(k): v for k, v in keep_idx.items()}, ensure_ascii=False),
                )
                for j, t in enumerate(batch):
                    if j in keep_idx:
                        t["content"] = keep_idx[j]
                        filtered.append(t)
            except Exception as exc:
                # OpenAICompatibleUnavailable or other model failure → degrade.
                logger.warning("organize.llm_filter batch failed: %s", exc)
                filtered.extend(batch)
                stats.llmDegraded = True
        filtered.extend(sorted_cands[cutoff:])
        return filtered
    except Exception as exc:
        logger.warning("organize.llm_filter failed: %s", exc)
        stats.llmDegraded = True
        return candidates


def _stage_projects(
    connection: sqlite3.Connection,
    stats: OrganizeStats,
    vault_root: Path,
    affected_conversations: set[str],
) -> None:
    """Re-extract projects only for affected conversations.

    If no conversations had new tasks, projectsUpdated stays 0.
    """
    from .project_extractor import extract_projects

    if not affected_conversations:
        stats.projectsUpdated = 0
        logger.info("organize.projects: no affected conversations, skipping")
        return

    try:
        projects = extract_projects(
            connection, vault_root=vault_root, min_messages=10,
            conversation_ids=tuple(sorted(affected_conversations)),
        )
        stats.projectsUpdated = len(projects)
        logger.info("organize.projects: updated=%d (affected=%d)", stats.projectsUpdated, len(affected_conversations))
    except Exception as exc:
        logger.warning("organize.projects failed: %s", exc)
        # Project extraction failure is NOT an LLM degradation.
        stats.projectsUpdated = 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_incremental_organize(
    connection_factory: Any,
    jobs_service: BackgroundJobService,
    job_id: str,
    *,
    vault_root: Path,
) -> JobResult:
    """Execute the full incremental organize pipeline.

    Called by the JobWorker handler.  Updates job progress and stores
    the final result in ``background_jobs.result_json``.
    """
    stats = OrganizeStats()
    total_stages = len(_STAGES)

    try:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            # Stage 1: activity
            jobs_service.transition(job_id, status="running", stage="activity", progress_current=1, progress_total=total_stages)
            _stage_activity(connection, stats)

            # Stage 2: classify
            connection.commit()
            jobs_service.transition(job_id, status="running", stage="classify", progress_current=2, progress_total=total_stages)
            _stage_classify(connection, stats)

            # Stage 3: tasks (incremental, watermark-based)
            connection.commit()
            jobs_service.transition(job_id, status="running", stage="tasks", progress_current=3, progress_total=total_stages)
            affected = _stage_tasks(connection, stats, jobs_service, job_id)

            # Stage 4: projects (only affected conversations)
            connection.commit()
            jobs_service.transition(job_id, status="running", stage="projects", progress_current=4, progress_total=total_stages)
            _stage_projects(connection, stats, vault_root, affected)

            # Stage 5: finish — persist result
            connection.commit()
            jobs_service.transition(job_id, status="running", stage="finish", progress_current=5, progress_total=total_stages)
            result = stats.to_dict()
            jobs_service.set_result(job_id, result)
            connection.commit()

        logger.info(
            "organize.complete: newTasks=%d duplicates=%d protected=%d watermarks=%d degraded=%s",
            stats.newTasks, stats.duplicatesSkipped,
            stats.protectedTasksPreserved, stats.watermarksAdvanced, stats.llmDegraded,
        )

        if stats.llmDegraded:
            return JobResult(status="degraded", error_code="LLM_UNAVAILABLE")
        return JobResult(status="completed")

    except Exception as exc:
        logger.exception("organize.failed: %s", exc)
        try:
            jobs_service.set_result(job_id, stats.to_dict())
        except Exception:
            pass
        return JobResult(status="failed", error_code="ORGANIZE_PIPELINE_ERROR")
