from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..credential_store import CredentialStoreUnavailable
from ..services.agent_service import (
    AgentService,
    AgentServiceValidationError,
    RemoteSendConfirmationRequired,
)
from ..services.agent_gateway_runtime import (
    AgentGatewayError,
    AgentRunInvalidCursor,
    AgentRunService,
)
from ..services.call_ledger_service import CallLedgerService
from ..services.agent_route_service import AgentRouteError
from ..services.agent_thread_service import (
    AgentThreadArchived,
    AgentThreadBusy,
    AgentThreadError,
    AgentThreadInvalidCursor,
    AgentThreadNotFound,
    AgentThreadService,
    AgentThreadValidationError,
    KnowledgeScopeValidationError,
)
from ..services.model_profile_service import (
    ModelCredentialMissing,
    ModelProfileNotFound,
    RemotePrivacyAcknowledgementRequired,
)


class RemoteSendConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=1, max_length=128)


class AgentStreamOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoningMode: Literal["auto", "fast", "deep"] = "auto"
    includeThreadHistory: bool = True
    generateInsights: bool = False


class AgentStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threadId: str | None = Field(default=None, min_length=1, max_length=500)
    question: str = Field(min_length=1, max_length=20_000)
    modelProfileId: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    scope: dict[str, object]
    projectId: str | None = Field(default=None, min_length=1, max_length=500)
    remoteSendConfirmation: RemoteSendConfirmationRequest | None = None
    options: AgentStreamOptions = Field(default_factory=AgentStreamOptions)


class AgentThreadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    scope: dict[str, object] | None = None
    projectId: str | None = Field(default=None, min_length=1, max_length=500)


class AgentThreadPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    isArchived: bool | None = None
    projectId: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_patch(self) -> AgentThreadPatchRequest:
        if not self.model_fields_set:
            raise ValueError("at least one thread field is required")
        if any(
            field_name != "projectId" and getattr(self, field_name) is None
            for field_name in self.model_fields_set
        ):
            raise ValueError("thread field must not be null")
        return self


class AgentRouteCandidateConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: str | None = Field(default=None, min_length=1, max_length=500)


