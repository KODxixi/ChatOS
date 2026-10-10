from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from .conversation_service import ConnectionFactory


Clock = Callable[[], int]
ProjectAssignmentExtractor = Callable[
    [dict[str, object]],
    Sequence[Mapping[str, object]],
]
_MEMORY_TYPES = {"fact", "preference", "goal", "decision", "constraint", "risk"}
_MEMORY_STATUSES = {"suggested", "confirmed", "rejected", "superseded", "archived"}


class GchatValidationError(ValueError):
    pass


class GchatNotFound(LookupError):
    pass


class GchatService:
    """Local, evidence-first memory and project service used by Gchat."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        clock: Clock | None = None,
        project_assignment_extractor: ProjectAssignmentExtractor | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._project_assignment_extractor = project_assignment_extractor

    @contextmanager
    def _open(self) -> Iterable[sqlite3.Connection]:
        connection = self._connection_factory()
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        name = _text(name, "project name", 200)
        description = _text(description, "project description", 10_000, allow_empty=True)
        now = self._clock()
        project_id = f"project-{uuid.uuid4().hex}"
        with self._open() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, name, description, now, now),
            )
            connection.execute(
                """
                INSERT INTO project_pulses(project_id, updated_at)
                VALUES (?, ?)
                """,
                (project_id, now),
            )
            connection.commit()
            return self._project(connection, project_id)

    def list_projects(self, *, include_archived: bool = False) -> dict[str, Any]:
        where = "" if include_archived else "WHERE projects.status <> 'archived'"
        with self._open() as connection:
            rows = connection.execute(
                f"""
                SELECT projects.*, COUNT(DISTINCT project_conversations.conversation_id) AS conversation_count,
                       COUNT(DISTINCT gchat_memories.id) FILTER (
                           WHERE gchat_memories.status = 'confirmed'
                       ) AS memory_count,
                       COUNT(DISTINCT gchat_decisions.id) FILTER (
                           WHERE gchat_decisions.status IN ('proposed', 'accepted')
                       ) AS decision_count
                FROM projects
                LEFT JOIN project_conversations
                  ON project_conversations.project_id = projects.id
                LEFT JOIN gchat_memories
                  ON gchat_memories.scope_kind = 'project'
                 AND gchat_memories.scope_id = projects.id
                LEFT JOIN gchat_decisions
                  ON gchat_decisions.project_id = projects.id
                {where}
                GROUP BY projects.id
                ORDER BY projects.updated_at DESC, projects.id DESC
                """
            ).fetchall()
            return {"items": [self._project_from_row(row) for row in rows]}

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = []
        parameters: list[object] = []
        if name is not None:
            assignments.append("name = ?")
            parameters.append(_text(name, "project name", 200))
        if description is not None:
            assignments.append("description = ?")
            parameters.append(_text(description, "project description", 10_000, allow_empty=True))
        if status is not None:
            if status not in {"active", "paused", "archived"}:
                raise GchatValidationError("invalid project status")
            assignments.append("status = ?")
            parameters.append(status)
        if not assignments:
            raise GchatValidationError("project update is empty")
        now = self._clock()
        assignments.append("updated_at = ?")
        parameters.extend((now, project_id))
        with self._open() as connection:
            cursor = connection.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise GchatNotFound("project not found")
            connection.commit()
            return self._project(connection, project_id)

    def assign_conversation(
        self,
        project_id: str,
        conversation_id: str,
        *,
        source: str = "user",
        confidence: float = 1.0,
    ) -> None:
        if source not in {"user", "agent", "rule"}:
            raise GchatValidationError("invalid project assignment source")
        if not 0 <= confidence <= 1:
            raise GchatValidationError("invalid project assignment confidence")
        now = self._clock()
        with self._open() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise GchatNotFound("project not found")
            if connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is None:
                raise GchatNotFound("conversation not found")
            connection.execute(
                """
                INSERT INTO project_conversations(
                    project_id, conversation_id, assignment_source, confidence,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, conversation_id) DO UPDATE SET
                    assignment_source = excluded.assignment_source,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (project_id, conversation_id, source, confidence, now, now),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
            connection.commit()

    def unassign_conversation(self, project_id: str, conversation_id: str) -> None:
        with self._open() as connection:
            connection.execute(
                "DELETE FROM project_conversations WHERE project_id = ? AND conversation_id = ?",
                (project_id, conversation_id),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (self._clock(), project_id))
            connection.commit()

    def generate_insights(
        self,
        *,
        question: str,
        answer: str,
        scope: Mapping[str, object],
        citations: Sequence[Mapping[str, object]],
        source_turn_id: str,
        model_profile_id: str,
    ) -> list[dict[str, Any]]:
        if self._project_assignment_extractor is None or not citations:
            return []
        payload: dict[str, object] = {
            "question": question,
            "answer": answer,
            "scope": dict(scope),
            "citations": [dict(item) for item in citations],
        }
        extracted = self._project_assignment_extractor(payload)
        if isinstance(extracted, (str, bytes)) or not isinstance(
            extracted,
            Sequence,
        ):
            raise GchatValidationError(
                "project assignment extraction is invalid"
            )
        created: list[dict[str, Any]] = []
        created_ids: set[str] = set()
        with self._open() as connection:
            for item in extracted:
                if not isinstance(item, Mapping):
                    continue
                candidate = self._insert_project_assignment_candidate(
                    connection,
                    item,
                    citations=citations,
                    source_turn_id=source_turn_id,
                    model_profile_id=model_profile_id,
                )
                if (
                    candidate is not None
                    and str(candidate["id"]) not in created_ids
                ):
                    created.append(candidate)
                    created_ids.add(str(candidate["id"]))
            connection.commit()
        return created

    def suggest_project_assignments_from_archive(
        self,
        *,
        conversation_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Create reviewable, local-only project suggestions for new archive data.

        This pass deliberately uses deterministic name/content matching. It never
        writes ``project_conversations``; a user confirmation is still required.
        The Gchat insight extractor can enrich the same candidate later.
        """
        normalized_ids = (
            tuple(
                dict.fromkeys(
                    _text(item, "conversation id", 500)
                    for item in conversation_ids
                )
            )
            if conversation_ids is not None
            else None
        )
        if conversation_ids is not None and not normalized_ids:
            return []
        created: list[dict[str, Any]] = []
        with self._open() as connection:
            projects = connection.execute(
                """
                SELECT id, name
                FROM projects
                WHERE status <> 'archived'
                ORDER BY updated_at DESC, id ASC
                """
            ).fetchall()
            if not projects:
                return []
            where = (
                "AND conversations.id IN ({})".format(
                    ",".join("?" for _ in normalized_ids)
                )
                if normalized_ids
                else ""
            )
            parameters: tuple[object, ...] = normalized_ids or ()
            conversations = connection.execute(
                f"""
                SELECT conversations.id, conversations.title
                FROM conversations
                WHERE conversations.kind <> 'system'
                  {where}
                ORDER BY conversations.last_message_at DESC, conversations.id ASC
                """,
                parameters,
            ).fetchall()
            for conversation in conversations:
                conversation_id = str(conversation["id"])
                messages = connection.execute(
                    """
                    SELECT messages.id, messages.sender_name, messages.sent_at,
                           message_parts.id AS part_id,
                           COALESCE(message_parts.text_content, messages.content) AS excerpt
                    FROM messages
                    LEFT JOIN message_parts
                      ON message_parts.message_id = messages.id
                     AND message_parts.ordinal = 0
                    WHERE messages.conversation_id = ?
                      AND messages.type NOT LIKE 'unsupported%'
                    ORDER BY messages.sent_at DESC, messages.id DESC
                    LIMIT 8
                    """,
                    (conversation_id,),
                ).fetchall()
                if not messages:
                    continue
                context = " ".join(
                    [
                        str(conversation["title"] or ""),
                        *(str(message["excerpt"] or "") for message in messages),
                    ]
                )
                best: tuple[float, sqlite3.Row] | None = None
                for project in projects:
                    if connection.execute(
                        """
                        SELECT 1
                        FROM project_assignment_candidates
                        WHERE conversation_id = ?
                          AND suggested_project_id = ?
                        LIMIT 1
                        """,
                        (conversation_id, str(project["id"])),
                    ).fetchone() is not None:
                        continue
                    if connection.execute(
                        """
                        SELECT 1
                        FROM project_conversations
                        WHERE conversation_id = ? AND project_id = ?
                        LIMIT 1
                        """,
                        (conversation_id, str(project["id"])),
                    ).fetchone() is not None:
                        continue
                    score = _project_assignment_match_score(
                        str(project["name"]),
                        context,
                    )
                    if score >= 0.76 and (best is None or score > best[0]):
                        best = (score, project)
                if best is None:
                    continue
                score, project = best
                citations = [
                    {
                        "conversationId": conversation_id,
                        "messageId": int(message["id"]),
                        "partId": (
                            int(message["part_id"])
                            if message["part_id"] is not None
                            else None
                        ),
                    }
                    for message in reversed(messages[:2])
                ]
                candidate = self._insert_project_assignment_candidate(
                    connection,
                    {
                        "conversationId": conversation_id,
                        "suggestedProjectId": str(project["id"]),
                        "suggestedProjectName": str(project["name"]),
                        "confidence": min(0.95, max(0.76, score)),
                    },
                    citations=citations,
                    source_turn_id=(
                        "archive-import-"
                        f"{hashlib.sha256(conversation_id.encode()).hexdigest()[:24]}"
                    ),
                    model_profile_id="local-archive-rules",
                )
                if candidate is not None:
                    created.append(candidate)
            connection.commit()
        return created

    def list_project_assignment_candidates(
        self,
        *,
        status: str | None = "suggested",
    ) -> dict[str, Any]:
        if status is not None and status not in {
            "suggested",
            "confirmed",
            "rejected",
        }:
            raise GchatValidationError("candidate status is invalid")
        where = "" if status is None else "WHERE status = ?"
        parameters: tuple[object, ...] = () if status is None else (status,)
        with self._open() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM project_assignment_candidates
                {where}
                ORDER BY created_at DESC, id DESC
                """,
                parameters,
            ).fetchall()
            return {
                "items": [
                    self._project_assignment_candidate_from_row(
                        connection,
                        row,
                    )
                    for row in rows
                ]
            }

    def confirm_project_assignment_candidate(
        self,
        candidate_id: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = _text(candidate_id, "candidate id", 500)
        selected_project_id = (
            _text(project_id, "project id", 500)
            if project_id is not None
            else None
        )
        now = self._clock()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_assignment_candidates WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise GchatNotFound("project assignment candidate not found")
            if str(row["status"]) == "rejected":
                raise GchatValidationError("rejected candidate cannot be confirmed")
            if str(row["status"]) == "confirmed":
                if (
                    selected_project_id is not None
                    and selected_project_id != str(row["suggested_project_id"])
                ):
                    raise GchatValidationError(
                        "confirmed candidate project cannot change"
                    )
                return self._project_assignment_candidate_from_row(
                    connection,
                    row,
                )

            resolved_project_id = selected_project_id
            if resolved_project_id is None and row["suggested_project_id"] is not None:
                resolved_project_id = str(row["suggested_project_id"])
            if resolved_project_id is None:
                suggested_name = _text(
                    row["suggested_project_name"],
                    "suggested project name",
                    200,
                )
                existing = connection.execute(
                    """
                    SELECT id FROM projects
                    WHERE name = ? AND status <> 'archived'
                    ORDER BY updated_at DESC, id ASC
                    LIMIT 1
                    """,
                    (suggested_name,),
                ).fetchone()
                if existing is not None:
                    resolved_project_id = str(existing[0])
                else:
                    resolved_project_id = f"project-{uuid.uuid4().hex}"
                    connection.execute(
                        """
                        INSERT INTO projects(
                            id, name, description, status,
                            created_at, updated_at
                        ) VALUES (?, ?, '', 'active', ?, ?)
                        """,
                        (resolved_project_id, suggested_name, now, now),
                    )
                    connection.execute(
                        """
                        INSERT INTO project_pulses(project_id, updated_at)
                        VALUES (?, ?)
                        """,
                        (resolved_project_id, now),
                    )
            project = connection.execute(
                """
                SELECT 1 FROM projects
                WHERE id = ? AND status <> 'archived'
                """,
                (resolved_project_id,),
            ).fetchone()
            if project is None:
                raise GchatNotFound("project not found")
            conversation_id = str(row["conversation_id"])
            if connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone() is None:
                raise GchatNotFound("conversation not found")
            connection.execute(
                """
                INSERT INTO project_conversations(
                    project_id, conversation_id, assignment_source,
                    confidence, created_at, updated_at
                ) VALUES (?, ?, 'agent', ?, ?, ?)
                ON CONFLICT(project_id, conversation_id) DO UPDATE SET
                    assignment_source = 'agent',
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_project_id,
                    conversation_id,
                    float(row["confidence"]),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE project_assignment_candidates
                SET suggested_project_id = ?, status = 'confirmed',
                    reviewed_at = ?
                WHERE id = ? AND status = 'suggested'
                """,
                (resolved_project_id, str(now), normalized_id),
            )
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (now, resolved_project_id),
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM project_assignment_candidates WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            assert updated is not None
            return self._project_assignment_candidate_from_row(
                connection,
                updated,
            )

    def reject_project_assignment_candidate(
        self,
        candidate_id: str,
    ) -> dict[str, Any]:
        normalized_id = _text(candidate_id, "candidate id", 500)
        now = self._clock()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_assignment_candidates WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise GchatNotFound("project assignment candidate not found")
            if str(row["status"]) == "confirmed":
                raise GchatValidationError("confirmed candidate cannot be rejected")
            if str(row["status"]) == "suggested":
                connection.execute(
                    """
                    UPDATE project_assignment_candidates
                    SET status = 'rejected', reviewed_at = ?
                    WHERE id = ?
                    """,
                    (str(now), normalized_id),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM project_assignment_candidates WHERE id = ?",
                    (normalized_id,),
                ).fetchone()
                assert row is not None
            return self._project_assignment_candidate_from_row(
                connection,
                row,
            )

    def get_project_pulse(self, project_id: str) -> dict[str, Any]:
        with self._open() as connection:
            project = self._project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM project_pulses WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise GchatNotFound("project pulse not found")
            messages = connection.execute(
                """
                SELECT messages.id, messages.sender_name, messages.sent_at,
                       COALESCE(message_parts.text_content, messages.content) AS excerpt
                FROM project_conversations
                JOIN messages ON messages.conversation_id = project_conversations.conversation_id
                LEFT JOIN message_parts
                  ON message_parts.message_id = messages.id AND message_parts.ordinal = 0
                WHERE project_conversations.project_id = ?
                  AND messages.type NOT LIKE 'unsupported%'
                ORDER BY messages.sent_at DESC, messages.id DESC
                LIMIT 8
                """,
                (project_id,),
            ).fetchall()
            memories = connection.execute(
                """
                SELECT id, content, memory_type, status, confidence, updated_at
                FROM gchat_memories
                WHERE scope_kind = 'project' AND scope_id = ?
                  AND status IN ('suggested', 'confirmed')
                ORDER BY updated_at DESC, id DESC LIMIT 8
                """,
                (project_id,),
            ).fetchall()
            decisions = connection.execute(
                """
                SELECT id, title, decision, status, next_action, due_at, updated_at
                FROM gchat_decisions
                WHERE project_id = ? AND status IN ('proposed', 'accepted')
                ORDER BY updated_at DESC, id DESC LIMIT 8
                """,
                (project_id,),
            ).fetchall()
            return {
                "projectId": project_id,
                "project": project,
                "messageCount": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM project_conversations
                        JOIN messages ON messages.conversation_id = project_conversations.conversation_id
                        WHERE project_conversations.project_id = ?
                        """,
                        (project_id,),
                    ).fetchone()[0]
                ),
                "recentMessages": [
                    {
                        "messageId": int(item["id"]),
                        "senderName": str(item["sender_name"]),
                        "sentAt": int(item["sent_at"]),
                        "excerpt": str(item["excerpt"] or "")[:280],
                    }
                    for item in messages
                ],
                "memories": [dict(item) for item in memories],
                "decisions": [dict(item) for item in decisions],
                "status": str(row["status"]),
                "updatedAt": int(row["updated_at"]),
            }

    def create_memory(
        self,
        *,
        content: str,
        memory_type: str,
        scope: Mapping[str, object],
        evidence: Iterable[Mapping[str, object]] = (),
        status: str = "suggested",
        confidence: float = 0.5,
        evidence_conversation_ids: Sequence[str] | None = None,
        subject_key: str = "",
    ) -> dict[str, Any]:
        """Create a memory proposal.

        subject_key: 同一主体(scope)+同一属性键+同一 memory_type 的新事实进来时，
            旧的 active(suggested/confirmed) 记忆不删除，而是被标记为
            status='superseded' 并通过 superseded_by 指向本新记忆 id。
            留空 '' 表示不做取代（向后兼容，自由文本记忆不会误取代同类）。
        """
        content = _text(content, "memory content", 20_000)
        if memory_type not in _MEMORY_TYPES or status != "suggested":
            raise GchatValidationError("invalid memory type or status")
        scope_kind, scope_id = _memory_scope(scope)
        if not 0 <= confidence <= 1:
            raise GchatValidationError("invalid memory confidence")
        evidence_items = tuple(evidence)
        if not evidence_items:
            raise GchatValidationError("memory evidence is required")
        now = self._clock()
        with self._open() as connection:
            self._validate_scope(connection, scope_kind, scope_id)
            normalized_evidence = self._normalize_evidence(
                connection,
                evidence_items,
                scope_kind=scope_kind,
                scope_id=scope_id,
                allowed_conversation_ids=evidence_conversation_ids,
            )
            source_hash = _proposal_hash(
                {
                    "kind": "memory",
                    "scopeKind": scope_kind,
                    "scopeId": scope_id,
                    "memoryType": memory_type,
                    "content": content,
                    "confidence": confidence,
                    "subjectKey": subject_key,
                },
                normalized_evidence,
            )
            memory_id = f"memory-{source_hash}"
            connection.execute(
                """
                INSERT OR IGNORE INTO gchat_memories(
                    id, scope_kind, scope_id, memory_type, content, status,
                    confidence, source_hash, subject_key, created_at, updated_at, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, 'suggested', ?, ?, ?, ?, ?, NULL)
                """,
                (
                    memory_id,
                    scope_kind,
                    scope_id,
                    memory_type,
                    content,
                    confidence,
                    source_hash,
                    subject_key,
                    now,
                    now,
                ),
            )
            self._insert_evidence(
                connection,
                "gchat_memory_evidence",
                "memory_id",
                memory_id,
                normalized_evidence,
            )
            # 事实演化：同一主体(scope_kind+scope_id)+同一属性键(subject_key)
            # +同一 memory_type 的旧 active 记忆被新事实取代。
            # 绝不物理删除——旧记录保留，仅状态翻为 superseded，并链到新记忆 id。
            if subject_key:
                connection.execute(
                    """
                    UPDATE gchat_memories
                    SET status = 'superseded',
                        superseded_by = ?,
                        updated_at = ?
                    WHERE scope_kind = ?
                      AND scope_id = ?
                      AND memory_type = ?
                      AND subject_key = ?
                      AND id <> ?
                      AND status IN ('suggested', 'confirmed')
                    """,
                    (
                        memory_id,
                        now,
                        scope_kind,
                        scope_id,
                        memory_type,
                        subject_key,
                        memory_id,
                    ),
                )
            connection.commit()
            return self._memory(connection, memory_id)

    def update_memory(self, memory_id: str, *, status: str | None = None, content: str | None = None) -> dict[str, Any]:
        if status is not None and status not in {"suggested", "confirmed", "rejected"}:
            raise GchatValidationError("invalid memory status")
        normalized_content = (
            _text(content, "memory content", 20_000)
            if content is not None
            else None
        )
        with self._open() as connection:
            current = connection.execute(
                "SELECT * FROM gchat_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if current is None:
                raise GchatNotFound("memory not found")
            current_status = str(current["status"])
            if current_status != "suggested":
                if (
                    (status is None or status == current_status)
                    and (
                        normalized_content is None
                        or normalized_content == str(current["content"])
                    )
                ):
                    return self._memory_from_row(connection, current)
                raise GchatValidationError("memory proposal is already resolved")
            if status == "confirmed" and connection.execute(
                "SELECT 1 FROM gchat_memory_evidence WHERE memory_id = ?",
                (memory_id,),
            ).fetchone() is None:
                raise GchatValidationError("memory evidence is required")
        assignments: list[str] = []
        parameters: list[object] = []
        if status is not None:
            assignments.extend(("status = ?", "confirmed_at = ?"))
            parameters.extend((status, self._clock() if status == "confirmed" else None))
        if normalized_content is not None:
            assignments.append("content = ?")
            parameters.append(normalized_content)
        if not assignments:
            raise GchatValidationError("memory update is empty")
        assignments.append("updated_at = ?")
        parameters.extend((self._clock(), memory_id))
        with self._open() as connection:
            cursor = connection.execute(
                f"UPDATE gchat_memories SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise GchatNotFound("memory not found")
            connection.commit()
            return self._memory(connection, memory_id)

    def confirmed_memories_for_scope(
        self,
        scope: Mapping[str, object],
    ) -> dict[str, Any]:
        scope_pairs = _memory_scopes_for_turn(scope)
        if not scope_pairs:
            return {"items": []}
        clauses = ["(scope_kind = ? AND scope_id = ?)" for _ in scope_pairs]
        parameters = [value for pair in scope_pairs for value in pair]
        with self._open() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM gchat_memories
                WHERE status = 'confirmed'
                  AND ({' OR '.join(clauses)})
                ORDER BY updated_at DESC, id DESC
                """,
                parameters,
            ).fetchall()
            return {
                "items": [
                    self._memory_from_row(connection, row) for row in rows
                ]
            }

    def list_memories(
        self,
        *,
        status: str | None = None,
        scope_id: str | None = None,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        """List memories.

        include_superseded: False（默认）时，即使没指定 status，也不返回
            status='superseded' 的旧记忆；显式传 status='superseded' 或
            include_superseded=True 才能看到被取代的历史记录。
        """
        where: list[str] = []
        parameters: list[object] = []
        if status is not None:
            if status not in _MEMORY_STATUSES:
                raise GchatValidationError("invalid memory status")
            where.append("status = ?")
            parameters.append(status)
        elif not include_superseded:
            where.append("status <> 'superseded'")
        if scope_id is not None:
            where.append("scope_id = ?")
            parameters.append(scope_id)
        condition = " AND ".join(where) or "1 = 1"
        with self._open() as connection:
            rows = connection.execute(
                f"SELECT * FROM gchat_memories WHERE {condition} ORDER BY updated_at DESC, id DESC",
                parameters,
            ).fetchall()
            return {"items": [self._memory_from_row(connection, row) for row in rows]}

    def list_memory_evidence(self, memory_id: str) -> dict[str, Any]:
        with self._open() as connection:
            if connection.execute("SELECT 1 FROM gchat_memories WHERE id = ?", (memory_id,)).fetchone() is None:
                raise GchatNotFound("memory not found")
            rows = connection.execute(
                """
                SELECT gchat_memory_evidence.*, messages.sender_name, messages.sent_at,
                       messages.conversation_id,
                       COALESCE(message_parts.text_content, messages.content) AS excerpt
                FROM gchat_memory_evidence
                JOIN messages ON messages.id = gchat_memory_evidence.message_id
                LEFT JOIN message_parts ON message_parts.id = gchat_memory_evidence.part_id
                WHERE memory_id = ?
                ORDER BY messages.sent_at DESC, messages.id DESC
                """,
                (memory_id,),
            ).fetchall()
            return {
                "items": [
                    {
                        "messageId": int(row["message_id"]),
                        "partId": int(row["part_id"]) if row["part_id"] is not None else None,
                        "conversationId": str(row["conversation_id"]),
                        "relation": str(row["relation"]),
                        "senderName": str(row["sender_name"]),
                        "sentAt": int(row["sent_at"]),
                        "excerpt": str(row["excerpt"] or "")[:500],
                    }
                    for row in rows
                ]
            }

    def create_decision(
        self,
        *,
        title: str,
        decision: str,
        project_id: str | None = None,
        rationale: str = "",
        next_action: str = "",
        evidence: Iterable[Mapping[str, object]] = (),
        status: str = "proposed",
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        if status != "proposed" or not 0 <= confidence <= 1:
            raise GchatValidationError("invalid decision status or confidence")
        title = _text(title, "decision title", 200)
        decision = _text(decision, "decision", 20_000)
        rationale = _text(rationale, "decision rationale", 20_000, allow_empty=True)
        next_action = _text(next_action, "decision next action", 20_000, allow_empty=True)
        evidence_items = tuple(evidence)
        if not evidence_items:
            raise GchatValidationError("decision evidence is required")
        now = self._clock()
        with self._open() as connection:
            if project_id is not None and connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise GchatNotFound("project not found")
            normalized_evidence = self._normalize_evidence(
                connection,
                evidence_items,
                scope_kind="project" if project_id is not None else "global",
                scope_id=project_id or "",
            )
            proposal_hash = _proposal_hash(
                {
                    "kind": "decision",
                    "projectId": project_id,
                    "title": title,
                    "decision": decision,
                    "rationale": rationale,
                    "nextAction": next_action,
                    "confidence": confidence,
                },
                normalized_evidence,
            )
            decision_id = f"decision-{proposal_hash}"
            connection.execute(
                """
                INSERT OR IGNORE INTO gchat_decisions(
                    id, project_id, title, decision, rationale, next_action,
                    status, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
                """,
                (decision_id, project_id, title, decision, rationale, next_action, confidence, now, now),
            )
            self._insert_evidence(
                connection,
                "gchat_decision_evidence",
                "decision_id",
                decision_id,
                normalized_evidence,
            )
            connection.commit()
            return self._decision(connection, decision_id)

    def list_decisions(self, *, project_id: str | None = None) -> dict[str, Any]:
        with self._open() as connection:
            if project_id is None:
                rows = connection.execute(
                    "SELECT * FROM gchat_decisions ORDER BY updated_at DESC, id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM gchat_decisions WHERE project_id = ? ORDER BY updated_at DESC, id DESC",
                    (project_id,),
                ).fetchall()
            return {"items": [self._decision_from_row(row) for row in rows]}

    def update_decision(
        self,
        decision_id: str,
        *,
        status: str | None = None,
        decision: str | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in {"proposed", "accepted", "rejected"}:
            raise GchatValidationError("invalid decision status")
        normalized_decision = (
            _text(decision, "decision", 20_000)
            if decision is not None
            else None
        )
        normalized_next_action = (
            _text(
                next_action,
                "decision next action",
                20_000,
                allow_empty=True,
            )
            if next_action is not None
            else None
        )
        with self._open() as connection:
            current = connection.execute(
                "SELECT * FROM gchat_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
            if current is None:
                raise GchatNotFound("decision not found")
            current_status = str(current["status"])
            if current_status != "proposed":
                if (
                    (status is None or status == current_status)
                    and (
                        normalized_decision is None
                        or normalized_decision == str(current["decision"])
                    )
                    and (
                        normalized_next_action is None
                        or normalized_next_action == str(current["next_action"])
                    )
                ):
                    return self._decision_from_row(current)
                raise GchatValidationError("decision proposal is already resolved")
            if status == "accepted" and connection.execute(
                "SELECT 1 FROM gchat_decision_evidence WHERE decision_id = ?",
                (decision_id,),
            ).fetchone() is None:
                raise GchatValidationError("decision evidence is required")
        assignments: list[str] = []
        parameters: list[object] = []
        if status is not None:
            assignments.append("status = ?")
            parameters.append(status)
        if normalized_decision is not None:
            assignments.append("decision = ?")
            parameters.append(normalized_decision)
        if normalized_next_action is not None:
            assignments.append("next_action = ?")
            parameters.append(normalized_next_action)
        if not assignments:
            raise GchatValidationError("decision update is empty")
        assignments.append("updated_at = ?")
        parameters.extend((self._clock(), decision_id))
        with self._open() as connection:
            cursor = connection.execute(
                f"UPDATE gchat_decisions SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise GchatNotFound("decision not found")
            connection.commit()
            return self._decision(connection, decision_id)

    def list_decision_evidence(self, decision_id: str) -> dict[str, Any]:
        with self._open() as connection:
            if connection.execute(
                "SELECT 1 FROM gchat_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone() is None:
                raise GchatNotFound("decision not found")
            rows = connection.execute(
                """
                SELECT gchat_decision_evidence.*, messages.sender_name,
                       messages.sent_at, messages.conversation_id,
                       COALESCE(message_parts.text_content, messages.content)
                           AS excerpt
                FROM gchat_decision_evidence
                JOIN messages
                  ON messages.id = gchat_decision_evidence.message_id
                LEFT JOIN message_parts
                  ON message_parts.id = gchat_decision_evidence.part_id
                WHERE decision_id = ?
                ORDER BY messages.sent_at DESC, messages.id DESC
                """,
                (decision_id,),
            ).fetchall()
            return {"items": [_evidence_response(row) for row in rows]}

    def _insert_project_assignment_candidate(
        self,
        connection: sqlite3.Connection,
        item: Mapping[str, object],
        *,
        citations: Sequence[Mapping[str, object]],
        source_turn_id: str,
        model_profile_id: str,
    ) -> dict[str, Any] | None:
        conversation_id = _text(
            item.get("conversationId"),
            "candidate conversation id",
            500,
        )
        suggested_project_id = _optional_text(
            item.get("suggestedProjectId"),
            "suggested project id",
            500,
        )
        suggested_project_name = _optional_text(
            item.get("suggestedProjectName"),
            "suggested project name",
            200,
        )
        if suggested_project_id is None and suggested_project_name is None:
            return None
        confidence = item.get("confidence", 0.5)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        if connection.execute(
            "SELECT 1 FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone() is None:
            return None
        if suggested_project_id is not None and connection.execute(
            """
            SELECT 1 FROM projects
            WHERE id = ? AND status <> 'archived'
            """,
            (suggested_project_id,),
        ).fetchone() is None:
            return None

        citation_sources: dict[tuple[int, int | None], str] = {}
        for citation in citations:
            message_id = citation.get("messageId")
            part_id = citation.get("partId")
            citation_conversation = citation.get("conversationId")
            if (
                isinstance(message_id, int)
                and not isinstance(message_id, bool)
                and (
                    part_id is None
                    or (
                        isinstance(part_id, int)
                        and not isinstance(part_id, bool)
                    )
                )
                and isinstance(citation_conversation, str)
            ):
                citation_sources[(message_id, part_id)] = citation_conversation
        requested_evidence = item.get("evidence")
        if requested_evidence is None:
            evidence_pairs = tuple(
                key
                for key, source_conversation in citation_sources.items()
                if source_conversation == conversation_id
            )
        elif isinstance(requested_evidence, Sequence) and not isinstance(
            requested_evidence,
            (str, bytes),
        ):
            normalized_pairs: set[tuple[int, int | None]] = set()
            for evidence_item in requested_evidence:
                if not isinstance(evidence_item, Mapping):
                    return None
                message_id = evidence_item.get("messageId")
                part_id = evidence_item.get("partId")
                if (
                    not isinstance(message_id, int)
                    or isinstance(message_id, bool)
                    or (
                        part_id is not None
                        and (
                            not isinstance(part_id, int)
                            or isinstance(part_id, bool)
                        )
                    )
                    or citation_sources.get((message_id, part_id))
                    != conversation_id
                ):
                    return None
                normalized_pairs.add((message_id, part_id))
            evidence_pairs = tuple(
                sorted(normalized_pairs, key=lambda pair: (pair[0], pair[1] or 0))
            )
        else:
            return None
        if not evidence_pairs:
            return None
        for message_id, part_id in evidence_pairs:
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
                            AND message_parts.status = 'resolved'
                      )
                  )
                """,
                (message_id, part_id, part_id),
            ).fetchone()
            if source is None or str(source[0]) != conversation_id:
                return None

        source_hash = _project_assignment_hash(
            conversation_id=conversation_id,
            suggested_project_id=suggested_project_id,
            suggested_project_name=suggested_project_name,
            evidence=evidence_pairs,
        )
        candidate_id = f"project-assignment-{source_hash}"
        now = str(self._clock())
        connection.execute(
            """
            INSERT OR IGNORE INTO project_assignment_candidates(
                id, conversation_id, suggested_project_id,
                suggested_project_name, source_turn_id, confidence,
                model_profile_id, source_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?)
            """,
            (
                candidate_id,
                conversation_id,
                suggested_project_id,
                suggested_project_name,
                _text(source_turn_id, "source turn id", 500),
                float(confidence),
                _text(model_profile_id, "model profile id", 500),
                source_hash,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM project_assignment_candidates WHERE source_hash = ?",
            (source_hash,),
        ).fetchone()
        if row is None or str(row["status"]) != "suggested":
            return None
        for message_id, part_id in evidence_pairs:
            evidence_hash = hashlib.sha256(
                f"{source_hash}\0{message_id}\0{part_id}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT OR IGNORE INTO project_assignment_candidate_evidence(
                    id, candidate_id, message_id, part_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"project-assignment-evidence-{evidence_hash}",
                    str(row["id"]),
                    str(message_id),
                    str(part_id) if part_id is not None else None,
                    now,
                ),
            )
        return self._project_assignment_candidate_from_row(connection, row)

    @staticmethod
    def _project_assignment_candidate_from_row(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        evidence_rows = connection.execute(
            """
            SELECT evidence.message_id, evidence.part_id,
                   messages.conversation_id, messages.sender_name,
                   messages.sent_at,
                   COALESCE(message_parts.text_content, messages.content) AS excerpt
            FROM project_assignment_candidate_evidence AS evidence
            JOIN messages
              ON messages.id = CAST(evidence.message_id AS INTEGER)
            LEFT JOIN message_parts
              ON message_parts.id = CAST(evidence.part_id AS INTEGER)
            WHERE evidence.candidate_id = ?
            ORDER BY messages.sent_at ASC, messages.id ASC,
                     message_parts.id ASC
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": str(row["id"]),
            "conversationId": str(row["conversation_id"]),
            "suggestedProjectId": (
                str(row["suggested_project_id"])
                if row["suggested_project_id"] is not None
                else None
            ),
            "suggestedProjectName": (
                str(row["suggested_project_name"])
                if row["suggested_project_name"] is not None
                else None
            ),
            "sourceTurnId": (
                str(row["source_turn_id"])
                if row["source_turn_id"] is not None
                else None
            ),
            "confidence": float(row["confidence"]),
            "modelProfileId": (
                str(row["model_profile_id"])
                if row["model_profile_id"] is not None
                else None
            ),
            "sourceHash": str(row["source_hash"]),
            "status": str(row["status"]),
            "createdAt": _timestamp_response(row["created_at"]),
            "reviewedAt": _timestamp_response(row["reviewed_at"]),
            "evidence": [_candidate_evidence_response(item) for item in evidence_rows],
        }

    def capture_turn(
        self,
        *,
        question: str,
        answer: str,
        scope: Mapping[str, object],
        citations: Sequence[Mapping[str, object]],
    ) -> dict[str, Any] | None:
        del answer
        normalized = question.strip()
        prefixes = ("请记住：", "请记住:", "记住：", "记住:")
        content = next((normalized[len(prefix):].strip() for prefix in prefixes if normalized.startswith(prefix)), None)
        if not content or not citations:
            return None
        memory_scope = _memory_scope_for_turn(scope)
        if memory_scope is None:
            return None
        return self.create_memory(
            content=content,
            memory_type="fact",
            scope=memory_scope,
            evidence=citations,
            status="suggested",
            confidence=0.85,
            evidence_conversation_ids=(
                scope.get("conversationIds")
                if scope.get("mode") == "project"
                and isinstance(scope.get("conversationIds"), list)
                else None
            ),
        )

    @staticmethod
    def _project(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT projects.*, COUNT(project_conversations.conversation_id) AS conversation_count FROM projects LEFT JOIN project_conversations ON project_conversations.project_id = projects.id WHERE projects.id = ? GROUP BY projects.id",
            (project_id,),
        ).fetchone()
        if row is None:
            raise GchatNotFound("project not found")
        return GchatService._project_from_row(row)

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "conversationCount": int(row["conversation_count"]),
            "memoryCount": int(row["memory_count"]) if "memory_count" in row.keys() else 0,
            "decisionCount": int(row["decision_count"]) if "decision_count" in row.keys() else 0,
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    @staticmethod
    def _memory(connection: sqlite3.Connection, memory_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM gchat_memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise GchatNotFound("memory not found")
        return GchatService._memory_from_row(connection, row)

    @staticmethod
    def _memory_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        evidence_count = connection.execute("SELECT COUNT(*) FROM gchat_memory_evidence WHERE memory_id = ?", (row["id"],)).fetchone()[0]
        return {
            "id": str(row["id"]),
            "scope": {"kind": str(row["scope_kind"]), "id": str(row["scope_id"])},
            "memoryType": str(row["memory_type"]),
            "content": str(row["content"]),
            "status": str(row["status"]),
            "confidence": float(row["confidence"]),
            "subjectKey": str(row["subject_key"]) if "subject_key" in row.keys() else "",
            "supersededBy": (
                str(row["superseded_by"])
                if "superseded_by" in row.keys() and row["superseded_by"] is not None
                else None
            ),
            "evidenceCount": int(evidence_count),
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
            "confirmedAt": int(row["confirmed_at"]) if row["confirmed_at"] is not None else None,
        }

    @staticmethod
    def _decision(connection: sqlite3.Connection, decision_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM gchat_decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            raise GchatNotFound("decision not found")
        return GchatService._decision_from_row(row)

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "projectId": str(row["project_id"]) if row["project_id"] is not None else None,
            "title": str(row["title"]),
            "decision": str(row["decision"]),
            "rationale": str(row["rationale"]),
            "nextAction": str(row["next_action"]),
            "status": str(row["status"]),
            "confidence": float(row["confidence"]),
            "dueAt": int(row["due_at"]) if row["due_at"] is not None else None,
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    @staticmethod
    def _validate_scope(connection: sqlite3.Connection, kind: str, scope_id: str) -> None:
        if kind == "project" and connection.execute("SELECT 1 FROM projects WHERE id = ?", (scope_id,)).fetchone() is None:
            raise GchatNotFound("project not found")
        if kind == "conversation" and connection.execute("SELECT 1 FROM conversations WHERE id = ?", (scope_id,)).fetchone() is None:
            raise GchatNotFound("conversation not found")

    @staticmethod
    def _insert_evidence(
        connection: sqlite3.Connection,
        table: str,
        foreign_key: str,
        item_id: str,
        evidence: Sequence[tuple[int, int | None, str]],
    ) -> None:
        for message_id, part_id, relation in evidence:
            exists = connection.execute(
                f"""
                SELECT 1 FROM {table}
                WHERE {foreign_key} = ? AND message_id = ?
                  AND part_id IS ? AND relation = ?
                """,
                (item_id, message_id, part_id, relation),
            ).fetchone()
            if exists is not None:
                continue
            connection.execute(
                f"INSERT OR IGNORE INTO {table}({foreign_key}, message_id, part_id, relation) VALUES (?, ?, ?, ?)",
                (item_id, message_id, part_id, relation),
            )

    @staticmethod
    def _normalize_evidence(
        connection: sqlite3.Connection,
        evidence: Iterable[Mapping[str, object]],
        *,
        scope_kind: str,
        scope_id: str,
        allowed_conversation_ids: Sequence[str] | None = None,
    ) -> tuple[tuple[int, int | None, str], ...]:
        allowed_conversations = (
            {
                _text(item, "scope conversation id", 500)
                for item in allowed_conversation_ids
            }
            if allowed_conversation_ids is not None
            else None
        )
        normalized: set[tuple[int, int | None, str]] = set()
        for item in evidence:
            message_id = item.get("messageId")
            part_id = item.get("partId")
            relation = item.get("relation", "supports")
            if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
                raise GchatValidationError("evidence messageId is invalid")
            if part_id is not None and (
                not isinstance(part_id, int)
                or isinstance(part_id, bool)
                or part_id <= 0
            ):
                raise GchatValidationError("evidence partId is invalid")
            if relation not in {"supports", "contradicts"}:
                raise GchatValidationError("evidence relation is invalid")
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
                (message_id, part_id, part_id),
            ).fetchone()
            if source is None:
                raise GchatValidationError("evidence source is invalid")
            conversation_id = str(source[0])
            if scope_kind == "conversation" and conversation_id != scope_id:
                raise GchatValidationError("evidence is outside memory scope")
            if scope_kind == "project":
                if allowed_conversations is not None:
                    in_project_scope = conversation_id in allowed_conversations
                else:
                    in_project_scope = connection.execute(
                        """
                        SELECT 1 FROM project_conversations
                        WHERE project_id = ? AND conversation_id = ?
                        """,
                        (scope_id, conversation_id),
                    ).fetchone() is not None
                if not in_project_scope:
                    raise GchatValidationError(
                        "evidence is outside project scope"
                    )
            normalized.add((message_id, part_id, str(relation)))
        if not normalized:
            raise GchatValidationError("proposal evidence is required")
        return tuple(
            sorted(
                normalized,
                key=lambda item: (item[0], item[1] or 0, item[2]),
            )
        )


def _project_assignment_match_score(project_name: str, context: str) -> float:
    normalized_name = project_name.strip().casefold()
    normalized_context = context.casefold()
    if not normalized_name or normalized_name not in normalized_context:
        name_tokens = re.findall(
            r"[a-z0-9][a-z0-9_-]+|[\u4e00-\u9fff]{2,}",
            normalized_name,
        )
        if not name_tokens:
            return 0.0
        context_tokens = set(
            re.findall(
                r"[a-z0-9][a-z0-9_-]+|[\u4e00-\u9fff]{2,}",
                normalized_context,
            )
        )
        overlap = sum(token in context_tokens for token in name_tokens)
        return overlap / len(name_tokens)
    return 0.88 if len(normalized_name) < 3 else 0.94


def _optional_text(value: object, label: str, limit: int) -> str | None:
    if value is None:
        return None
    return _text(value, label, limit)


def _project_assignment_hash(
    *,
    conversation_id: str,
    suggested_project_id: str | None,
    suggested_project_name: str | None,
    evidence: Sequence[tuple[int, int | None]],
) -> str:
    payload = {
        "conversationId": conversation_id,
        "suggestedProjectId": suggested_project_id,
        "suggestedProjectName": suggested_project_name,
        "evidence": [list(item) for item in evidence],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _timestamp_response(value: object) -> int | str | None:
    if value is None:
        return None
    normalized = str(value)
    return int(normalized) if normalized.isdigit() else normalized


def _candidate_evidence_response(row: sqlite3.Row) -> dict[str, object]:
    return {
        "conversationId": str(row["conversation_id"]),
        "messageId": int(row["message_id"]),
        "partId": int(row["part_id"]) if row["part_id"] is not None else None,
        "senderName": str(row["sender_name"]),
        "sentAt": int(row["sent_at"]),
        "excerpt": str(row["excerpt"]),
    }


def _text(value: object, label: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GchatValidationError(f"{label} is invalid")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise GchatValidationError(f"{label} is empty")
    if len(normalized) > limit:
        raise GchatValidationError(f"{label} is too long")
    return normalized


def _memory_scope(scope: Mapping[str, object]) -> tuple[str, str]:
    kind = scope.get("kind")
    identifier = scope.get("id", "")
    if kind not in {"global", "project", "conversation"} or not isinstance(identifier, str):
        raise GchatValidationError("memory scope is invalid")
    if kind == "global":
        identifier = ""
    elif not identifier.strip():
        raise GchatValidationError("memory scope id is empty")
    return str(kind), identifier.strip()


def _memory_scope_for_turn(
    scope: Mapping[str, object],
) -> dict[str, object] | None:
    scope_pairs = _memory_scopes_for_turn(scope)
    if len(scope_pairs) != 1:
        return None
    kind, identifier = scope_pairs[0]
    return {"kind": kind, "id": identifier}


def _memory_scopes_for_turn(
    scope: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    mode = scope.get("mode")
    if mode == "global":
        return (("global", ""),)
    if mode == "project":
        project_id = scope.get("projectId")
        if isinstance(project_id, str) and project_id.strip():
            return (("project", project_id.strip()),)
        return ()
    if mode == "selection":
        conversation_ids = scope.get("conversationIds")
        if not isinstance(conversation_ids, list) or not conversation_ids:
            return ()
        if any(
            not isinstance(item, str) or not item.strip()
            for item in conversation_ids
        ):
            return ()
        return tuple(
            ("conversation", identifier)
            for identifier in sorted({item.strip() for item in conversation_ids})
        )
    return ()


def _proposal_hash(
    payload: Mapping[str, object],
    evidence: Sequence[tuple[int, int | None, str]],
) -> str:
    encoded = json.dumps(
        {
            "payload": dict(payload),
            "evidence": [list(item) for item in evidence],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_response(row: sqlite3.Row) -> dict[str, object]:
    return {
        "messageId": int(row["message_id"]),
        "partId": (
            int(row["part_id"]) if row["part_id"] is not None else None
        ),
        "conversationId": str(row["conversation_id"]),
        "relation": str(row["relation"]),
        "senderName": str(row["sender_name"]),
        "sentAt": int(row["sent_at"]),
        "excerpt": str(row["excerpt"] or "")[:500],
    }
