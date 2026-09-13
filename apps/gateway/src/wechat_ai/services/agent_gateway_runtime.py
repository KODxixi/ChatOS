from __future__ import annotations

import json
import logging
import base64
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Literal

_LOGGER = logging.getLogger(__name__)

ConnectionFactory = Callable[[], sqlite3.Connection]
Clock = Callable[[], int]
IdFactory = Callable[[], str]
RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


class AgentGatewayError(RuntimeError):
    code = "AGENT_GATEWAY_ERROR"


class AgentGatewayRunNotFound(AgentGatewayError):
    code = "AGENT_RUN_NOT_FOUND"


class AgentGatewayRunStateError(AgentGatewayError):
    code = "AGENT_RUN_STATE_INVALID"


class AgentRunInvalidCursor(AgentGatewayError):
    code = "AGENT_RUN_INVALID_CURSOR"


@dataclass(frozen=True, slots=True)
class QueuedAgentRun:
    id: str
    request: dict[str, object]
    source: Literal["http", "weixin"]
    source_ref: str | None


class AgentRunService:
    """Durable queue and append-only event log for every Agent execution."""

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

    def create_run(
        self,
        *,
        request: Mapping[str, object],
        source: Literal["http", "weixin"],
        source_ref: str | None = None,
        start_immediately: bool = False,
    ) -> dict[str, object]:
        if source not in {"http", "weixin"}:
            raise ValueError("agent run source is invalid")
        if source == "weixin" and not source_ref:
            raise ValueError("weixin agent run requires a source reference")
        request_json = _json_object(request, "agent request")
        run_id = self._id_factory()
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            try:
                status = "running" if start_immediately else "queued"
                connection.execute(
                    """
                    INSERT INTO agent_runs(
                        id, source, source_ref, request_json, status,
                        created_at, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        source,
                        source_ref,
                        request_json,
                        status,
                        now,
                        now if start_immediately else None,
                        now,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                if source_ref is None:
                    raise
                row = connection.execute(
                    "SELECT id FROM agent_runs WHERE source = ? AND source_ref = ?",
                    (source, source_ref),
                ).fetchone()
                if row is None:
                    raise
                run_id = str(row["id"])
        return self.get_run(run_id)

    def claim_next(self) -> QueuedAgentRun | None:
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE status = 'queued'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            updated = connection.execute(
                """
                UPDATE agent_runs
                SET status = 'running', started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        return QueuedAgentRun(
            id=str(row["id"]),
            request=json.loads(str(row["request_json"])),
            source=str(row["source"]),  # type: ignore[arg-type]
            source_ref=(
                str(row["source_ref"])
                if row["source_ref"] is not None
                else None
            ),
        )

    def append_event(
        self,
        run_id: str,
        event: Mapping[str, object],
    ) -> dict[str, object]:
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("agent event type is required")
        normalized_id = _required_text(run_id, "run id")
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT current_seq, status FROM agent_runs WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AgentGatewayRunNotFound("agent run was not found")
            if str(row["status"]) not in {"queued", "running", "waiting_approval"}:
                connection.rollback()
                raise AgentGatewayRunStateError("agent run is not active")
            seq = int(row["current_seq"]) + 1
            payload = dict(event)
            payload["runId"] = normalized_id
            payload["seq"] = seq
            connection.execute(
                """
                INSERT INTO agent_run_events(
                    run_id, seq, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    seq,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            connection.execute(
                "UPDATE agent_runs SET current_seq = ?, updated_at = ? WHERE id = ?",
                (seq, now, normalized_id),
            )
            connection.commit()
        return payload

    def complete(
        self,
        run_id: str,
        *,
        thread_id: str | None,
        answer: str,
    ) -> dict[str, object]:
        return self._finish(
            run_id,
            status="completed",
            thread_id=self._existing_thread_id(thread_id),
            answer=answer,
            error_code=None,
        )

    def fail(self, run_id: str, error_code: str) -> dict[str, object]:
        return self._finish(
            run_id,
            status="failed",
            thread_id=None,
            answer="",
            error_code=_required_text(error_code, "error code"),
        )

    def wait_for_approval(self, run_id: str, error_code: str) -> dict[str, object]:
        return self._finish(
            run_id,
            status="waiting_approval",
            thread_id=None,
            answer="",
            error_code=_required_text(error_code, "error code"),
            terminal=False,
        )

    def request_cancel(self, run_id: str) -> dict[str, object]:
        normalized_id = _required_text(run_id, "run id")
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM agent_runs WHERE id = ?", (normalized_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AgentGatewayRunNotFound("agent run was not found")
            status = str(row["status"])
            if status == "queued":
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'cancelled', cancel_requested_at = ?,
                        finished_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, now, normalized_id),
                )
                self._append_event_in_transaction(
                    connection,
                    normalized_id,
                    {"type": "cancelled"},
                    now,
                )
            elif status in {"running", "waiting_approval"}:
                connection.execute(
                    """
                    UPDATE agent_runs SET cancel_requested_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, normalized_id),
                )
            connection.commit()
        return self.get_run(normalized_id)

    def cancellation_requested(self, run_id: str) -> bool:
        with closing(self._connection_factory()) as connection:
            row = connection.execute(
                "SELECT cancel_requested_at FROM agent_runs WHERE id = ?",
                (_required_text(run_id, "run id"),),
            ).fetchone()
        if row is None:
            raise AgentGatewayRunNotFound("agent run was not found")
        return row[0] is not None

    def mark_cancelled(self, run_id: str) -> dict[str, object]:
        try:
            self.append_event(run_id, {"type": "cancelled"})
        except AgentGatewayRunStateError:
            pass
        return self._finish(
            run_id,
            status="cancelled",
            thread_id=None,
            answer="",
            error_code=None,
        )

    def recover_interrupted(self) -> int:
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            updated = connection.execute(
                """
                UPDATE agent_runs
                SET status = 'interrupted', error_code = 'APP_RESTARTED',
                    finished_at = ?, updated_at = ?
                WHERE status IN ('running', 'waiting_approval')
                """,
                (now, now),
            ).rowcount
            connection.commit()
        return updated

    def retry(self, run_id: str) -> dict[str, object]:
        normalized_id = _required_text(run_id, "run id")
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            updated = connection.execute(
                """
                UPDATE agent_runs
                SET status = 'queued', error_code = NULL, finished_at = NULL,
                    cancel_requested_at = NULL, updated_at = ?
                WHERE id = ? AND status IN ('failed', 'interrupted')
                """,
                (now, normalized_id),
            )
            if updated.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM agent_runs WHERE id = ?", (normalized_id,)
                ).fetchone()
                connection.rollback()
                if exists is None:
                    raise AgentGatewayRunNotFound("agent run was not found")
                raise AgentGatewayRunStateError("agent run cannot be retried")
            connection.commit()
        return self.get_run(normalized_id)

    def update_request(
        self,
        run_id: str,
        updates: Mapping[str, object],
    ) -> dict[str, object]:
        normalized_id = _required_text(run_id, "run id")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT request_json, status FROM agent_runs WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise AgentGatewayRunNotFound("agent run was not found")
            if str(row["status"]) != "queued":
                raise AgentGatewayRunStateError("only queued agent runs can change request")
            request = json.loads(str(row["request_json"]))
            if not isinstance(request, dict):
                raise AgentGatewayError("agent request is invalid")
            request.update(dict(updates))
            connection.execute(
                "UPDATE agent_runs SET request_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    self._clock(),
                    normalized_id,
                ),
            )
            connection.commit()
        return self.get_run(normalized_id)

    def list_events(
        self,
        run_id: str,
        *,
        after_seq: int,
    ) -> list[dict[str, object]]:
        if isinstance(after_seq, bool) or after_seq < 0:
            raise ValueError("event sequence is invalid")
        normalized_id = _required_text(run_id, "run id")
        with closing(self._connection_factory()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM agent_runs WHERE id = ?", (normalized_id,)
            ).fetchone()
            if exists is None:
                raise AgentGatewayRunNotFound("agent run was not found")
            rows = connection.execute(
                """
                SELECT payload_json FROM agent_run_events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq ASC
                """,
                (normalized_id, after_seq),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def get_run(self, run_id: str) -> dict[str, object]:
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (_required_text(run_id, "run id"),),
            ).fetchone()
        if row is None:
            raise AgentGatewayRunNotFound("agent run was not found")
        return _run_response(row)

    def list_runs(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: RunStatus | None = None,
        source: Literal["http", "weixin"] | None = None,
        model_profile_id: str | None = None,
        started_after: int | None = None,
        started_before: int | None = None,
    ) -> dict[str, object]:
        if isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("run page limit is invalid")
        if status is not None and status not in {
            "queued", "running", "waiting_approval", "completed",
            "failed", "cancelled", "interrupted",
        }:
            raise ValueError("run status is invalid")
        if source is not None and source not in {"http", "weixin"}:
            raise ValueError("run source is invalid")
        for value in (started_after, started_before):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError("run time filter is invalid")
        if (
            started_after is not None
            and started_before is not None
            and started_after >= started_before
        ):
            raise ValueError("run time range is invalid")

        where: list[str] = []
        parameters: list[object] = []
        if status is not None:
            where.append("runs.status = ?")
            parameters.append(status)
        if source is not None:
            where.append("runs.source = ?")
            parameters.append(source)
        if model_profile_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM agent_call_spans AS model_spans "
                "WHERE model_spans.run_id = runs.id "
                "AND model_spans.model_profile_id = ?)"
            )
            parameters.append(_required_text(model_profile_id, "model profile id"))
        if started_after is not None:
            where.append("runs.created_at >= ?")
            parameters.append(started_after)
        if started_before is not None:
            where.append("runs.created_at < ?")
            parameters.append(started_before)
        filter_sql = f"WHERE {' AND '.join(where)}" if where else ""

        page_where = list(where)
        page_parameters = list(parameters)
        if cursor is not None:
            cursor_created_at, cursor_id = _decode_run_cursor(cursor)
            page_where.append(
                "(runs.created_at < ? OR (runs.created_at = ? AND runs.id < ?))"
            )
            page_parameters.extend((cursor_created_at, cursor_created_at, cursor_id))
        page_filter_sql = (
            f"WHERE {' AND '.join(page_where)}" if page_where else ""
        )
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT runs.* FROM agent_runs AS runs
                {page_filter_sql}
                ORDER BY runs.created_at DESC, runs.id DESC LIMIT ?
                """,
                (*page_parameters, limit + 1),
            ).fetchall()
            summary_row = connection.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN runs.status IN (
                           'queued', 'running', 'waiting_approval'
                       ) THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN runs.status IN (
                           'failed', 'interrupted'
                       ) THEN 1 ELSE 0 END) AS failed,
                       AVG(CASE WHEN runs.started_at IS NOT NULL
                                 AND runs.finished_at IS NOT NULL
                           THEN runs.finished_at - runs.started_at END) AS average_duration
                FROM agent_runs AS runs
                {filter_sql}
                """,
                parameters,
            ).fetchone()
            token_row = connection.execute(
                f"""
                SELECT COALESCE(SUM(spans.input_tokens), 0),
                       COALESCE(SUM(spans.output_tokens), 0),
                       COALESCE(SUM(spans.total_tokens), 0)
                FROM agent_call_spans AS spans
                JOIN agent_runs AS runs ON runs.id = spans.run_id
                {filter_sql}
                """,
                parameters,
            ).fetchone()
            page_rows = rows[:limit]
            metrics = _run_metrics(connection, [str(row["id"]) for row in page_rows])
        has_more = len(rows) > limit
        next_cursor = (
            _encode_run_cursor(page_rows[-1])
            if has_more and page_rows
            else None
        )
        summary = {
            "total": int(summary_row["total"]),
            "active": int(summary_row["active"] or 0),
            "failed": int(summary_row["failed"] or 0),
            "averageDurationMs": (
                round(float(summary_row["average_duration"]))
                if summary_row["average_duration"] is not None
                else None
            ),
            "inputTokens": int(token_row[0]),
            "outputTokens": int(token_row[1]),
            "totalTokens": int(token_row[2]),
        }
        return {
            "items": [
                _run_response(row, metrics.get(str(row["id"])))
                for row in page_rows
            ],
            "total": summary["total"],
            "nextCursor": next_cursor,
            "summary": summary,
            "retentionDays": 90,
        }

    def _existing_thread_id(self, thread_id: str | None) -> str | None:
        if thread_id is None:
            return None
        normalized_id = _required_text(thread_id, "thread id")
        with closing(self._connection_factory()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM agent_threads WHERE id = ?", (normalized_id,)
            ).fetchone()
        return normalized_id if exists is not None else None

    def _finish(
        self,
        run_id: str,
        *,
        status: RunStatus,
        thread_id: str | None,
        answer: str,
        error_code: str | None,
        terminal: bool = True,
    ) -> dict[str, object]:
        normalized_id = _required_text(run_id, "run id")
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            updated = connection.execute(
                """
                UPDATE agent_runs
                SET status = ?, thread_id = COALESCE(?, thread_id),
                    answer_text = ?, error_code = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ? AND status IN ('running', 'waiting_approval')
                """,
                (
                    status,
                    thread_id,
                    answer,
                    error_code,
                    now if terminal else None,
                    now,
                    normalized_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise AgentGatewayRunStateError("agent run is not active")
            connection.commit()
        return self.get_run(normalized_id)

    @staticmethod
    def _append_event_in_transaction(
        connection: sqlite3.Connection,
        run_id: str,
        event: Mapping[str, object],
        now: int,
    ) -> dict[str, object]:
        row = connection.execute(
            "SELECT current_seq FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise AgentGatewayRunNotFound("agent run was not found")
        seq = int(row[0]) + 1
        payload = dict(event)
        payload.update({"runId": run_id, "seq": seq})
        connection.execute(
            """
            INSERT INTO agent_run_events(run_id, seq, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                str(payload["type"]),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        connection.execute(
            "UPDATE agent_runs SET current_seq = ? WHERE id = ?", (seq, run_id)
        )
        return payload


class AgentGatewayWorker:
    """Dedicated worker so model traffic never blocks import/graph jobs."""

    def __init__(
        self,
        runs: AgentRunService,
        executor: Callable[[str, Mapping[str, object]], Iterator[dict[str, object]]],
        *,
        on_started: Callable[[QueuedAgentRun], None] | None = None,
        on_completed: Callable[[QueuedAgentRun, dict[str, object]], None] | None = None,
        on_finished: Callable[[QueuedAgentRun, dict[str, object]], None] | None = None,
    ) -> None:
        self._runs = runs
        self._executor = executor
        self._on_started = on_started
        self._on_completed = on_completed
        self._on_finished = on_finished
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="gchat-agent-gateway",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def notify(self) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        run = None
        events: Iterator[dict[str, object]] | None = None
        answer_parts: list[str] = []
        final_event: dict[str, object] = {}
        try:
            run = self._runs.claim_next()
            if run is None:
                return False
            if self._on_started is not None:
                try:
                    self._on_started(run)
                except Exception:
                    # Channel presence must never make an Agent run fail.
                    pass
            events = iter(self._executor(run.id, run.request))
            for event in events:
                if self._runs.cancellation_requested(run.id):
                    self._runs.mark_cancelled(run.id)
                    return True
                stored = self._runs.append_event(run.id, event)
                final_event = stored
                if stored.get("type") == "delta":
                    answer_parts.append(str(stored.get("text", "")))
            thread_id = final_event.get("threadId")
            completed = self._runs.complete(
                run.id,
                thread_id=str(thread_id) if thread_id else None,
                answer="".join(answer_parts),
            )
            if self._on_completed is not None:
                self._on_completed(run, completed)
        except Exception as exc:
            # claim_next 可能因 SQLite 锁竞争抛异常，此时 run 尚未认领、无 run 可 fail；
            # 仅当 run 存在时才落失败状态，异常本身交由 _loop 外层守护退避。
            if run is not None:
                self._runs.fail(run.id, getattr(exc, "code", type(exc).__name__))
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()
            if run is not None and self._on_finished is not None:
                try:
                    self._on_finished(run, self._runs.get_run(run.id))
                except Exception:
                    pass
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._wake.wait(1.0)
                    self._wake.clear()
            except Exception:
                # claim_next / run_once 可能因 SQLite 锁竞争等异常穿透；daemon 线程
                # 绝不能静默死亡，否则 gateway 永久停摆。打日志后退避 1s 再
                # continue（同 job_worker._run 的外层兜底范式）。
                _LOGGER.exception("agent gateway loop iteration failed; backing off")
                self._wake.wait(1.0)
                self._wake.clear()


def _run_response(
    row: sqlite3.Row,
    metrics: dict[str, int] | None = None,
) -> dict[str, object]:
    request = json.loads(str(row["request_json"]))
    started_at = int(row["started_at"]) if row["started_at"] is not None else None
    finished_at = int(row["finished_at"]) if row["finished_at"] is not None else None
    resolved_metrics = metrics or {
        "retrievalCandidateCount": 0,
        "citationCount": 0,
        "modelCallCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
    }
    return {
        "id": str(row["id"]),
        "runId": str(row["id"]),
        "threadId": str(row["thread_id"]) if row["thread_id"] is not None else None,
        "source": str(row["source"]),
        "sourceRef": str(row["source_ref"]) if row["source_ref"] is not None else None,
        "request": request,
        "status": str(row["status"]),
        "currentSeq": int(row["current_seq"]),
        "answer": str(row["answer_text"]),
        "errorCode": str(row["error_code"]) if row["error_code"] is not None else None,
        "createdAt": int(row["created_at"]),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "updatedAt": int(row["updated_at"]),
        "metrics": {
            "totalDurationMs": (
                max(0, finished_at - started_at)
                if started_at is not None and finished_at is not None
                else None
            ),
            **resolved_metrics,
        },
    }


def _run_metrics(
    connection: sqlite3.Connection,
    run_ids: list[str],
) -> dict[str, dict[str, int]]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _item in run_ids)
    rows = connection.execute(
        f"""
        SELECT run_id,
               COALESCE(SUM(CASE WHEN kind = 'retrieval'
                   THEN candidate_count ELSE 0 END), 0) AS candidates,
               COALESCE(SUM(CASE WHEN kind = 'retrieval'
                   THEN citation_count ELSE 0 END), 0) AS citations,
               SUM(CASE WHEN kind = 'model' THEN 1 ELSE 0 END) AS model_calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM agent_call_spans
        WHERE run_id IN ({placeholders})
        GROUP BY run_id
        """,
        run_ids,
    ).fetchall()
    return {
        str(row["run_id"]): {
            "retrievalCandidateCount": int(row["candidates"]),
            "citationCount": int(row["citations"]),
            "modelCallCount": int(row["model_calls"]),
            "inputTokens": int(row["input_tokens"]),
            "outputTokens": int(row["output_tokens"]),
            "totalTokens": int(row["total_tokens"]),
        }
        for row in rows
    }


def _encode_run_cursor(row: sqlite3.Row) -> str:
    raw = json.dumps(
        [int(row["created_at"]), str(row["id"])],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_run_cursor(value: str) -> tuple[int, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentRunInvalidCursor("run cursor is invalid") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or isinstance(decoded[0], bool)
        or not isinstance(decoded[0], int)
        or decoded[0] < 0
        or not isinstance(decoded[1], str)
        or not decoded[1]
    ):
        raise AgentRunInvalidCursor("run cursor is invalid")
    return decoded[0], decoded[1]


def _json_object(value: Mapping[str, object], label: str) -> str:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not JSON serializable") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be an object")
    return encoded


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()
