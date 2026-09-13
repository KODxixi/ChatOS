from __future__ import annotations

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
CallKind = Literal["retrieval", "model", "tool", "embedding"]
CallStatus = Literal["completed", "failed", "cancelled"]
EvidenceRole = Literal["candidate", "citation"]

CALL_RETENTION_DAYS = 90
_DAY_MS = 24 * 60 * 60 * 1000
_PRUNE_MARKER_KEY = "agent_call_ledger_last_prune_at"
_ALLOWED_METADATA = {
    "retrieval": {
        "backend",
        "indexVersion",
        "scopeMode",
        "strategy",
        "timeRangeApplied",
    },
    "model": {"provider", "reasoningMode", "isRemote"},
    "tool": {"toolName"},
    "embedding": {"backend", "indexVersion", "dimensions"},
}


@dataclass(frozen=True, slots=True)
class CallEvidence:
    message_id: int
    part_id: int | None
    role: EvidenceRole
    rank: int
    score: float | None = None


class CallLedgerService:
    """Best-effort, metadata-only observability for Agent calls."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        retention_days: int = CALL_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            raise ValueError("call retention must be positive")
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._retention_days = retention_days
        self._degraded_detail: str | None = None

    def start_span(
        self,
        *,
        run_id: str,
        kind: CallKind,
        operation: str,
        parent_span_id: str | None = None,
        model_profile_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> str | None:
        try:
            normalized_kind = _kind(kind)
            normalized_metadata = _metadata(normalized_kind, metadata or {})
            span_id = _required_text(self._id_factory(), "span id")
            now = self._clock()
            with closing(self._connection_factory()) as connection:
                connection.execute(
                    """
                    INSERT INTO agent_call_spans(
                        id, run_id, parent_span_id, kind, operation,
                        model_profile_id, started_at, metadata_json, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        span_id,
                        _required_text(run_id, "run id"),
                        parent_span_id,
                        normalized_kind,
                        _required_text(operation, "call operation"),
                        model_profile_id,
                        now,
                        json.dumps(
                            normalized_metadata,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        now + self._retention_days * _DAY_MS,
                    ),
                )
                connection.commit()
            return span_id
        except (TypeError, ValueError, sqlite3.Error) as exc:
            self._degrade(exc)
            return None

    def finish_span(
        self,
        span_id: str | None,
        *,
        status: CallStatus = "completed",
        error_code: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        candidate_count: int | None = None,
        citation_count: int | None = None,
        evidence: Sequence[CallEvidence] = (),
    ) -> None:
        if span_id is None:
            return
        try:
            if status not in {"completed", "failed", "cancelled"}:
                raise ValueError("call status is invalid")
            counts = tuple(
                _optional_count(value)
                for value in (
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    candidate_count,
                    citation_count,
                )
            )
            if counts[2] is None and counts[0] is not None and counts[1] is not None:
                counts = (*counts[:2], counts[0] + counts[1], *counts[3:])
            now = self._clock()
            with closing(self._connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT started_at FROM agent_call_spans WHERE id = ? AND status = 'running'",
                    (_required_text(span_id, "span id"),),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return
                connection.execute(
                    """
                    UPDATE agent_call_spans
                    SET status = ?, finished_at = ?, duration_ms = ?,
                        input_tokens = ?, output_tokens = ?, total_tokens = ?,
                        candidate_count = ?, citation_count = ?, error_code = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        status,
                        now,
                        max(0, now - int(row["started_at"])),
                        *counts,
                        error_code,
                        span_id,
                    ),
                )
                for item in evidence:
                    _validate_evidence(item)
                    connection.execute(
                        """
                        INSERT INTO agent_call_evidence(
                            span_id, message_id, part_id, role, rank, score
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            span_id,
                            item.message_id,
                            item.part_id,
                            item.role,
                            item.rank,
                            item.score,
                        ),
                    )
                connection.commit()
        except (TypeError, ValueError, sqlite3.Error) as exc:
            self._degrade(exc)

    def list_run_spans(self, run_id: str) -> dict[str, object]:
        try:
            with closing(self._connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT * FROM agent_call_spans
                    WHERE run_id = ?
                    ORDER BY started_at ASC, rowid ASC
                    """,
                    (_required_text(run_id, "run id"),),
                ).fetchall()
                evidence_rows = connection.execute(
                    """
                    SELECT evidence.*
                    FROM agent_call_evidence AS evidence
                    JOIN agent_call_spans AS spans ON spans.id = evidence.span_id
                    WHERE spans.run_id = ?
                    ORDER BY evidence.span_id, evidence.role ASC, evidence.rank ASC
                    """,
                    (run_id,),
                ).fetchall()
        except (ValueError, sqlite3.Error) as exc:
            self._degrade(exc)
            return {"items": [], "retentionDays": self._retention_days}
        evidence_by_span: dict[str, list[dict[str, object]]] = {}
        for row in evidence_rows:
            evidence_by_span.setdefault(str(row["span_id"]), []).append(
                {
                    "messageId": int(row["message_id"]),
                    "partId": int(row["part_id"]) if row["part_id"] is not None else None,
                    "role": str(row["role"]),
                    "rank": int(row["rank"]),
                    "score": float(row["score"]) if row["score"] is not None else None,
                }
            )
        return {
            "items": [
                _span_response(row, evidence_by_span.get(str(row["id"]), []))
                for row in rows
            ],
            "retentionDays": self._retention_days,
        }

    def add_citations(
        self,
        span_id: str | None,
        evidence: Sequence[CallEvidence],
    ) -> None:
        if span_id is None:
            return
        try:
            with closing(self._connection_factory()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM agent_call_spans WHERE id = ?",
                    (_required_text(span_id, "span id"),),
                ).fetchone()
                if exists is None:
                    connection.rollback()
                    return
                for item in evidence:
                    _validate_evidence(item)
                    if item.role != "citation":
                        raise ValueError("call citation role is invalid")
                    connection.execute(
                        """
                        INSERT INTO agent_call_evidence(
                            span_id, message_id, part_id, role, rank, score
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(span_id, role, rank) DO UPDATE SET
                            message_id = excluded.message_id,
                            part_id = excluded.part_id,
                            score = excluded.score
                        """,
                        (
                            span_id,
                            item.message_id,
                            item.part_id,
                            item.role,
                            item.rank,
                            item.score,
                        ),
                    )
                connection.execute(
                    "UPDATE agent_call_spans SET citation_count = ? WHERE id = ?",
                    (len(evidence), span_id),
                )
                connection.commit()
        except (TypeError, ValueError, sqlite3.Error) as exc:
            self._degrade(exc)

    def prune_expired(self) -> int:
        try:
            with closing(self._connection_factory()) as connection:
                deleted = connection.execute(
                    """
                    DELETE FROM agent_call_spans
                    WHERE expires_at <= ? AND status <> 'running'
                    """,
                    (self._clock(),),
                ).rowcount
                connection.commit()
            return deleted
        except sqlite3.Error as exc:
            self._degrade(exc)
            return 0

    def prune_if_due(self) -> int:
        now = self._clock()
        try:
            with closing(self._connection_factory()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT value FROM app_meta WHERE key = ?",
                    (_PRUNE_MARKER_KEY,),
                ).fetchone()
                if row is not None and int(row[0]) > now - _DAY_MS:
                    connection.commit()
                    return 0
                deleted = connection.execute(
                    """
                    DELETE FROM agent_call_spans
                    WHERE expires_at <= ? AND status <> 'running'
                    """,
                    (now,),
                ).rowcount
                connection.execute(
                    """
                    INSERT INTO app_meta(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (_PRUNE_MARKER_KEY, str(now)),
                )
                connection.commit()
            return deleted
        except (TypeError, ValueError, sqlite3.Error) as exc:
            self._degrade(exc)
            return 0

    def health(self) -> dict[str, str]:
        if self._degraded_detail is None:
            return {"status": "ready", "detail": "调用审计可用"}
        return {"status": "degraded", "detail": self._degraded_detail}

    def _degrade(self, exc: BaseException) -> None:
        self._degraded_detail = f"调用审计降级：{type(exc).__name__}"


def _span_response(
    row: sqlite3.Row,
    evidence: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "runId": str(row["run_id"]),
        "parentSpanId": str(row["parent_span_id"]) if row["parent_span_id"] is not None else None,
        "kind": str(row["kind"]),
        "operation": str(row["operation"]),
        "status": str(row["status"]),
        "modelProfileId": str(row["model_profile_id"]) if row["model_profile_id"] is not None else None,
        "startedAt": int(row["started_at"]),
        "finishedAt": int(row["finished_at"]) if row["finished_at"] is not None else None,
        "durationMs": int(row["duration_ms"]) if row["duration_ms"] is not None else None,
        "inputTokens": int(row["input_tokens"]) if row["input_tokens"] is not None else None,
        "outputTokens": int(row["output_tokens"]) if row["output_tokens"] is not None else None,
        "totalTokens": int(row["total_tokens"]) if row["total_tokens"] is not None else None,
        "candidateCount": int(row["candidate_count"]) if row["candidate_count"] is not None else None,
        "citationCount": int(row["citation_count"]) if row["citation_count"] is not None else None,
        "errorCode": str(row["error_code"]) if row["error_code"] is not None else None,
        "metadata": json.loads(str(row["metadata_json"])),
        "evidence": evidence,
    }


def _metadata(kind: CallKind, metadata: Mapping[str, object]) -> dict[str, object]:
    unknown = set(metadata) - _ALLOWED_METADATA[kind]
    if unknown:
        raise ValueError("call metadata contains non-allowlisted fields")
    normalized: dict[str, object] = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            if not value or len(value) > 128:
                raise ValueError("call metadata text is invalid")
            normalized[key] = value
        elif isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            normalized[key] = value
        else:
            raise ValueError("call metadata value is invalid")
    return normalized


def _kind(value: str) -> CallKind:
    if value not in _ALLOWED_METADATA:
        raise ValueError("call kind is invalid")
    return value  # type: ignore[return-value]


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _optional_count(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("call count is invalid")
    return value


def _validate_evidence(item: CallEvidence) -> None:
    if item.message_id <= 0 or (item.part_id is not None and item.part_id <= 0):
        raise ValueError("call evidence identity is invalid")
    if item.role not in {"candidate", "citation"} or item.rank <= 0:
        raise ValueError("call evidence position is invalid")
