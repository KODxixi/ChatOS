from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import Literal


ConnectionFactory = Callable[[], sqlite3.Connection]
Clock = Callable[[], int]
IdFactory = Callable[[], str]


class AgentThreadError(RuntimeError):
    code = "AGENT_THREAD_ERROR"


class KnowledgeScopeValidationError(AgentThreadError):
    code = "KNOWLEDGE_SCOPE_INVALID"


class AgentThreadNotFound(AgentThreadError):
    code = "AGENT_THREAD_NOT_FOUND"


class AgentThreadBusy(AgentThreadError):
    code = "AGENT_THREAD_BUSY"


class AgentThreadArchived(AgentThreadError):
    code = "AGENT_THREAD_ARCHIVED"


class AgentThreadInvalidCursor(AgentThreadError):
    code = "AGENT_THREAD_INVALID_CURSOR"


class AgentThreadValidationError(AgentThreadError):
    code = "AGENT_THREAD_INVALID"


class AgentRunStateError(AgentThreadError):
    code = "AGENT_RUN_STATE_INVALID"


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    mode: Literal["global", "selection", "project"]
    project_id: str | None = None
    source_app_ids: tuple[str, ...] = ()
    source_account_ids: tuple[str, ...] = ()
    conversation_ids: tuple[str, ...] = ()
    message_ids: tuple[int, ...] = ()
    conversation_kinds: tuple[Literal["direct", "group"], ...] = ()
    content_kinds: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    project_snapshot: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.mode == "global":
            return {"mode": "global"}
        if self.mode == "project":
            result: dict[str, object] = {
                "mode": "project",
                "projectId": self.project_id,
            }
            if self.project_snapshot:
                result["conversationIds"] = list(self.conversation_ids)
            return result
        result: dict[str, object] = {"mode": "selection"}
        if self.source_app_ids:
            result["sourceAppIds"] = list(self.source_app_ids)
        if self.source_account_ids:
            result["sourceAccountIds"] = list(self.source_account_ids)
        if self.conversation_ids:
            result["conversationIds"] = list(self.conversation_ids)
        if self.message_ids:
            result["messageIds"] = list(self.message_ids)
        if self.conversation_kinds:
            result["conversationKinds"] = list(self.conversation_kinds)
        if self.content_kinds:
            result["contentKinds"] = list(self.content_kinds)
        if self.node_ids:
            result["nodeIds"] = list(self.node_ids)
        return result

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class AgentRun:
    thread_id: str
    user_turn_id: str
    assistant_turn_id: str
    model_profile_id: str
    scope: KnowledgeScope


@dataclass(frozen=True, slots=True)
class AgentCitation:
    conversation_id: str
    message_id: int
    part_id: int | None
    sender_name: str
    sent_at: int
    excerpt: str

    def to_dict(self) -> dict[str, object]:
        return {
            "conversationId": self.conversation_id,
            "messageId": self.message_id,
            "partId": self.part_id,
            "senderName": self.sender_name,
            "sentAt": self.sent_at,
            "excerpt": self.excerpt,
        }


