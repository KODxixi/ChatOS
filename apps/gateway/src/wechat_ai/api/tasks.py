"""Task / todo extraction API (ChatOS v0.3.0).

Scans recent messages for action-oriented todo items using keyword rules.
All data is derived (WeChat raw tables are read-only); results are written
to the ``tasks`` table created by migration 015.
"""

from __future__ import annotations

import hashlib
import re
import logging
import sqlite3
import time
from contextlib import closing
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from .conversations import api_error_response
from ..services import message_cleaning as mc
from ..services.conversation_service import ConnectionFactory

logger = logging.getLogger(__name__)

# Allowed task status / priority values.  Kept in one place so the API contract
# and the UI status mapping agree.  Raw task lifecycle: todo -> completed;
# ignored is a user choice to hide the item.  `done` is accepted as an alias
# for completed (some older rows use it).
TASK_STATUSES = frozenset({"todo", "completed", "ignored", "done"})
TASK_PRIORITIES = frozenset({"low", "medium", "high"})


class TaskPatch(BaseModel):
    """Body for PATCH /api/tasks/{task_id}.  Both fields optional; at least
    one must be provided (enforced in the handler).  Values are validated
    against the task lifecycle enums so a UI typo cannot silently persist an
    unknown status (e.g. mapping draft/in_progress straight to todo)."""

    status: Literal["todo", "completed", "ignored", "done"] | None = None
    priority: Literal["low", "medium", "high"] | None = None


# ---------------------------------------------------------------------------
# Rule-based todo detection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Strict rule-based todo detection
# ---------------------------------------------------------------------------

# A todo must contain an action verb OR a time commitment OR an @mention + verb.
def _looks_like_todo(text: str) -> bool:
    if mc.ACTION_VERBS_RE.search(text):
        return True
    if mc._TIME_COMMIT_RE.search(text):
        return True
    if mc._IMPERATIVE_RE.search(text):
        return True
    return False


def _is_excluded(text: str) -> bool:
    if mc._SYSTEM_RE.search(text):
        return True
    if mc._AD_RE.search(text):
        return True
    if mc._EMOTION_RE.search(text):
        return True
    if mc._SYSTEM_INVITE_RE.search(text):
        return True
    # starts with punctuation
    if mc._PUNCT_START_RE.match(text):
        return True
    # explanation / conditional openers
    if mc._EXPLAIN_OPEN_RE.match(text):
        return True
    # file paths
    if mc._FILEPATH_RE.search(text) or mc._PATH_SEG_RE.search(text):
        return True
    # profanity / outburst at start
    if mc._PROFANITY_RE.match(text):
        return True
    # pure state / impossibility
    if mc._PURE_STATE_RE.search(text):
        return True
    # number/unit-heavy description (distance, data) without an imperative start
    if (len(mc._NUMERIC_DESC_RE.findall(text)) >= 1
            and not mc._IMPERATIVE_START_RE.match(text)
            and not mc._IMPERATIVE_RE.search(text)):
        return True
    # status report (业主/领导 + 明确了/决定了) with no real forward action
    if (mc._STATUS_REPORT_RE.search(text)
            and not mc._REQUEST_RE.search(text)
            and not re.search(r"(发我|给我|帮我|请|提交|准备|修改|设计|画|写|整理|安排|跟进|要提交|要完成|要修改|要准备)", text)):
        return True
    # stock / investment discussion without a real work verb
    if mc._STOCK_RE.search(text) and not mc._WORK_VERB_RE.search(text):
        return True
    # existential / descriptive opener (有个.../呈现.../示范区...)
    if mc._OBSERVE_OPEN_RE.match(text):
        return True
    # weather / small-talk without a real work action
    if mc._WEATHER_RE.search(text) and not mc._WORK_VERB_RE.search(text):
        return True
    # descriptive / observation opener (跟...一样/看起来/好像/感觉)
    if mc._DESCRIBE_RE.match(text):
        return True
    # chatty laughter opener with no action verb
    if mc._CHATTY_RE.match(text) and not mc.ACTION_VERBS_RE.search(text):
        return True
    # past-tense verb + an inline date that is already in the past
    if _is_past_completed(text):
        return True
    # past tense / already done — only exclude when no future action marker
    if mc._PAST_TENSE_RE.search(text) and not _has_future_marker(text):
        return True
    # pure question ending in ? with no action verb
    if mc._PURE_QUESTION_RE.search(text) and not mc.ACTION_VERBS_RE.search(text):
        return True
    # rhetorical question particle (吗/么/呢/吧) without a polite request cue
    if (mc._QUESTION_TAIL_RE.search(text)
            and not mc._REQUEST_RE.search(text)):
        return True
    # any internal '？' with no polite request and no concrete action commitment
    if ("？" in text or "?" in text) and not mc._REQUEST_RE.search(text) and not re.search(
        r"要|得|需要|必须|应该|发我|给我|帮我|提交|准备|修改|安排|跟进|画|写|做|联系|买|订", text
    ):
        return True
    # prediction / estimate with no concrete action verb
    if mc._PREDICT_RE.search(text) and not mc._CONCRETE_VERB_RE.search(text):
        return True
    # too short with no verb
    if len(text) < 8 and not mc.ACTION_VERBS_RE.search(text):
        return True
    # offer / help phrases without a concrete action+time commitment
    if mc._OFFER_RE.search(text) and not re.search(
        r"(发我|给我|帮我|提交|准备|修改|画|写|整理|安排|跟进).{0,6}(今天|明天|周一|周二|周三|周四|周五|下周|尽快|之前)|(今天|明天|周一|周二|周三|周四|周五|下周|尽快).{0,6}(发|做|写|画|提交|准备|修改|整理|安排)",
        text,
    ):
        return True
    # joke / sarcasm "能不能...呢" with no real object
    if mc._JOKE_Q_RE.search(text) and not mc._JOKE_OBJECT_RE.search(text):
        return True
    # mid-sentence yes/no question (能做吗...) without an imperative start
    if (mc._MID_QUESTION_RE.search(text)
            and not mc._IMPERATIVE_START_RE.search(text)
            and not mc._IMPERATIVE_RE.search(text)):
        return True
    return False


