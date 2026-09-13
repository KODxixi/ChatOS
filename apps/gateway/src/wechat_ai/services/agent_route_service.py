from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing

from .conversation_service import ConnectionFactory


Classifier = Callable[[str], object]
Clock = Callable[[], int]
IdFactory = Callable[[], str]

CONFIDENCE_THRESHOLD = 0.75


class AgentRouteError(ValueError):
    """Base error for invalid project routing operations."""


class AgentRouteCandidateNotFound(LookupError, AgentRouteError):
    """Raised when a route candidate does not exist."""


class AgentRouteCandidateStateError(RuntimeError):
    """Raised when a terminal candidate is reviewed again."""


class AgentRouteService:
    """Resolve a question to a project without mutating thread scope on route."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        classifier: Classifier | None = None,
        *,
        structured_classifier: Classifier | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        if classifier is not None and structured_classifier is not None:
            raise ValueError("provide only one project classifier")
        self._connection_factory = connection_factory
        self._classifier = classifier or structured_classifier
        self._thread_id = _optional_identifier(thread_id, "thread id")
        self._run_id = _optional_identifier(run_id, "run id")
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def route(self, question: str) -> dict[str, object]:
        normalized_question = _required_text(question, "question")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            projects = self._projects(connection)

            selected = self._explicit_project(normalized_question, projects)
            route_source = "explicit"
            confidence = 1.0
            if selected is None:
                route_source = "model"
                model_result = self._classify(normalized_question)
                if model_result is not None:
                    project_id, confidence = model_result
                    selected = next(
                        (
                            project
                            for project in projects
                            if project["id"] == project_id
                        ),
                        None,
                    )

            if selected is None or confidence < CONFIDENCE_THRESHOLD:
                return _global_route()

            candidate = self._create_candidate(
                connection,
                question=normalized_question,
                project=selected,
                confidence=confidence,
                route_source=route_source,
            )
            connection.commit()
            return {
                "scope": {
                    "mode": "project",
                    "projectId": selected["id"],
                },
                "project": {
                    "id": selected["id"],
                    "name": selected["name"],
                },
                "candidate": candidate,
            }

    def confirm(
        self,
        candidate_id: str,
        project_id: str | None = None,
    ) -> dict[str, object]:
        normalized_candidate_id = _required_text(candidate_id, "candidate id")
        normalized_project_id = (
            _required_text(project_id, "project id")
            if project_id is not None
            else None
        )
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            candidate = self._candidate(connection, normalized_candidate_id)
            self._ensure_suggested(candidate)
            selected_project_id = normalized_project_id or str(
                candidate["project_id"]
            )
            project = self._project(connection, selected_project_id)
            status = (
                "changed"
                if selected_project_id != str(candidate["project_id"])
                else "confirmed"
            )
            now = self._clock()
            connection.execute(
                """
                UPDATE agent_route_candidates
                SET project_id = ?, project_name = ?, status = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (
                    project["id"],
                    project["name"],
                    status,
                    now,
                    normalized_candidate_id,
                ),
            )
            thread_id = candidate["thread_id"]
            if thread_id is not None:
                updated = connection.execute(
                    """
                    UPDATE agent_threads
                    SET project_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (project["id"], now, thread_id),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    raise AgentRouteError("agent thread was not found")
            connection.commit()
            reviewed = self._candidate(connection, normalized_candidate_id)
            return _candidate_response(reviewed)

    def reject(self, candidate_id: str) -> dict[str, object]:
        normalized_candidate_id = _required_text(candidate_id, "candidate id")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            candidate = self._candidate(connection, normalized_candidate_id)
            self._ensure_suggested(candidate)
            connection.execute(
                """
                UPDATE agent_route_candidates
                SET status = 'rejected', reviewed_at = ?
                WHERE id = ?
                """,
                (self._clock(), normalized_candidate_id),
            )
            connection.commit()
            reviewed = self._candidate(connection, normalized_candidate_id)
            return _candidate_response(reviewed)

    def list_candidates(self) -> dict[str, object]:
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT *
                FROM agent_route_candidates
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return {"items": [_candidate_response(row) for row in rows]}

    @staticmethod
    def _projects(connection: sqlite3.Connection) -> list[dict[str, str]]:
        rows = connection.execute(
            """
            SELECT id, name
            FROM projects
            WHERE status <> 'archived'
            ORDER BY updated_at DESC, id ASC
            """
        ).fetchall()
        return [
            {"id": str(row["id"]), "name": str(row["name"])}
            for row in rows
        ]

    @staticmethod
    def _explicit_project(
        question: str,
        projects: list[dict[str, str]],
    ) -> dict[str, str] | None:
        normalized_question = question.casefold()
        matches = [
            project
            for project in projects
            if project["name"].casefold() in normalized_question
        ]
        if not matches:
            return None
        return min(matches, key=lambda project: (-len(project["name"]), project["id"]))

    def _classify(self, question: str) -> tuple[str, float] | None:
        if self._classifier is None:
            return None
        try:
            raw = self._classifier(question)
            if isinstance(raw, (str, bytes, bytearray)):
                raw = json.loads(raw)
            if not isinstance(raw, Mapping):
                return None
            project_id = raw.get("projectId", raw.get("project_id"))
            confidence = raw.get("confidence")
            if (
                not isinstance(project_id, str)
                or not project_id.strip()
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                return None
            return project_id.strip(), float(confidence)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _create_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        question: str,
        project: Mapping[str, str],
        confidence: float,
        route_source: str,
    ) -> dict[str, object]:
        run_id = self._ensure_route_run(connection, question)
        candidate_id = self._id_factory()
        connection.execute(
            """
            INSERT INTO agent_route_candidates(
                id, run_id, thread_id, project_id, project_name,
                confidence, route_source, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'suggested', ?)
            """,
            (
                candidate_id,
                run_id,
                self._thread_id,
                project["id"],
                project["name"],
                confidence,
                route_source,
                self._clock(),
            ),
        )
        return _candidate_response(self._candidate(connection, candidate_id))

    def _ensure_route_run(
        self,
        connection: sqlite3.Connection,
        question: str,
    ) -> str:
        run_id = self._run_id or self._id_factory()
        existing = connection.execute(
            "SELECT id FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            used = connection.execute(
                "SELECT 1 FROM agent_route_candidates WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if used is not None:
                run_id = self._id_factory()
                existing = connection.execute(
                    "SELECT id FROM agent_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
        if existing is None:
            if self._thread_id is not None:
                thread = connection.execute(
                    "SELECT 1 FROM agent_threads WHERE id = ?",
                    (self._thread_id,),
                ).fetchone()
                if thread is None:
                    raise AgentRouteError("agent thread was not found")
            now = self._clock()
            connection.execute(
                """
                INSERT INTO agent_runs(
                    id, thread_id, source, request_json, status,
                    answer_text, created_at, finished_at, updated_at
                ) VALUES (?, ?, 'http', ?, 'completed', '', ?, ?, ?)
                """,
                (
                    run_id,
                    self._thread_id,
                    json.dumps(
                        {"question": question, "routeOnly": True},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                    now,
                ),
            )
        return run_id

    @staticmethod
    def _project(
        connection: sqlite3.Connection,
        project_id: str,
    ) -> dict[str, str]:
        row = connection.execute(
            """
            SELECT id, name
            FROM projects
            WHERE id = ? AND status <> 'archived'
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            raise AgentRouteError("project id is invalid")
        return {"id": str(row["id"]), "name": str(row["name"])}

    @staticmethod
    def _candidate(
        connection: sqlite3.Connection,
        candidate_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM agent_route_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise AgentRouteCandidateNotFound(candidate_id)
        return row

    @staticmethod
    def _ensure_suggested(candidate: sqlite3.Row) -> None:
        if candidate["status"] != "suggested":
            raise AgentRouteCandidateStateError(
                f"candidate is already {candidate['status']}"
            )


ProjectRouteService = AgentRouteService


def _global_route() -> dict[str, object]:
    return {
        "scope": {"mode": "global"},
        "project": None,
        "candidate": None,
    }


def _candidate_response(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "runId": str(row["run_id"]),
        "threadId": str(row["thread_id"]) if row["thread_id"] is not None else None,
        "projectId": str(row["project_id"]),
        "projectName": str(row["project_name"]),
        "confidence": float(row["confidence"]),
        "routeSource": str(row["route_source"]),
        "status": str(row["status"]),
        "createdAt": int(row["created_at"]),
        "reviewedAt": (
            int(row["reviewed_at"]) if row["reviewed_at"] is not None else None
        ),
    }


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentRouteError(f"{label} is invalid")
    return value.strip()


def _optional_identifier(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)