def normalize_knowledge_scope(
    value: object,
    *,
    allow_project_snapshot: bool = False,
) -> KnowledgeScope:
    if isinstance(value, KnowledgeScope):
        return value
    if not isinstance(value, Mapping):
        raise KnowledgeScopeValidationError(
            "knowledge scope must be an object"
        )
    allowed = {
        "mode",
        "projectId",
        "sourceAppIds",
        "sourceAccountIds",
        "conversationIds",
        "messageIds",
        "conversationKinds",
        "contentKinds",
        "nodeIds",
    }
    if any(key not in allowed for key in value):
        raise KnowledgeScopeValidationError(
            "knowledge scope contains an unsupported filter"
        )
    mode = value.get("mode")
    filter_keys = set(value) - {"mode"}
    if mode == "global":
        if filter_keys:
            raise KnowledgeScopeValidationError(
                "global knowledge scope cannot contain filters"
            )
        return KnowledgeScope(mode="global")
    if mode == "project":
        allowed_project_keys = {"projectId"}
        if allow_project_snapshot:
            allowed_project_keys.add("conversationIds")
        if filter_keys - allowed_project_keys:
            raise KnowledgeScopeValidationError(
                "project knowledge scope cannot contain filters"
            )
        project_id = _required_text(value.get("projectId"), "project id")
        project_snapshot = "conversationIds" in value
        conversation_ids = (
            _identifier_list(value.get("conversationIds"), "conversation ids")
            if project_snapshot and allow_project_snapshot
            else ()
        )
        return KnowledgeScope(
            mode="project",
            project_id=project_id,
            conversation_ids=conversation_ids,
            project_snapshot=project_snapshot,
        )
    if mode != "selection":
        raise KnowledgeScopeValidationError(
            "knowledge scope mode is invalid"
        )
    if "projectId" in filter_keys:
        raise KnowledgeScopeValidationError(
            "selection knowledge scope cannot contain a project id"
        )

    source_app_ids = _identifier_list(
        value.get("sourceAppIds"), "source app ids"
    )
    source_account_ids = _identifier_list(
        value.get("sourceAccountIds"), "source account ids"
    )
    conversation_ids = _identifier_list(
        value.get("conversationIds"), "conversation ids"
    )
    message_ids = _integer_id_list(value.get("messageIds"), "message ids")
    node_ids = _identifier_list(value.get("nodeIds"), "node ids")
    conversation_kinds = _identifier_list(
        value.get("conversationKinds"), "conversation kinds"
    )
    if any(kind not in {"direct", "group"} for kind in conversation_kinds):
        raise KnowledgeScopeValidationError(
            "conversation kind filter is invalid"
        )
    content_kinds = _identifier_list(
        value.get("contentKinds"), "content kinds"
    )
    valid_content_kinds = {
        "text", "link", "image", "file", "audio", "video",
        "sticker", "attachment", "unsupported",
    }
    if any(kind not in valid_content_kinds for kind in content_kinds):
        raise KnowledgeScopeValidationError("content kind filter is invalid")
    if not any(
        (
            source_app_ids,
            source_account_ids,
            conversation_ids,
            message_ids,
            conversation_kinds,
            content_kinds,
            node_ids,
        )
    ):
        raise KnowledgeScopeValidationError(
            "selection knowledge scope requires a filter"
        )
    return KnowledgeScope(
        mode="selection",
        source_app_ids=source_app_ids,
        source_account_ids=source_account_ids,
        conversation_ids=conversation_ids,
        message_ids=message_ids,
        conversation_kinds=conversation_kinds,
        content_kinds=content_kinds,
        node_ids=node_ids,
    )