def _has_future_marker(text: str) -> bool:
    """True if the message points to a future action (keeps '已经…明天…' items)."""
    if mc._TIME_COMMIT_RE.search(text):
        return True
    if re.search(r"发我|给我|帮我|待办|尽快|麻烦|帮忙", text):
        return True
    if mc._IMPERATIVE_RE.search(text):
        return True
    if mc._IMPERATIVE_START_RE.match(text):
        return True
    return False


_PAST_VERB_RE = re.compile(r"推了|发了|做了|搞定了|完成了|弄了|改了|写了|提交了|发出来了")


def _is_past_completed(text: str) -> bool:
    """Past-tense verb + an inline date already before today => done, not a todo."""
    if not _PAST_VERB_RE.search(text):
        return False
    import datetime as _dt
    today = _dt.date.today()
    for m in mc._INLINE_DATE_RE.finditer(text):
        try:
            if m.group(1):  # YYYY-MM-DD
                d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:  # M月D日 (assume current year)
                d = _dt.date(today.year, int(m.group(4)), int(m.group(5)))
            if d < today:
                return True
        except (ValueError, TypeError):
            continue
    return False


_HIGH_PRIORITY = re.compile(
    r"务必|紧急|立刻|立即|截止|ddl|今天|今晚|尽快|急"
)
_MEDIUM_PRIORITY = re.compile(r"明天|下周|需要|安排|跟进|待办|todo|这周|本周")

_DATE_RE = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?)"
    r"|(下?周[一二三四五六日天]?)"
    r"|(今天|明天|后天|大后天|昨天|明晚|今晚)"
    r"|(本[周个天]|本月|下个月|下周)"
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def _clean_todo_body(raw: str) -> str:
    """Produce a clean 10-50 char todo description."""
    body = mc.strip_sender_header(raw)
    body = mc.strip_emoji(body)
    body = mc.clean_mentions(body)
    body = _URL_RE.sub("", body)
    body = body.replace("\u2005", " ").replace("\u3000", " ")
    body = re.sub(r"\s+", " ", body).strip()
    body = mc.strip_leading_filler(body)
    # strip leading @-style name tokens, e.g. "缪旭东 ZQi 今天PPT..." -> "今天PPT..."
    def _is_name(tok: str) -> bool:
        return bool(re.match(r"^([\u4e00-\u9fa5]{2,4}|[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9_·]{1,15})$", tok))
    parts = body.split(" ")
    while len(parts) > 1 and _is_name(parts[0]) and _is_name(parts[1]):
        parts = parts[1:]
    # now parts[0] is the last leading name; strip it only if the action starts next
    if (len(parts) > 1 and _is_name(parts[0])
            and re.match(r"^(今天|明天|周一|周二|周三|周四|周五|下周|尽快|发|提交|准备|修改|做|写|要|帮|把|我们|你们|这|那)", parts[1])):
        parts = parts[1:]
    body = " ".join(parts)
    # strip a trailing pure-name token, e.g. "...发你了 缪旭东" -> "...发你了"
    for _ in range(2):
        m = mc._TRAILING_NAME_RE.search(body)
        if not m:
            break
        body = body[: m.start()].rstrip(" ，。、;；")
    # strip a trailing no-space English name token, e.g. "...一下ZQi" -> "...一下"
    body = re.sub(r"[\u4e00-\u9fa5][A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9_·]{1,9}$",
                  lambda mm: mm.group(0)[0], body)
    body = body.rstrip(" ，。、;；")
    return body


