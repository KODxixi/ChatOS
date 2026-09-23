from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._errors import api_error_response, not_found_response as _not_found_response
from ..services.activity_filter import evaluate_activity_overrides
from ..services.conversation_classifier import classify_all_overrides
from ..services.project_extractor import extract_projects
from ..services import message_cleaning as mc
from ..services.conversation_service import (
    ConnectionFactory,
    ConversationNotFound,
    ConversationGroupNotFound,
    InvalidCursor,
    MessageNotFound,
    get_conversation,
    list_conversations,
    list_messages,
    update_conversation,
)
from ..wechat_media import add_media_readiness

logger = logging.getLogger(__name__)



class ConversationResponse(BaseModel):
    id: str
    sourceConversationId: str
    sourceAppId: str
    sourceAccountId: str
    title: str
    kind: str
    lastMessageAt: int | None
    messageCount: int
    isFavorite: bool
    isArchived: bool
    isIndexingEnabled: bool
    domain: str | None = None
    projectId: str | None = None
    projectRole: str | None = None
    classificationStatus: str | None = None
    groupId: str | None = None
    note: str


class ConversationGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=12)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("name is required")
        return normalized


class ConversationGroupResponse(BaseModel):
    id: str
    name: str
    count: int


class ConversationGroupPage(BaseModel):
    items: list[ConversationGroupResponse]


class MediaDerivativeResponse(BaseModel):
    kind: Literal["ocr", "transcript"]
    status: Literal["ready", "skipped", "error"]
    errorCode: str | None
    indexed: bool


class MessageAssetResponse(BaseModel):
    id: int
    messagePartId: int
    kind: str
    status: str
    externalUrl: None
    hasLocalFile: bool
    fileName: str | None
    mimeType: str | None
    byteSize: int | None
    contentUrl: str | None
    readinessState: Literal[
        "discovered", "resolved", "previewable", "processed", "error"
    ]
    processingUrl: str | None
    playbackUrl: str | None
    errorCode: str | None
    derivative: MediaDerivativeResponse | None


class MessageResponse(BaseModel):
    id: int
    conversationId: str
    sourceMessageId: str | None
    senderId: str | None
    senderName: str
    sentAt: int
    type: str
    content: str
    assets: list[MessageAssetResponse] = Field(default_factory=list)


class ConversationPage(BaseModel):
    items: list[ConversationResponse]
    nextCursor: str | None


class MessagePage(BaseModel):
    items: list[MessageResponse]
    nextCursor: str | None


class ConversationPreview(BaseModel):
    id: str
    title: str
    kind: str
    messageCount: int
    todoCount: int = 0
    lastMessageAt: int | None
    isIndexingEnabled: bool
    domain: str | None
    projectId: str | None
    projectRole: str | None
    classificationStatus: str | None
    latestMessage: str | None
    latestSender: str | None
    latestSentAt: int | None
    topSpeakers: list[dict[str, object]]
    summary: str | None = None
    topTodo: str | None = None


class ConversationPreviewPage(BaseModel):
    items: list[ConversationPreview]
    total: int


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isFavorite: bool | None = None
    isArchived: bool | None = None
    isIndexingEnabled: bool | None = None
    note: str | None = Field(default=None, max_length=20_000)
    groupId: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator(
        "isFavorite",
        "isArchived",
        "isIndexingEnabled",
        "note",
        mode="before",
    )
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("null is not allowed")
        return value

    @model_validator(mode="after")
    def require_update(self) -> ConversationPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


def install_api_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        RequestValidationError,
        _validation_error_handler,
    )