class AgentThreadService:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def create_thread(
        self,
        *,
        title: str | None,
        scope: object,
        project_id: str | None = None,
    ) -> dict[str, object]:
        normalized_scope = normalize_knowledge_scope(scope)
        normalized_title = (
            "新提问" if title is None else _thread_title(title)
        )
        normalized_project_id = project_id.strip() if project_id is not None else None
        if normalized_project_id == "":
            raise AgentThreadValidationError("project id is invalid")
        if normalized_scope.mode == "project":
            if (
                normalized_project_id is not None
                and normalized_project_id != normalized_scope.project_id
            ):
                raise AgentThreadValidationError("project id does not match scope")
            normalized_project_id = normalized_scope.project_id
        thread_id = self._id_factory()
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            self._validate_project(connection, normalized_project_id)
            connection.execute(
                """
                INSERT INTO agent_threads(
                    id, title, default_scope_json, project_id,
                    is_archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    thread_id,
                    normalized_title,
                    normalized_scope.to_json(),
                    normalized_project_id,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get_thread(thread_id)

    def list_threads(
        self,
        *,
        limit: int,
        cursor: str | None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise AgentThreadValidationError("thread page limit is invalid")
        cursor_values = _decode_thread_cursor(cursor) if cursor else None
        parameters: list[object] = []
        where_parts: list[str] = []
        if project_id is not None:
            normalized_project_id = project_id.strip()
            if not normalized_project_id:
                raise AgentThreadValidationError("project id is invalid")
            where_parts.append("agent_threads.project_id = ?")
            parameters.append(normalized_project_id)
        if cursor_values is not None:
            cursor_updated_at, cursor_id = cursor_values
            where_parts.append("(updated_at < ? OR (updated_at = ? AND id < ?))")
            parameters.extend((cursor_updated_at, cursor_updated_at, cursor_id))
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        parameters.append(limit + 1)
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT agent_threads.*,
                       (
                           SELECT COUNT(*) FROM agent_turns
                           WHERE agent_turns.thread_id = agent_threads.id
                       ) AS turn_count
                FROM agent_threads
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page_rows[-1]
            next_cursor = _encode_thread_cursor(
                int(last["updated_at"]),
                str(last["id"]),
            )
        return {
            "items": [self._thread_summary(row) for row in page_rows],
            "nextCursor": next_cursor,
        }

    def update_thread(
        self,
        thread_id: str,
        updates: Mapping[str, object],
    ) -> dict[str, object]:
        normalized_id = _required_text(thread_id, "thread id")
        if not updates or any(key not in {"title", "isArchived", "projectId"} for key in updates):
            raise AgentThreadValidationError("thread update is invalid")
        assignments: list[str] = []
        parameters: list[object] = []
        if "title" in updates:
            assignments.append("title = ?")
            parameters.append(_thread_title(updates["title"]))
        if "isArchived" in updates:
            archived = updates["isArchived"]
            if not isinstance(archived, bool):
                raise AgentThreadValidationError(
                    "thread archived flag is invalid"
                )
            assignments.append("is_archived = ?")
            parameters.append(int(archived))
        if "projectId" in updates:
            project_id = updates["projectId"]
            if project_id is not None and (
                not isinstance(project_id, str) or not project_id.strip()
            ):
                raise AgentThreadValidationError("project id is invalid")
            normalized_project_id = project_id.strip() if isinstance(project_id, str) else None
            with closing(self._connection_factory()) as validation_connection:
                self._validate_project(validation_connection, normalized_project_id)
            assignments.append("project_id = ?")
            parameters.append(normalized_project_id)
        assignments.append("updated_at = ?")
        parameters.extend((self._clock(), normalized_id))
        with closing(self._connection_factory()) as connection:
            updated = connection.execute(
                f"""
                UPDATE agent_threads SET {", ".join(assignments)}
                WHERE id = ?
                """,
                parameters,
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise AgentThreadNotFound("agent thread was not found")
            connection.commit()
        return self.get_thread(normalized_id)

    def delete_thread(self, thread_id: str) -> None:
        normalized_id = _required_text(thread_id, "thread id")
        with closing(self._connection_factory()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT 1 FROM agent_turns
                WHERE thread_id = ? AND role = 'assistant'
                  AND status = 'streaming'
                """,
                (normalized_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise AgentThreadBusy("agent thread already has an active run")
            deleted = connection.execute(
                "DELETE FROM agent_threads WHERE id = ?",
                (normalized_id,),
            )
            if deleted.rowcount != 1:
                connection.rollback()
                raise AgentThreadNotFound("agent thread was not found")
            connection.commit()

    def recover_interrupted_runs(self) -> int:
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT DISTINCT thread_id FROM agent_turns
                WHERE role = 'assistant' AND status = 'streaming'
                """
            ).fetchall()
            interrupted = connection.execute(
                """
                UPDATE agent_turns
                SET content = '', status = 'interrupted',
                    error_code = 'APP_RESTARTED', completed_at = ?
                WHERE role = 'assistant' AND status = 'streaming'
                """,
                (now,),
            ).rowcount
            for row in rows:
                connection.execute(
                    "UPDATE agent_threads SET updated_at = ? WHERE id = ?",
                    (now, row["thread_id"]),
                )
            connection.commit()
        return interrupted

    @staticmethod
    def _thread_summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "projectId": str(row["project_id"]) if row["project_id"] is not None else None,
            "scope": json.loads(str(row["default_scope_json"])),
            "isArchived": bool(row["is_archived"]),
            "turnCount": int(row["turn_count"]),
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    def start_run(
        self,
        *,
        thread_id: str | None,
        question: str,
        model_profile_id: str,
        scope: object,
        project_id: str | None = None,
        model_options: Mapping[str, object] | None = None,
        retrieval_summary: Mapping[str, object] | None = None,
    ) -> AgentRun:
        normalized_question = _required_text(question, "question")
        normalized_profile_id = _required_text(
            model_profile_id, "model profile id"
        )
        normalized_scope = normalize_knowledge_scope(scope)
        normalized_project_id = project_id.strip() if project_id is not None else None
        if normalized_project_id == "":
            raise AgentThreadValidationError("project id is invalid")
        if normalized_scope.mode == "project":
            if (
                normalized_project_id is not None
                and normalized_project_id != normalized_scope.project_id
            ):
                raise AgentThreadValidationError("project id does not match scope")
            normalized_project_id = normalized_scope.project_id
        scope_json = normalized_scope.to_json()
        model_options_json = _json_object(
            model_options or {},
            "model options",
        )
        retrieval_summary_json = _json_object(
            retrieval_summary or {},
            "retrieval summary",
        )
        now = self._clock()

        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._validate_project(connection, normalized_project_id)
            if thread_id is None:
                resolved_thread_id = self._id_factory()
                connection.execute(
                    """
                    INSERT INTO agent_threads(
                        id, title, default_scope_json, project_id,
                        is_archived, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        resolved_thread_id,
                        normalized_question[:80],
                        scope_json,
                        normalized_project_id,
                        now,
                        now,
                    ),
                )
            else:
                resolved_thread_id = _required_text(thread_id, "thread id")
                row = connection.execute(
                    """
                    SELECT default_scope_json, is_archived
                    FROM agent_threads
                    WHERE id = ?
                    """,
                    (resolved_thread_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise AgentThreadNotFound("agent thread was not found")
                if bool(row["is_archived"]):
                    connection.rollback()
                    raise AgentThreadArchived("agent thread is archived")
                active = connection.execute(
                    """
                    SELECT 1 FROM agent_turns
                    WHERE thread_id = ?
                      AND role = 'assistant'
                      AND status = 'streaming'
                    """,
                    (resolved_thread_id,),
                ).fetchone()
                if active is not None:
                    connection.rollback()
                    raise AgentThreadBusy("agent thread already has an active run")
                if normalized_project_id is not None:
                    connection.execute(
                        "UPDATE agent_threads SET project_id = ? WHERE id = ?",
                        (normalized_project_id, resolved_thread_id),
                    )

            user_turn_id = self._id_factory()
            assistant_turn_id = self._id_factory()
            connection.execute(
                """
                INSERT INTO agent_turns(
                    id, thread_id, role, content, status,
                    model_profile_id, scope_json, created_at, completed_at
                ) VALUES (?, ?, 'user', ?, 'complete', NULL, ?, ?, ?)
                """,
                (
                    user_turn_id,
                    resolved_thread_id,
                    normalized_question,
                    scope_json,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_turns(
                    id, thread_id, role, content, status,
                    model_profile_id, scope_json, created_at,
                    model_options_json, retrieval_summary_json
                ) VALUES (?, ?, 'assistant', '', 'streaming', ?, ?, ?, ?, ?)
                """,
                (
                    assistant_turn_id,
                    resolved_thread_id,
                    normalized_profile_id,
                    scope_json,
                    now,
                    model_options_json,
                    retrieval_summary_json,
                ),
            )
            connection.execute(
                """
                UPDATE agent_threads
                SET default_scope_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (scope_json, now, resolved_thread_id),
            )
            connection.commit()
        return AgentRun(
            thread_id=resolved_thread_id,
            user_turn_id=user_turn_id,
            assistant_turn_id=assistant_turn_id,
            model_profile_id=normalized_profile_id,
            scope=normalized_scope,
        )

    def complete_run(
        self,
        run: AgentRun,
        *,
        answer: str,
        citations: Sequence[AgentCitation],
        grounding_status: str = "unknown",
        retrieval_summary: Mapping[str, object] | None = None,
    ) -> None:
        if grounding_status not in {
            "unknown",
            "grounded",
            "insufficient",
            "conflicted",
            "invalid",
        }:
            raise AgentThreadValidationError("grounding status is invalid")
        retrieval_summary_json = (
            _json_object(retrieval_summary, "retrieval summary")
            if retrieval_summary is not None
            else None
        )
        if run.scope.mode == "project":
            allowed_conversations = set(run.scope.conversation_ids)
            if any(
                citation.conversation_id not in allowed_conversations
                for citation in citations
            ):
                raise KnowledgeScopeValidationError(
                    "citation is outside the project scope snapshot"
                )
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if run.scope.mode == "project":
                allowed_conversations = set(run.scope.conversation_ids)
                for citation in citations:
                    source = connection.execute(
                        """
                        SELECT messages.conversation_id
                        FROM messages
                        WHERE messages.id = ?
                          AND (
                              ? IS NULL
                              OR EXISTS (
                                  SELECT 1 FROM message_parts
                                  WHERE message_parts.id = ?
                                    AND message_parts.message_id = messages.id
                              )
                          )
                        """,
                        (
                            citation.message_id,
                            citation.part_id,
                            citation.part_id,
                        ),
                    ).fetchone()
                    if (
                        source is None
                        or str(source[0]) != citation.conversation_id
                        or str(source[0]) not in allowed_conversations
                    ):
                        connection.rollback()
                        raise KnowledgeScopeValidationError(
                            "citation is outside the project scope snapshot"
                        )
            updated = connection.execute(
                """
                UPDATE agent_turns
                SET content = ?, status = 'complete', completed_at = ?,
                    error_code = NULL, grounding_status = ?,
                    retrieval_summary_json = COALESCE(?, retrieval_summary_json)
                WHERE id = ? AND thread_id = ?
                  AND role = 'assistant' AND status = 'streaming'
                """,
                (
                    answer,
                    now,
                    grounding_status,
                    retrieval_summary_json,
                    run.assistant_turn_id,
                    run.thread_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise AgentRunStateError("agent run is not streaming")
            for ordinal, citation in enumerate(citations):
                connection.execute(
                    """
                    INSERT INTO agent_citations(
                        assistant_turn_id, ordinal, conversation_id,
                        message_id, message_part_id, sender_name,
                        sent_at, excerpt
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.assistant_turn_id,
                        ordinal,
                        citation.conversation_id,
                        citation.message_id,
                        citation.part_id,
                        citation.sender_name,
                        citation.sent_at,
                        citation.excerpt,
                    ),
                )
            connection.execute(
                "UPDATE agent_threads SET updated_at = ? WHERE id = ?",
                (now, run.thread_id),
            )
            connection.commit()

    def cancel_run(self, run: AgentRun) -> None:
        self._finish_without_content(run, status="cancelled", error_code=None)

    def fail_run(self, run: AgentRun, error_code: str) -> None:
        self._finish_without_content(
            run,
            status="error",
            error_code=_required_text(error_code, "error code"),
        )

    def get_thread(self, thread_id: str) -> dict[str, object]:
        normalized_id = _required_text(thread_id, "thread id")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            thread = connection.execute(
                "SELECT * FROM agent_threads WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if thread is None:
                raise AgentThreadNotFound("agent thread was not found")
            turns = connection.execute(
                """
                SELECT rowid AS storage_order, *
                FROM agent_turns
                WHERE thread_id = ?
                ORDER BY created_at ASC, storage_order ASC
                """,
                (normalized_id,),
            ).fetchall()
            response_turns = [
                self._turn_response(connection, row) for row in turns
            ]
        return {
            "id": str(thread["id"]),
            "title": str(thread["title"]),
            "scope": json.loads(str(thread["default_scope_json"])),
            "projectId": str(thread["project_id"]) if thread["project_id"] is not None else None,
            "isArchived": bool(thread["is_archived"]),
            "createdAt": int(thread["created_at"]),
            "updatedAt": int(thread["updated_at"]),
            "turns": response_turns,
        }

    def history_for_run(
        self,
        run: AgentRun,
        *,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise AgentThreadValidationError("agent history limit is invalid")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT role, content, scope_json
                FROM agent_turns
                WHERE thread_id = ? AND status = 'complete'
                  AND rowid < (
                      SELECT rowid FROM agent_turns WHERE id = ?
                  )
                ORDER BY rowid DESC
                """,
                (run.thread_id, run.user_turn_id),
            ).fetchall()
        target_scope = run.scope.to_json()
        history: list[dict[str, str]] = []
        for row in rows:
            try:
                row_scope = normalize_knowledge_scope(
                    json.loads(str(row["scope_json"])),
                    allow_project_snapshot=True,
                ).to_json()
            except (json.JSONDecodeError, KnowledgeScopeValidationError):
                continue
            if row_scope != target_scope:
                continue
            history.append(
                {
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                }
            )
            if len(history) == limit:
                break
        history.reverse()
        return history

    def _finish_without_content(
        self,
        run: AgentRun,
        *,
        status: Literal["cancelled", "error"],
        error_code: str | None,
    ) -> None:
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            updated = connection.execute(
                """
                UPDATE agent_turns
                SET content = '', status = ?, error_code = ?, completed_at = ?
                WHERE id = ? AND thread_id = ?
                  AND role = 'assistant' AND status = 'streaming'
                """,
                (
                    status,
                    error_code,
                    now,
                    run.assistant_turn_id,
                    run.thread_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise AgentRunStateError("agent run is not streaming")
            connection.execute(
                "UPDATE agent_threads SET updated_at = ? WHERE id = ?",
                (now, run.thread_id),
            )
            connection.commit()

    def _turn_response(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, object]:
        citations = connection.execute(
            """
            SELECT * FROM agent_citations
            WHERE assistant_turn_id = ?
            ORDER BY ordinal ASC
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": str(row["id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "status": str(row["status"]),
            "modelProfileId": (
                str(row["model_profile_id"])
                if row["model_profile_id"] is not None
                else None
            ),
            "scope": json.loads(str(row["scope_json"])),
            "modelOptions": json.loads(str(row["model_options_json"])),
            "groundingStatus": str(row["grounding_status"]),
            "retrievalSummary": json.loads(
                str(row["retrieval_summary_json"])
            ),
            "errorCode": (
                str(row["error_code"])
                if row["error_code"] is not None
                else None
            ),
            "createdAt": int(row["created_at"]),
            "completedAt": (
                int(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            "citations": [
                {
                    "conversationId": str(item["conversation_id"]),
                    "messageId": int(item["message_id"]),
                    "partId": (
                        int(item["message_part_id"])
                        if item["message_part_id"] is not None
                        else None
                    ),
                    "senderName": str(item["sender_name"]),
                    "sentAt": int(item["sent_at"]),
                    "excerpt": str(item["excerpt"]),
                }
                for item in citations
            ],
        }

    @staticmethod
    def _validate_project(
        connection: sqlite3.Connection,
        project_id: str | None,
    ) -> None:
        if project_id is not None and connection.execute(
            "SELECT 1 FROM projects WHERE id = ? AND status <> 'archived'",
            (project_id,),
        ).fetchone() is None:
            raise AgentThreadValidationError("project id is invalid")


def _identifier_list(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 200:
        raise KnowledgeScopeValidationError(f"{label} must be a list")
    normalized = tuple(_required_text(item, label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise KnowledgeScopeValidationError(f"{label} must be unique")
    return normalized


def _integer_id_list(value: object, label: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 200:
        raise KnowledgeScopeValidationError(f"{label} must be a list")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item <= 0
        for item in value
    ):
        raise KnowledgeScopeValidationError(
            f"{label} must be positive integers"
        )
    normalized = tuple(value)
    if len(normalized) != len(set(normalized)):
        raise KnowledgeScopeValidationError(f"{label} must be unique")
    return normalized


def _json_object(value: Mapping[str, object], label: str) -> str:
    if not isinstance(value, Mapping):
        raise AgentThreadValidationError(f"{label} is invalid")
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AgentThreadValidationError(f"{label} is invalid") from exc


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeScopeValidationError(f"{label} must not be blank")
    return value.strip()


def _thread_title(value: object) -> str:
    if not isinstance(value, str):
        raise AgentThreadValidationError("thread title is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise AgentThreadValidationError("thread title is invalid")
    return normalized


def _encode_thread_cursor(updated_at: int, thread_id: str) -> str:
    payload = json.dumps(
        ["agent-thread", updated_at, thread_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_thread_cursor(cursor: str) -> tuple[int, str]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 2_048:
        raise AgentThreadInvalidCursor("thread cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise AgentThreadInvalidCursor("thread cursor is invalid") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 3
        or payload[0] != "agent-thread"
        or isinstance(payload[1], bool)
        or not isinstance(payload[1], int)
        or not isinstance(payload[2], str)
        or not payload[2]
    ):
        raise AgentThreadInvalidCursor("thread cursor is invalid")
    return payload[1], payload[2]