def _priority_for(text: str) -> str:
    if _HIGH_PRIORITY.search(text):
        return "high"
    if _MEDIUM_PRIORITY.search(text):
        return "medium"
    return "low"


def _due_date_for(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    return next(g for g in m.groups() if g)


def _task_id(conversation_id: str, message_id: int, content: str) -> str:
    raw = f"{conversation_id}:{message_id}:{content}".encode("utf-8")
    return "tsk_" + hashlib.sha1(raw).hexdigest()[:16]


def extract_todos_from_rows(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Extract todo items from the recent messages of one conversation.

    Shared by POST /tasks/extract and GET /conversations/{id}/summary.
    Returns at most 10 clean, actionable todos per conversation, newest first.
    """
    rows = connection.execute(
        """
        SELECT id, sender_name, sent_at, content
        FROM messages
        WHERE conversation_id = ?
          AND type = 'text'
          AND content IS NOT NULL AND length(content) > 0
        ORDER BY sent_at DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    ).fetchall()

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_prefixes: set[str] = set()
    for r in rows:
        body = _clean_todo_body(r["content"] or "")
        if mc.is_filler(body):
            continue
        if _is_excluded(body):
            continue
        if not _looks_like_todo(body):
            continue
        # length gate 10..50
        if len(body) < 10:
            continue
        body = mc.truncate(body, 50)
        if body in seen:
            continue
        # near-duplicate dedup by first 6 chars (repeated restatements)
        prefix = body[:6]
        if prefix in seen_prefixes:
            continue
        seen.add(body)
        seen_prefixes.add(prefix)
        results.append({
            "taskId": _task_id(conversation_id, r["id"], body),
            "conversationId": conversation_id,
            "content": body,
            "senderName": r["sender_name"],
            "sentAt": r["sent_at"],
            "sourceMessageId": r["id"],
            "status": "todo",
            "priority": _priority_for(body),
            "dueDate": _due_date_for(body),
        })
        if len(results) >= 10:
            break
    return results


def create_tasks_router(
    connection_factory: ConnectionFactory,
) -> APIRouter:
    router = APIRouter()

    @router.post("/tasks/extract")
    def extract_tasks() -> dict[str, Any]:
        """Scan all indexed conversations' recent messages and persist todos."""
        now_ms = int(time.time() * 1000)
        stats = {
            "conversationsScanned": 0,
            "messagesScanned": 0,
            "todosFound": 0,
            "newInserted": 0,
            "existingKept": 0,
            "protectedTasksPreserved": 0,
        }
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            # Incremental/idempotent: never DELETE existing tasks.
            # completed/ignored/user-modified tasks are permanently preserved.
            conv_rows = connection.execute(
                """
                SELECT id, title FROM conversations
                WHERE COALESCE(
                        (SELECT o.is_indexing_enabled FROM conversation_overrides o
                         WHERE o.conversation_id = conversations.id),
                        conversations.is_indexing_enabled, 1) = 1
                  AND kind IN ('group', 'direct')
                """
            ).fetchall()

            inserted = 0
            kept = 0
            scanned_msgs = 0
            protected_count = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('completed', 'ignored', 'done')"
            ).fetchone()[0]
            stats["protectedTasksPreserved"] = protected_count
            global_prefixes: dict[str, dict[str, Any]] = {}
            for conv in conv_rows:
                todos = extract_todos_from_rows(connection, conv["id"], limit=200)
                scanned_msgs += len(todos)
                stats["conversationsScanned"] += 1
                for todo in todos:
                    stats["todosFound"] += 1
                    # global dedup by first 12 chars of content, keep newest sentAt
                    gkey = todo["content"][:12]
                    prev = global_prefixes.get(gkey)
                    if prev is not None:
                        if todo["sentAt"] > prev["sentAt"]:
                            global_prefixes[gkey] = todo
                        continue
                    global_prefixes[gkey] = todo
            # ---- LLM secondary filter (cached, graceful fallback) ----
            from ..services import llm_service as llm
            deduped = sorted(global_prefixes.values(), key=lambda t: -t["sentAt"])
            if llm.is_available() and deduped:
                try:
                    filtered: list[dict[str, Any]] = []
                    # only LLM-filter the top 60 most recent candidates for cost/speed
                    llm_cutoff = min(60, len(deduped))
                    head = deduped[:llm_cutoff]
                    for i in range(0, len(head), 20):
                        batch = head[i : i + 20]
                        items = [{"index": j, "text": t["content"]} for j, t in enumerate(batch)]
                        try:
                            res = llm.filter_todos(items)
                            keep_idx = {r["index"]: r["text"] for r in res}
                            for j, t in enumerate(batch):
                                if j in keep_idx:
                                    t["content"] = keep_idx[j]
                                    filtered.append(t)
                        except Exception as _e:
                            logger.warning("LLM todo batch failed: %s", _e); filtered.extend(batch)
                    filtered.extend(deduped[llm_cutoff:])
                    deduped = filtered
                except Exception as _e:
                    logger.warning("LLM todo filter failed: %s", _e)
            # write the deduplicated set (newest-first)
            for todo in deduped:
                existing = connection.execute(
                    "SELECT task_id FROM tasks WHERE task_id = ?",
                    (todo["taskId"],),
                ).fetchone()
                if existing is not None:
                    kept += 1
                    continue
                conv_title = next(
                    (c["title"] for c in conv_rows if c["id"] == todo["conversationId"]),
                    "",
                )
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
                        conv_title, todo["content"], todo["senderName"],
                        todo["sentAt"], todo["status"], todo["priority"],
                        todo["dueDate"], todo.get("sourceMessageId"), now_ms, now_ms,
                    ),
                )
                inserted += 1
            connection.commit()
            stats["messagesScanned"] = scanned_msgs
            stats["newInserted"] = inserted
            stats["existingKept"] = kept
        return {"ok": True, "stats": stats}

    @router.get("/tasks")
    def list_tasks(
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: int | None = None,
    ) -> dict[str, Any]:
        """List tasks, newest first. cursor = last sent_at of previous page."""
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("t.status = ?")
            params.append(status)
        if cursor is not None:
            where.append("t.sent_at < ?")
            params.append(cursor)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT t.task_id, t.conversation_id, t.conversation_title,
                       t.content, t.sender_name, t.sent_at, t.status,
                       t.priority, t.due_date, t.source_message_id
                FROM tasks t
                {where_sql}
                ORDER BY t.sent_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            items = [
                {
                    "taskId": r["task_id"],
                    "conversationId": r["conversation_id"],
                    "conversationTitle": r["conversation_title"],
                    "content": r["content"],
                    "senderName": r["sender_name"],
                    "sentAt": r["sent_at"],
                    "status": r["status"],
                    "priority": r["priority"],
                    "dueDate": r["due_date"],
                    "sourceMessageId": r["source_message_id"],
                }
                for r in rows
            ]
            next_cursor = rows[-1]["sent_at"] if len(rows) == limit else None
        return {"items": items, "nextCursor": next_cursor, "total": len(items)}

    @router.patch("/tasks/{task_id}", response_model=None)
    def update_task(
        task_id: str,
        patch: TaskPatch,
    ) -> dict[str, Any] | JSONResponse:
        updates: dict[str, Any] = {}
        if patch.status is not None:
            updates["status"] = patch.status
        if patch.priority is not None:
            updates["priority"] = patch.priority
        if not updates:
            return api_error_response(
                status_code=400, code="NO_UPDATE",
                message="status 或 priority 至少提供一个。",
            )
        updates["updated_at"] = int(time.time() * 1000)
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = [*updates.values(), task_id]
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            cur = connection.execute(
                f"UPDATE tasks SET {sets} WHERE task_id = ?", params,
            )
            connection.commit()
            if cur.rowcount == 0:
                return api_error_response(
                    status_code=404, code="TASK_NOT_FOUND",
                    message="未找到任务。",
                )
            row = connection.execute(
                "SELECT task_id, conversation_id, conversation_title, content,"
                " sender_name, sent_at, status, priority, due_date, source_message_id"
                " FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return {
            "taskId": row["task_id"],
            "conversationId": row["conversation_id"],
            "conversationTitle": row["conversation_title"],
            "content": row["content"],
            "senderName": row["sender_name"],
            "sentAt": row["sent_at"],
            "status": row["status"],
            "priority": row["priority"],
            "dueDate": row["due_date"],
            "sourceMessageId": row["source_message_id"],
        }

    return router