def create_conversations_router(
    connection_factory: ConnectionFactory,
    *,
    asset_root: Path | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/conversation-groups", response_model=ConversationGroupPage)
    def conversation_groups() -> dict[str, object]:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT g.id, g.name, COUNT(o.conversation_id) AS count
                FROM conversation_groups g
                LEFT JOIN conversation_overrides o ON o.group_id = g.id
                GROUP BY g.id, g.name, g.sort_order, g.created_at
                ORDER BY g.sort_order, g.created_at, g.id
                """
            ).fetchall()
            return {"items": [dict(row) for row in rows]}

    @router.post(
        "/conversation-groups",
        response_model=ConversationGroupResponse,
        status_code=201,
    )
    def create_conversation_group(
        payload: ConversationGroupCreate,
    ) -> dict[str, object] | JSONResponse:
        group_id = f"group-{uuid.uuid4().hex[:12]}"
        now = int(time.time() * 1000)
        try:
            with closing(connection_factory()) as connection:
                connection.execute(
                    """
                    INSERT INTO conversation_groups (id, name, sort_order, created_at, updated_at)
                    VALUES (?, ?, 100, ?, ?)
                    """,
                    (group_id, payload.name, now, now),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            return api_error_response(
                status_code=409,
                code="CONVERSATION_GROUP_EXISTS",
                message="同名会话分组已存在。",
            )
        return {"id": group_id, "name": payload.name, "count": 0}

    @router.delete(
        "/conversation-groups/{group_id}",
        status_code=204,
        response_model=None,
    )
    def delete_conversation_group(group_id: str) -> Response | JSONResponse:
        with closing(connection_factory()) as connection:
            if connection.execute(
                "SELECT 1 FROM conversation_groups WHERE id = ?",
                (group_id,),
            ).fetchone() is None:
                return api_error_response(
                    status_code=404,
                    code="CONVERSATION_GROUP_NOT_FOUND",
                    message="未找到会话分组。",
                )
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            connection.execute(
                "UPDATE conversation_overrides SET group_id = NULL, updated_at = ? WHERE group_id = ?",
                (now_iso, group_id),
            )
            connection.execute("DELETE FROM conversation_groups WHERE id = ?", (group_id,))
            connection.commit()
        return Response(status_code=204)

    @router.get("/conversations", response_model=ConversationPage)
    def conversations(
        sourceAppId: str | None = None,
        sourceAccountId: str | None = None,
        kind: str | None = None,
        includeArchived: bool = False,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return list_conversations(
                    connection,
                    limit=limit,
                    cursor=cursor,
                    source_app_id=sourceAppId,
                    source_account_id=sourceAccountId,
                    kind=kind,
                    include_archived=includeArchived,
                )
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="分页游标无效。",
            )

    @router.get("/conversations/preview", response_model=ConversationPreviewPage)
    def conversation_previews(
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        onlyActive: bool = True,
        domain: str | None = None,
    ) -> dict[str, object]:
        """Return conversations with latest message preview and top speakers.

        Optimized for card-based UI: avoids N+1 message fetches.
        """
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            where = []
            params: list[object] = []
            if onlyActive:
                where.append("COALESCE(o.is_indexing_enabled, conversations.is_indexing_enabled, 1) = 1")
            if domain:
                where.append("COALESCE(o.domain, conversations.domain) = ?")
                params.append(domain)
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            convs = connection.execute(
                f"""
                SELECT conversations.id, conversations.title, conversations.kind,
                       conversations.message_count, conversations.last_message_at,
                       COALESCE(o.is_indexing_enabled, conversations.is_indexing_enabled, 1) AS is_indexing_enabled,
                       COALESCE(o.domain, conversations.domain) AS domain,
                       COALESCE(o.project_id, conversations.project_id) AS project_id,
                       COALESCE(o.project_role, conversations.project_role) AS project_role,
                       COALESCE(o.classification_status, conversations.classification_status, 'needs_review') AS classification_status
                FROM conversations
                LEFT JOIN conversation_overrides o ON o.conversation_id = conversations.id
                {where_sql}
                ORDER BY conversations.last_message_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

            items = []
            # batch todo counts for all previewed conversations
            todo_counts: dict[str, int] = {}
            try:
                ids = [conv["id"] for conv in convs]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    for r in connection.execute(
                        f"SELECT conversation_id, COUNT(*) c FROM tasks "
                        f"WHERE status='todo' AND conversation_id IN ({placeholders}) "
                        f"GROUP BY conversation_id",
                        ids,
                    ).fetchall():
                        todo_counts[r["conversation_id"]] = r["c"]
            except Exception as _e:
                logging.getLogger(__name__).warning("todoCount batch failed: %s", _e)

            # batch topTodo: most recent todo per conversation
            top_todos: dict[str, str] = {}
            try:
                if ids:
                    for r in connection.execute(
                        f"SELECT conversation_id, content FROM tasks "
                        f"WHERE status='todo' AND conversation_id IN ({placeholders}) "
                        f"ORDER BY sent_at DESC",
                        ids,
                    ).fetchall():
                        if r["conversation_id"] not in top_todos:
                            top_todos[r["conversation_id"]] = r["content"]
            except Exception as _e:
                logging.getLogger(__name__).warning("topTodo batch failed: %s", _e)

            # batch summary from llm_cache (task_type='summary')
            summaries: dict[str, str] = {}
            try:
                if ids:
                    for r in connection.execute(
                        f"SELECT reference_id, result FROM llm_cache "
                        f"WHERE task_type='summary' AND reference_id IN ({placeholders}) "
                        f"ORDER BY created_at DESC",
                        ids,
                    ).fetchall():
                        summaries.setdefault(r["reference_id"], r["result"])
            except Exception as _e:
                logging.getLogger(__name__).warning("summary batch failed: %s", _e)
            for conv in convs:
                # Look back up to 10 recent messages; pick the first that has
                # real readable text (skip pure image/voice/video/call/etc.).
                recent = connection.execute(
                    """
                    SELECT type, content, sender_name, sent_at
                    FROM messages
                    WHERE conversation_id = ? AND content IS NOT NULL AND length(content) > 0
                    ORDER BY sent_at DESC
                    LIMIT 10
                    """,
                    (conv["id"],),
                ).fetchall()

                latest_row = None
                media_seen: list[str] = []
                for r in recent:
                    if r["type"] == "text":
                        body = mc.clean_summary_body(r["content"] or "")
                        if not mc.is_filler(body) and len(body) >= 2:
                            latest_row = (body, r["sender_name"], r["sent_at"])
                            break
                        # else: pure-emoji text, keep scanning
                    label = mc.MEDIA_LABELS.get(r["type"])
                    if label and label not in media_seen:
                        media_seen.append(label)

                if latest_row is not None:
                    body, sender, sent_at = latest_row
                    body = mc.truncate(body, 40)
                    if conv["kind"] == "group":
                        latest_msg = f"{sender}: {body}" if sender else body
                    else:
                        latest_msg = body
                    latest_sender = sender
                    latest_sent_at = sent_at
                elif media_seen:
                    # all recent are media — return a compact label list
                    latest_msg = " ".join(media_seen[:4])
                    latest_sender = recent[0]["sender_name"] if recent else None
                    latest_sent_at = recent[0]["sent_at"] if recent else None
                else:
                    latest_msg = None
                    latest_sender = None
                    latest_sent_at = None

                # Top speakers (for group chats)
                top_speakers = []
                if conv["kind"] == "group":
                    speakers = connection.execute(
                        """
                        SELECT sender_name, COUNT(*) as cnt
                        FROM messages
                        WHERE conversation_id = ? AND sender_name IS NOT NULL AND sender_name != ''
                        GROUP BY sender_name
                        ORDER BY cnt DESC
                        LIMIT 3
                        """,
                        (conv["id"],),
                    ).fetchall()
                    top_speakers = [{"name": s["sender_name"], "count": s["cnt"]} for s in speakers]

                items.append({
                    "id": conv["id"],
                    "title": conv["title"] or conv["id"],
                    "kind": conv["kind"],
                    "messageCount": conv["message_count"],
                    "todoCount": todo_counts.get(conv["id"], 0),
                    "lastMessageAt": conv["last_message_at"],
                    "isIndexingEnabled": bool(conv["is_indexing_enabled"]),
                    "domain": conv["domain"],
                    "projectId": conv["project_id"],
                    "projectRole": conv["project_role"],
                    "classificationStatus": conv["classification_status"],
                    "latestMessage": latest_msg,
                    "latestSender": latest_sender,
                    "latestSentAt": latest_sent_at,
                    "topSpeakers": top_speakers,
                    "summary": summaries.get(conv["id"]),
                    "topTodo": top_todos.get(conv["id"]),
                })

            total = connection.execute(
                f"SELECT COUNT(*) FROM conversations "
                f"LEFT JOIN conversation_overrides o ON o.conversation_id = conversations.id "
                f"{where_sql}", params,
            ).fetchone()[0]

        return {"items": items, "total": total}

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
    )
    def conversation(
        conversation_id: str,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return get_conversation(connection, conversation_id)
        except ConversationNotFound:
            return _not_found_response()

    @router.patch(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
    )
    def patch_conversation(
        conversation_id: str,
        patch: ConversationPatch,
    ) -> dict[str, Any] | JSONResponse:
        updates = {
            key: value
            for key, value in patch.model_dump().items()
            if key in patch.model_fields_set
        }
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return update_conversation(
                    connection,
                    conversation_id,
                    updates,
                )
        except ConversationNotFound:
            return _not_found_response()
        except ConversationGroupNotFound:
            return api_error_response(
                status_code=404,
                code="CONVERSATION_GROUP_NOT_FOUND",
                message="未找到会话分组。",
            )

    @router.get(
        "/conversations/{conversation_id}/messages",
        response_model=MessagePage,
    )
    def messages(
        conversation_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        targetMessageId: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                page = list_messages(
                    connection,
                    conversation_id,
                    limit=limit,
                    cursor=cursor,
                    target_message_id=targetMessageId,
                    asset_root=asset_root,
                )
                add_media_readiness(
                    connection,
                    page["items"],
                    asset_root=asset_root,
                )
                return page
        except ConversationNotFound:
            return _not_found_response()
        except MessageNotFound:
            return api_error_response(
                status_code=404,
                code="MESSAGE_NOT_FOUND",
                message="未找到引用消息。",
            )
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="分页游标无效。",
            )

    @router.post("/conversations/evaluate-activity")
    def evaluate_activity_endpoint(
        inactiveDays: Annotated[int, Query(ge=1, le=365)] = 30,
    ) -> dict[str, Any]:
        """Evaluate all conversations by user activity and mark inactive ones.

        Conversations where the user has sent zero messages in the last
        *inactiveDays* days are marked ``isIndexingEnabled=false`` and their
        retrieval chunks are removed immediately.
        """
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            result = evaluate_activity_overrides(
                connection,
                inactive_days=inactiveDays,
            )
        return {
            "userWxid": result.user_wxid,
            "inactiveDays": result.inactive_days,
            "evaluated": result.evaluated,
            "markedInactive": result.marked_inactive,
            "alreadyInactive": result.already_inactive,
            "active": result.active,
            "markedIds": list(result.marked_ids),
        }

    @router.post("/conversations/classify")
    def classify_conversations_endpoint(
        all: bool = False,
    ) -> dict[str, object]:
        """Run automatic conversation classification.

        By default only classifies conversations marked needs_review.
        Set ?all=true to re-classify everything.
        """
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            result = classify_all_overrides(connection, only_needs_review=not all)
        return {
            "evaluated": result.evaluated,
            "classified": result.classified,
            "needsReview": result.needs_review,
            "byDomain": result.by_domain,
        }

    @router.post("/conversations/extract-projects")
    def extract_projects_endpoint(
        minMessages: Annotated[int, Query(ge=1, le=10000)] = 10,
    ) -> dict[str, object]:
        """Extract project information from project-domain conversations.

        Generates project-brief.md files in the vault for syncing to ArchOS.
        """
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            vault_root = Path(connection.execute("PRAGMA database_list").fetchone()[2]).parent.parent.parent
            projects = extract_projects(
                connection, vault_root=vault_root, min_messages=minMessages,
            )
        return {
            "projectCount": len(projects),
            "projects": [
                {
                    "projectId": p.project_id,
                    "projectName": p.project_name,
                    "messageCount": p.message_count,
                    "decisions": len(p.decisions),
                    "milestones": len(p.milestones),
                    "clientFeedback": len(p.client_feedback),
                }
                for p in sorted(projects, key=lambda x: -x.message_count)
            ],
        }

    @router.get("/conversations/projects")
    def list_projects(
        minMessages: Annotated[int, Query(ge=0, le=100000)] = 10,
    ) -> dict[str, object]:
        """List projects extracted from project-domain conversations."""
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            vault_root = Path(connection.execute("PRAGMA database_list").fetchone()[2]).parent.parent.parent
            projects = extract_projects(
                connection,
                vault_root=vault_root,
                min_messages=minMessages,
            )
        items = [
            {
                "projectId": p.project_id,
                "projectName": p.project_name,
                "conversationCount": len(p.conversation_ids),
                "messageCount": p.message_count,
                "decisions": len(p.decisions),
                "milestones": len(p.milestones),
                "clientFeedback": len(p.client_feedback),
                "lastUpdated": p.last_updated,
            }
            for p in sorted(projects, key=lambda x: -x.message_count)
            if len(p.conversation_ids) > 1  # hide single-conversation "projects" (they are just chats)
        ]
        return {
            "total": len(items),
            "items": items,
        }


    # ------------------------------------------------------------------
    # Conversation summary / media / links (v0.3.0)
    # ------------------------------------------------------------------

    @router.get("/conversations/{conversation_id}/summary", response_model=None)
    def conversation_summary(
        conversation_id: str,
    ) -> dict[str, Any] | JSONResponse:
        """Generate a rule-based summary and key-info extraction for a conversation."""
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            conv = connection.execute(
                "SELECT id, title, kind FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conv is None:
                return _not_found_response()

            rows = connection.execute(
                """
                SELECT id, sender_name, sender_id, sent_at, type, content
                FROM messages
                WHERE conversation_id = ?
                  AND type = 'text'
                  AND content IS NOT NULL AND length(content) > 0
                ORDER BY sent_at DESC
                LIMIT 100
                """,
                (conversation_id,),
            ).fetchall()

            # ---- cleaned text candidates (last 20 text msgs) ----
            import re as _re
            recent = rows[:20]
            candidates: list[tuple[str, int]] = []  # (clean_body, msg_len)
            seen_bodies: set[str] = set()
            for r in recent:
                body = mc.strip_mentions(r["content"] or "")
                body = mc.clean_summary_body(body)
                # drop action-verb noise that leaked into content
                body = mc.ACTION_NOISE_RE.sub("", body)
                # strip URLs so a meeting/card invite doesn't dominate
                body = mc.URL_RE.sub("", body)
                body = body.strip(" ，。、;；")
                if mc.is_filler(body):
                    continue
                if mc.SYSTEM_INVITE_RE.search(body):
                    continue
                # drop sentences that lead with a transition (they aren't the point)
                if mc.EXPLAIN_OPEN_RE.match(body):
                    continue
                if len(body) < 4:
                    continue
                if body in seen_bodies:
                    continue
                seen_bodies.add(body)
                candidates.append((body, len(body)))

            # Rank: prefer declarative action+time messages, drop pure questions.
            def _score(body: str) -> int:
                s = 0
                if mc.ACTION_VERBS_RE.search(body):
                    s += 3
                if mc.TIME_COMMIT_RE.search(body):
                    s += 3
                if mc.IMPERATIVE_RE.search(body):
                    s += 2
                # length preference (longer = more info), capped
                s += min(len(body) // 8, 3)
                return s

            def _is_question(body: str) -> bool:
                return bool(mc.QUESTION_TAIL_RE.search(body)
                            or mc.MID_QUESTION_RE.search(body)
                            or _re.search(r"[?？]", body))

            # Keep ten compact, source-backed evidence extracts for the recent
            # inference.  Rank meaningful messages first, then use recency as
            # the stable tie-breaker; never manufacture rows to reach ten.
            important_ranked: list[tuple[int, int, dict[str, Any]]] = []
            seen_extracts: set[str] = set()
            for recency, r in enumerate(rows):
                body = mc.strip_mentions(r["content"] or "")
                body = mc.clean_summary_body(body)
                body = mc.ACTION_NOISE_RE.sub("", body)
                body = mc.URL_RE.sub("", body).strip(" ，。、;；")
                if len(body) < 4 or mc.is_filler(body):
                    continue
                if mc.SYSTEM_INVITE_RE.search(body):
                    continue
                excerpt = mc.truncate(body, 120)
                if excerpt in seen_extracts:
                    continue
                seen_extracts.add(excerpt)
                importance = _score(body) - (2 if _is_question(body) else 0)
                important_ranked.append((
                    importance,
                    recency,
                    {
                        "messageId": r["id"],
                        "senderName": (r["sender_name"] or "未知联系人").strip(),
                        "sentAt": r["sent_at"],
                        "content": excerpt,
                    },
                ))
            important_ranked.sort(key=lambda item: (-item[0], item[1]))
            important_messages = [item[2] for item in important_ranked[:10]]

            decl = [(b, _score(b)) for b, _l in candidates if not _is_question(b)]
            decl.sort(key=lambda cb: -cb[1])
            q_only = [(b, _score(b) - 2) for b, _l in candidates if _is_question(b)]
            q_only.sort(key=lambda cb: -cb[1])

            chosen = [b for b, _s in decl[:1]]
            if len(chosen) < 1:
                chosen = [b for b, _s in q_only[:1]]
            # only add a 2nd item if it keeps the joined summary <= 50 chars
            if chosen:
                first = chosen[0]
                for b, _s in decl[1:]:
                    if b in chosen:
                        continue
                    if len(first) + 1 + len(b) <= 50:
                        chosen.append(b)
                    break
            if not chosen:
                chosen = [b for b, _l in candidates[:1]]

            # drop @mentions (name included) from the summary itself
            chosen = [mc.strip_mentions(b).strip() for b in chosen]
            chosen = [b for b in chosen if b]
            summary = "，".join(chosen[:2]) if chosen else ""
            summary = mc.truncate(summary, 50)

            # ---- LLM summary override (cached, async fill, graceful fallback) ----
            import threading
            from ..services import llm_service as llm
            try:
                recent_rows = rows[:20]
                sentinel = ""
                if recent_rows:
                    sentinel = f"{recent_rows[0]['sent_at']}:{len(recent_rows)}"
                msg_lines: list[str] = []
                for r in recent_rows:
                    nm = mc.clean_summary_body(mc.strip_sender_header(r["content"] or ""))
                    nm = mc.strip_emoji(nm)
                    if nm and not mc.is_filler(nm):
                        sender = (r["sender_name"] or "").strip()
                        msg_lines.append(f"{sender}: {nm}"[:200])
                msg_text = "\n".join(reversed(msg_lines))[:3000]
                cached = llm.get_cached(connection, "summary", conversation_id, sentinel)
                if cached:
                    summary = cached
                elif msg_text and llm.is_available():
                    # spawn background thread; return rule-based now, fill cache async
                    def _bg():
                        try:
                            with closing(connection_factory()) as _c:
                                _c.row_factory = sqlite3.Row
                                out = llm.summarize(msg_text)
                                if out:
                                    llm.put_cached(_c, "summary", conversation_id, sentinel,
                                                   mc.truncate(out, 80))
                                    _c.commit()
                        except Exception as _be:
                            logging.getLogger(__name__).warning("async LLM summary: %r", _be)
                    threading.Thread(target=_bg, daemon=True).start()
            except Exception as _e:
                logging.getLogger(__name__).warning("LLM summary fallback: %s", _e)

            # keyPeople: group by sender_id, resolve to real names, drop non-persons
            person_map = mc.get_person_map(connection)
            kp_rows = connection.execute(
                """
                SELECT sender_id, sender_name, COUNT(*) AS cnt
                FROM messages
                WHERE conversation_id = ?
                  AND sender_id IS NOT NULL AND sender_id != ''
                GROUP BY sender_id, sender_name
                ORDER BY cnt DESC
                LIMIT 40
                """,
                (conversation_id,),
            ).fetchall()
            agg: dict[str, dict[str, Any]] = {}
            for r in kp_rows:
                sid = r["sender_id"]
                name = mc.resolve_person_name(sid, r["sender_name"], person_map)
                if not name:
                    continue
                if sid not in agg:
                    agg[sid] = {"name": name, "count": r["cnt"]}
                else:
                    agg[sid]["count"] += r["cnt"]
            key_people = sorted(
                agg.values(), key=lambda e: -e["count"]
            )[:5]

            # keyTopics: noun-like keyword extraction (no sentence fragments).
            topic_counts: dict[str, int] = {}
            topic_stop = {
                "我们", "你们", "他们", "这个", "那个", "什么", "可以", "现在",
                "知道", "觉得", "还是", "不是", "没有", "这样", "那样", "已经",
                "因为", "所以", "但是", "如果", "就是", "一下", "时候", "自己",
                "大家", "可能", "应该", "怎么", "为什么", "东西", "事情", "问题",
                # round-3: generic time/adverb stopwords
                "目前", "明天", "今天", "昨天", "之前", "以后", "然后",
                "看看", "需要", "一下", "以后", "以前", "过后", "刚才",
                # round-4: laughter / filler particles
                "哈哈", "哈哈哈", "呵呵", "嘿嘿", "嘻嘻", "嗯嗯", "哦哦",
                "啊啊", "哎呀", "哇", "嗯", "哦", "啊", "呀", "吧", "呢",
            }
            latin_re = _re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
            for body, _l in candidates:
                for gram, c in mc.extract_noun_grams(body).items():
                    if gram in topic_stop:
                        continue
                    topic_counts[gram] = topic_counts.get(gram, 0) + c
                for tok in latin_re.findall(body):
                    low = tok.lower()
                    if mc.looks_like_wxid(tok):
                        continue
                    if low in {"https", "http", "www", "com", "html", "the",
                               "and", "for", "are", "but", "not", "api"}:
                        continue
                    topic_counts[tok] = topic_counts.get(tok, 0) + 1
            # require at least 2 occurrences (drops single-message fragments)
            topic_items = [(t, c) for t, c in topic_counts.items() if c >= 2]
            # drop grams that are substrings of a longer, equally-frequent gram
            topic_items.sort(key=lambda tc: (-tc[1], -len(tc[0])))
            kept: list[tuple[str, int]] = []
            for t, c in topic_items:
                if any(t in k and t != k for k, _kc in kept):
                    continue
                kept.append((t, c))
            key_topics = [
                {"term": t, "count": c}
                for t, c in kept[:5]
            ]
            # if fewer than 3 meaningful keywords, return empty rather than pad
            if len(key_topics) < 3:
                key_topics = []

            # keyDates: regex extract relative / absolute date expressions
            date_re = _re.compile(
                r"(\d{1,2}月\d{1,2}[日号]?)"
                r"|(周[一二三四五六日天])"
                r"|(下?周[一二三四五六日天]?)"
                r"|(今天|明天|后天|大后天|昨天|今早|明晚|今晚)"
                r"|(\d{1,2}[:：]\d{2})"
                r"|(本[周个天]|下周|本月|上月|下个月)"
            )
            date_set: list[str] = []
            for body, _l in candidates:
                for m in date_re.finditer(body):
                    for g in m.groups():
                        if g and g not in date_set:
                            date_set.append(g)
                            break
            key_dates = date_set[:10]


            # todos: prefer LLM-filtered rows from the tasks table; fall back to
            # the live rule-based extractor when the conversation has none yet.
            todo_rows = connection.execute(
                """
                SELECT task_id, content, sender_name, sent_at, status, priority
                FROM tasks
                WHERE conversation_id = ? AND status = 'todo'
                ORDER BY sent_at DESC
                LIMIT 10
                """,
                (conversation_id,),
            ).fetchall()
            if todo_rows:
                todos = [
                    {
                        "taskId": r["task_id"],
                        "content": r["content"],
                        "senderName": r["sender_name"],
                        "sentAt": r["sent_at"],
                        "status": r["status"],
                        "priority": r["priority"],
                    }
                    for r in todo_rows
                ]
            else:
                from .tasks import extract_todos_from_rows
                todos = extract_todos_from_rows(
                    connection, conversation_id, limit=200,
                )

            # files: attachment count + recent file names
            file_rows = connection.execute(
                """
                SELECT m.id, m.sender_name, m.sent_at, a.file_name, a.byte_size
                FROM messages m
                LEFT JOIN message_parts mp ON mp.message_id = m.id
                LEFT JOIN assets a ON a.part_id = mp.id
                WHERE m.conversation_id = ? AND m.type = 'attachment'
                ORDER BY m.sent_at DESC
                LIMIT 3
                """,
                (conversation_id,),
            ).fetchall()
            file_count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND type = 'attachment'",
                (conversation_id,),
            ).fetchone()[0]
            files = {
                "count": file_count,
                "recent": [
                    {
                        "id": r["id"],
                        "fileName": r["file_name"] or "[附件]",
                        "fileSize": r["byte_size"],
                        "senderName": r["sender_name"],
                        "sentAt": r["sent_at"],
                    }
                    for r in file_rows
                ],
            }

            # links: URL count + recent 3
            url_re = _re.compile(r"https?://[^\s\u4e00-\u9fa5，。！、）)】\]\"'<>]+")
            link_map: dict[str, dict[str, Any]] = {}
            link_rows = connection.execute(
                """
                SELECT sender_name, sent_at, content
                FROM messages
                WHERE conversation_id = ? AND type = 'text'
                  AND content LIKE '%http%'
                ORDER BY sent_at DESC
                LIMIT 500
                """,
                (conversation_id,),
            ).fetchall()
            for r in link_rows:
                for url in url_re.findall(r["content"] or ""):
                    entry = link_map.get(url)
                    if entry is None:
                        link_map[url] = {
                            "url": url,
                            "senderName": r["sender_name"],
                            "sentAt": r["sent_at"],
                            "count": 1,
                        }
                    else:
                        entry["count"] += 1
            links_sorted = sorted(
                link_map.values(), key=lambda e: -e["sentAt"]
            )
            links = {"count": len(link_map), "recent": links_sorted[:3]}

            # media count: image/voice/video/attachment; the UI needs type records
            # even when the source asset cannot be opened locally.
            media_counts: dict[str, int] = {}
            for r in connection.execute(
                """
                SELECT type, COUNT(*) AS c
                FROM messages
                WHERE conversation_id = ? AND type IN ('image','voice','video','attachment')
                GROUP BY type
                """,
                (conversation_id,),
            ).fetchall():
                media_counts[r["type"]] = r["c"]

            last_active = conv["last_message_at"] if "last_message_at" in conv.keys() else None
            last_active_row = connection.execute(
                "SELECT last_message_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            last_active = last_active_row[0] if last_active_row else None

        return {
            "conversationId": conversation_id,
            "summary": summary,
            "importantMessages": important_messages,
            "keyPeople": key_people,
            "keyTopics": key_topics,
            "keyDates": key_dates,
            "todos": todos,
            "files": files,
            "links": links,
            "mediaCount": media_counts,
            "lastActive": last_active,
        }

    @router.get("/conversations/{conversation_id}/media", response_model=None)
    def conversation_media(
        conversation_id: str,
        kind: str = "all",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any] | JSONResponse:
        """List media messages (image/voice/video/attachment) in a conversation."""
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            conv = connection.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conv is None:
                return _not_found_response()

            type_map = {
                "image": ("image",),
                "voice": ("voice",),
                "video": ("video",),
                "file": ("attachment",),
                "all": ("image", "voice", "video", "attachment"),
            }
            wanted = type_map.get(kind, ("image", "voice", "video", "attachment"))
            placeholders = ", ".join("?" for _ in wanted)

            rows = connection.execute(
                f"""
                SELECT m.id, m.type, m.content, m.sender_name, m.sent_at,
                       (SELECT a.file_name
                          FROM message_parts mp
                          JOIN assets a ON a.part_id = mp.id
                         WHERE mp.message_id = m.id
                         ORDER BY a.id LIMIT 1) AS file_name,
                       (SELECT a.byte_size
                          FROM message_parts mp
                          JOIN assets a ON a.part_id = mp.id
                         WHERE mp.message_id = m.id
                         ORDER BY a.id LIMIT 1) AS byte_size,
                       (SELECT a.local_path
                          FROM message_parts mp
                          JOIN assets a ON a.part_id = mp.id
                         WHERE mp.message_id = m.id
                         ORDER BY a.id LIMIT 1) AS local_path,
                       (SELECT a.status
                          FROM message_parts mp
                          JOIN assets a ON a.part_id = mp.id
                         WHERE mp.message_id = m.id
                         ORDER BY a.id LIMIT 1) AS asset_status,
                       (SELECT a.id
                          FROM message_parts mp
                          JOIN assets a ON a.part_id = mp.id
                         WHERE mp.message_id = m.id
                         ORDER BY a.id LIMIT 1) AS asset_id
                FROM messages m
                WHERE m.conversation_id = ? AND m.type IN ({placeholders})
                ORDER BY m.sent_at DESC
                LIMIT ?
                """,
                (conversation_id, *wanted, limit),
            ).fetchall()

            items = []
            for r in rows:
                has_local = (
                    r["asset_status"] == "resolved"
                    and bool(r["local_path"])
                    and asset_root is not None
                )
                if has_local:
                    candidate = (asset_root / r["local_path"]).resolve()
                    try:
                        candidate.relative_to(asset_root.resolve())
                        has_local = candidate.is_file()
                    except ValueError:
                        has_local = False
                items.append({
                    "id": r["id"],
                    "type": r["type"],
                    "content": r["content"] or "",
                    "senderName": r["sender_name"],
                    "sentAt": r["sent_at"],
                    "fileName": r["file_name"],
                    "fileSize": r["byte_size"],
                    "hasLocalFile": bool(has_local),
                    "assetId": r["asset_id"],
                })

        return {"items": items, "total": len(items)}

    @router.get("/conversations/{conversation_id}/links", response_model=None)
    def conversation_links(
        conversation_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any] | JSONResponse:
        """List deduplicated links extracted from a conversation's text messages."""
        import re as _re
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row

            conv = connection.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conv is None:
                return _not_found_response()

            url_re = _re.compile(r"https?://[^\s\u4e00-\u9fa5，。！、）)】\]\"'<>]+")
            rows = connection.execute(
                """
                SELECT sender_name, sent_at, content
                FROM messages
                WHERE conversation_id = ? AND type = 'text'
                  AND content LIKE '%http%'
                ORDER BY sent_at DESC
                LIMIT 1000
                """,
                (conversation_id,),
            ).fetchall()

            link_map: dict[str, dict[str, Any]] = {}
            for r in rows:
                for url in url_re.findall(r["content"] or ""):
                    entry = link_map.get(url)
                    if entry is None:
                        # title: 40 chars of surrounding text before the url
                        idx = (r["content"] or "").find(url)
                        ctx = (r["content"] or "")[max(0, idx - 40):idx].strip()
                        link_map[url] = {
                            "url": url,
                            "title": ctx[-40:] if ctx else None,
                            "senderName": r["sender_name"],
                            "sentAt": r["sent_at"],
                            "count": 1,
                        }
                    else:
                        entry["count"] += 1

            items = sorted(link_map.values(), key=lambda e: -e["sentAt"])[:limit]

        return {"items": items, "total": len(link_map)}


    return router


def api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": {},
            }
        },
    )


# _not_found_response imported from ._errors
_ = api_error_response(
    status_code=404,
    code="CONVERSATION_NOT_FOUND",
    message="未找到会话。",
    )


async def _validation_error_handler(
    _request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return api_error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="请求参数无效。",
    )