def create_agent_router(
    service: AgentService,
    threads: AgentThreadService,
    *,
    runs: AgentRunService | None = None,
    call_ledger: CallLedgerService | None = None,
    run_created: Callable[[], None] | None = None,
    routes: object | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/agent/stream", response_model=None)
    def stream_agent(
        request: AgentStreamRequest,
    ) -> StreamingResponse | JSONResponse:
        run: dict[str, object] | None = None
        try:
            if runs is not None:
                run = runs.create_run(
                    request=request.model_dump(),
                    source="http",
                    start_immediately=True,
                )
            events = service.stream(
                **_service_request(
                    request,
                    execution_run_id=(str(run["runId"]) if run is not None else None),
                )
            )
        except Exception as exc:
            if runs is not None and run is not None:
                try:
                    runs.fail(
                        str(run["runId"]),
                        getattr(exc, "code", type(exc).__name__),
                    )
                except AgentGatewayError:
                    pass
            return _agent_error_response(exc)
        if runs is not None and run is not None:
            events = _record_stream(runs, str(run["runId"]), events)
        return StreamingResponse(
            _encode_ndjson(events),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/agent/runs", status_code=202, response_model=None)
    def create_run(
        request: AgentStreamRequest,
    ) -> dict[str, object] | JSONResponse:
        if runs is None:
            return _error_response(
                status_code=503,
                code="AGENT_GATEWAY_UNAVAILABLE",
                message="Agent Gateway 尚未启动。",
                retryable=True,
            )
        try:
            result = runs.create_run(
                request=request.model_dump(),
                source="http",
            )
            if run_created is not None:
                run_created()
            return result
        except Exception as exc:
            return _agent_error_response(exc)

    @router.get("/agent/runs", response_model=None)
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        status: Literal[
            "queued", "running", "waiting_approval", "completed",
            "failed", "cancelled", "interrupted",
        ] | None = None,
        source: Literal["http", "weixin"] | None = None,
        modelProfileId: str | None = Query(default=None, min_length=1, max_length=500),
        startedAfter: Annotated[int | None, Query(ge=0)] = None,
        startedBefore: Annotated[int | None, Query(ge=0)] = None,
    ) -> dict[str, object] | JSONResponse:
        if runs is None:
            return {
                "items": [],
                "total": 0,
                "nextCursor": None,
                "summary": {
                    "total": 0,
                    "active": 0,
                    "failed": 0,
                    "averageDurationMs": None,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                },
                "retentionDays": 90,
            }
        try:
            return runs.list_runs(
                limit=limit,
                cursor=cursor,
                status=status,
                source=source,
                model_profile_id=modelProfileId,
                started_after=startedAfter,
                started_before=startedBefore,
            )
        except Exception as exc:
            return _agent_error_response(exc)

    @router.get("/agent/runs/{run_id}/calls", response_model=None)
    def list_run_calls(run_id: str) -> dict[str, object] | JSONResponse:
        if runs is None or call_ledger is None:
            return _error_response(
                status_code=503,
                code="AGENT_OBSERVABILITY_UNAVAILABLE",
                message="Agent 调用记录暂时不可用。",
                retryable=True,
            )
        try:
            runs.get_run(run_id)
            return call_ledger.list_run_spans(run_id)
        except Exception as exc:
            return _agent_error_response(exc)

    @router.get("/agent/runs/{run_id}/events", response_model=None)
    def replay_run_events(
        run_id: str,
        afterSeq: Annotated[int, Query(ge=0)] = 0,
    ) -> StreamingResponse | JSONResponse:
        if runs is None:
            return _error_response(
                status_code=503,
                code="AGENT_GATEWAY_UNAVAILABLE",
                message="Agent Gateway 尚未启动。",
                retryable=True,
            )
        try:
            events = runs.list_events(run_id, after_seq=afterSeq)
        except Exception as exc:
            return _agent_error_response(exc)
        return StreamingResponse(
            _encode_ndjson(iter(events)),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/agent/route-candidates", response_model=None)
    def list_route_candidates() -> dict[str, object] | JSONResponse:
        if routes is None:
            return {"items": []}
        try:
            return routes.list_candidates()
        except Exception as exc:
            return _agent_error_response(exc)

    @router.post("/agent/route-candidates/{candidate_id}/confirm", response_model=None)
    def confirm_route_candidate(
        candidate_id: str,
        request: AgentRouteCandidateConfirmRequest | None = None,
    ) -> dict[str, object] | JSONResponse:
        if routes is None:
            return _error_response(
                status_code=503,
                code="AGENT_ROUTE_UNAVAILABLE",
                message="Agent 项目路由暂时不可用。",
                retryable=True,
            )
        try:
            return routes.confirm(
                candidate_id,
                request.projectId if request is not None else None,
            )
        except Exception as exc:
            return _agent_error_response(exc)

    @router.post("/agent/route-candidates/{candidate_id}/reject", response_model=None)
    def reject_route_candidate(candidate_id: str) -> dict[str, object] | JSONResponse:
        if routes is None:
            return _error_response(
                status_code=503,
                code="AGENT_ROUTE_UNAVAILABLE",
                message="Agent 项目路由暂时不可用。",
                retryable=True,
            )
        try:
            return routes.reject(candidate_id)
        except Exception as exc:
            return _agent_error_response(exc)

    @router.get("/agent/threads", response_model=None)
    def list_threads(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        projectId: str | None = Query(default=None, min_length=1, max_length=500),
    ) -> dict[str, object] | JSONResponse:
        try:
            return threads.list_threads(limit=limit, cursor=cursor, project_id=projectId)
        except AgentThreadError as exc:
            return _agent_error_response(exc)

    @router.post("/agent/threads", status_code=201, response_model=None)
    def create_thread(
        request: AgentThreadCreateRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            scope = request.scope
            if scope is None and request.projectId is not None:
                scope = {
                    "mode": "project",
                    "projectId": request.projectId,
                }
            return threads.create_thread(
                title=request.title,
                scope=scope,
                project_id=request.projectId,
            )
        except AgentThreadError as exc:
            return _agent_error_response(exc)

    @router.get("/agent/threads/{thread_id}", response_model=None)
    def get_thread(thread_id: str) -> dict[str, object] | JSONResponse:
        try:
            return threads.get_thread(thread_id)
        except AgentThreadError as exc:
            return _agent_error_response(exc)

    @router.patch("/agent/threads/{thread_id}", response_model=None)
    def patch_thread(
        thread_id: str,
        request: AgentThreadPatchRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return threads.update_thread(
                thread_id,
                request.model_dump(exclude_unset=True),
            )
        except AgentThreadError as exc:
            return _agent_error_response(exc)

    @router.delete(
        "/agent/threads/{thread_id}",
        status_code=204,
        response_model=None,
        response_class=Response,
    )
    def delete_thread(thread_id: str) -> Response | JSONResponse:
        try:
            threads.delete_thread(thread_id)
        except AgentThreadError as exc:
            return _agent_error_response(exc)
        return Response(status_code=204)

    return router


def _encode_ndjson(
    events: Iterator[dict[str, object]],
) -> Iterator[bytes]:
    try:
        for event in events:
            yield (
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


def _record_stream(
    runs: AgentRunService,
    run_id: str,
    events: Iterator[dict[str, object]],
) -> Iterator[dict[str, object]]:
    answer_parts: list[str] = []
    thread_id: str | None = None
    terminal = False
    try:
        for event in events:
            stored = runs.append_event(run_id, event)
            if stored.get("type") == "delta":
                answer_parts.append(str(stored.get("text", "")))
            if stored.get("threadId"):
                thread_id = str(stored["threadId"])
            yield stored
        runs.complete(
            run_id,
            thread_id=thread_id,
            answer="".join(answer_parts),
        )
        terminal = True
    except GeneratorExit:
        if not terminal:
            runs.mark_cancelled(run_id)
        raise
    except Exception as exc:
        runs.fail(run_id, getattr(exc, "code", type(exc).__name__))
        raise
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


def _service_request(
    request: AgentStreamRequest,
    *,
    execution_run_id: str | None = None,
) -> dict[str, object]:
    return {
        "thread_id": request.threadId,
        "question": request.question,
        "model_profile_id": request.modelProfileId,
        "scope": request.scope,
        "project_id": request.projectId,
        "remote_send_confirmation": (
            request.remoteSendConfirmation.fingerprint
            if request.remoteSendConfirmation is not None
            else None
        ),
        "reasoning_mode": request.options.reasoningMode,
        "include_thread_history": request.options.includeThreadHistory,
        "generate_insights": request.options.generateInsights,
        **(
            {"execution_run_id": execution_run_id}
            if execution_run_id is not None
            else {}
        ),
    }


def _agent_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, RemoteSendConfirmationRequired):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="发送到远程模型前需要确认本次内容。",
            details={"preview": exc.preview.to_dict()},
        )
    if isinstance(exc, AgentThreadNotFound):
        return _error_response(
            status_code=404,
            code=exc.code,
            message="未找到 Agent 会话。",
        )
    if isinstance(exc, ModelProfileNotFound):
        return _error_response(
            status_code=404,
            code=exc.code,
            message="未找到模型配置。",
        )
    if isinstance(
        exc,
        (
            KnowledgeScopeValidationError,
            AgentServiceValidationError,
            AgentThreadValidationError,
            AgentThreadInvalidCursor,
            AgentRunInvalidCursor,
        ),
    ):
        return _error_response(
            status_code=422,
            code=exc.code,
            message="Agent 请求参数无效。",
        )
    if isinstance(exc, RemotePrivacyAcknowledgementRequired):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="需要先确认远程模型隐私提示。",
        )
    if isinstance(exc, ModelCredentialMissing):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="模型凭据不可用。",
        )
    if isinstance(exc, (AgentThreadBusy, AgentThreadArchived)):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="Agent 会话当前不可操作。",
        )
    if isinstance(exc, CredentialStoreUnavailable):
        return _error_response(
            status_code=503,
            code="CREDENTIAL_STORE_UNAVAILABLE",
            message="Windows 凭据管理器不可用。",
            retryable=True,
        )
    if isinstance(exc, AgentGatewayError):
        return _error_response(
            status_code=(404 if exc.code == "AGENT_RUN_NOT_FOUND" else 409),
            code=exc.code,
            message="Agent 运行状态不可用。",
        )
    if isinstance(exc, AgentRouteError):
        return _error_response(
            status_code=404 if exc.__class__.__name__.endswith("NotFound") else 409,
            code="AGENT_ROUTE_ERROR",
            message="项目归类候选当前不可操作。",
        )
    return _error_response(
        status_code=500,
        code="AGENT_ERROR",
        message="Agent 操作失败。",
    )


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        },
    )
