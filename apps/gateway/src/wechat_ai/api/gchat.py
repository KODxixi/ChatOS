from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..gchat_preset import get_gchat_preset
from ..services.gchat_service import GchatNotFound, GchatService, GchatValidationError


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)


class ProjectPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")

    @model_validator(mode="after")
    def validate_patch(self) -> "ProjectPatchRequest":
        if not self.model_fields_set:
            raise ValueError("project patch is empty")
        return self


class ProjectConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversationId: str = Field(min_length=1, max_length=500)
    source: str = Field(default="user", pattern="^(user|agent|rule)$")
    confidence: float = Field(default=1.0, ge=0, le=1)


class ProjectAssignmentCandidateConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: str | None = Field(default=None, min_length=1, max_length=500)


class MemoryEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageId: int = Field(gt=0)
    partId: int | None = Field(default=None, gt=0)
    relation: str = Field(default="supports", pattern="^(supports|contradicts)$")


class MemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    memoryType: str = Field(pattern="^(fact|preference|goal|decision|constraint|risk)$")
    scope: dict[str, object]
    evidence: list[MemoryEvidenceRequest] = Field(default_factory=list, max_length=200)
    status: str = Field(default="suggested", pattern="^suggested$")
    confidence: float = Field(default=0.5, ge=0, le=1)


class MemoryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        pattern="^(confirmed|rejected)$",
    )
    content: str | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "MemoryPatchRequest":
        if not self.model_fields_set:
            raise ValueError("memory patch is empty")
        return self


class DecisionEvidenceRequest(MemoryEvidenceRequest):
    pass


class DecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    decision: str = Field(min_length=1, max_length=20_000)
    projectId: str | None = Field(default=None, max_length=500)
    rationale: str = Field(default="", max_length=20_000)
    nextAction: str = Field(default="", max_length=20_000)
    evidence: list[DecisionEvidenceRequest] = Field(default_factory=list, max_length=200)
    status: str = Field(default="proposed", pattern="^proposed$")
    confidence: float = Field(default=0.5, ge=0, le=1)


class DecisionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, pattern="^(accepted|rejected)$")
    decision: str | None = Field(default=None, min_length=1, max_length=20_000)
    nextAction: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "DecisionPatchRequest":
        if not self.model_fields_set:
            raise ValueError("decision patch is empty")
        return self


def create_gchat_router(service: GchatService) -> APIRouter:
    router = APIRouter()

    @router.get("/gchat/preset", response_model=None)
    def gchat_preset() -> dict[str, object]:
        return get_gchat_preset()

    @router.get("/projects", response_model=None)
    def projects(includeArchived: bool = False) -> dict[str, object] | JSONResponse:
        try:
            return service.list_projects(include_archived=includeArchived)
        except Exception as exc:
            return _error(exc)

    @router.post("/projects", status_code=201, response_model=None)
    def create_project(request: ProjectCreateRequest) -> dict[str, object] | JSONResponse:
        try:
            return service.create_project(request.name, request.description)
        except Exception as exc:
            return _error(exc)

    @router.patch("/projects/{project_id}", response_model=None)
    def patch_project(project_id: str, request: ProjectPatchRequest) -> dict[str, object] | JSONResponse:
        try:
            return service.update_project(
                project_id,
                name=request.name,
                description=request.description,
                status=request.status,
            )
        except Exception as exc:
            return _error(exc)

    @router.delete("/projects/{project_id}", status_code=204, response_model=None)
    def archive_project(project_id: str) -> Response | JSONResponse:
        try:
            service.update_project(project_id, status="archived")
        except Exception as exc:
            return _error(exc)
        return Response(status_code=204)

    @router.get("/projects/{project_id}/pulse", response_model=None)
    def project_pulse(project_id: str) -> dict[str, object] | JSONResponse:
        try:
            return service.get_project_pulse(project_id)
        except Exception as exc:
            return _error(exc)

    @router.post("/projects/{project_id}/conversations", status_code=204, response_model=None)
    def assign_project_conversation(
        project_id: str,
        request: ProjectConversationRequest,
    ) -> Response | JSONResponse:
        try:
            service.assign_conversation(
                project_id,
                request.conversationId,
                source=request.source,
                confidence=request.confidence,
            )
        except Exception as exc:
            return _error(exc)
        return Response(status_code=204)

    @router.delete("/projects/{project_id}/conversations/{conversation_id}", status_code=204, response_model=None)
    def unassign_project_conversation(project_id: str, conversation_id: str) -> Response | JSONResponse:
        try:
            service.unassign_conversation(project_id, conversation_id)
        except Exception as exc:
            return _error(exc)
        return Response(status_code=204)

    @router.get(
        "/gchat/project-assignment-candidates",
        response_model=None,
    )
    def project_assignment_candidates(
        status: str | None = Query(default="suggested"),
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.list_project_assignment_candidates(status=status)
        except Exception as exc:
            return _error(exc)

    @router.post(
        "/gchat/project-assignment-candidates/{candidate_id}/confirm",
        response_model=None,
    )
    def confirm_project_assignment_candidate(
        candidate_id: str,
        request: ProjectAssignmentCandidateConfirmRequest | None = None,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.confirm_project_assignment_candidate(
                candidate_id,
                project_id=request.projectId if request is not None else None,
            )
        except Exception as exc:
            return _error(exc)

    @router.post(
        "/gchat/project-assignment-candidates/{candidate_id}/reject",
        response_model=None,
    )
    def reject_project_assignment_candidate(
        candidate_id: str,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.reject_project_assignment_candidate(candidate_id)
        except Exception as exc:
            return _error(exc)

    @router.get("/gchat/memories", response_model=None)
    def memories(
        status: str | None = Query(default=None),
        scopeId: str | None = Query(default=None),
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.list_memories(status=status, scope_id=scopeId)
        except Exception as exc:
            return _error(exc)

    @router.post("/gchat/memories", status_code=201, response_model=None)
    def create_memory(request: MemoryCreateRequest) -> dict[str, object] | JSONResponse:
        try:
            return service.create_memory(
                content=request.content,
                memory_type=request.memoryType,
                scope=request.scope,
                evidence=[item.model_dump(by_alias=True) for item in request.evidence],
                status=request.status,
                confidence=request.confidence,
            )
        except Exception as exc:
            return _error(exc)

    @router.patch("/gchat/memories/{memory_id}", response_model=None)
    def patch_memory(memory_id: str, request: MemoryPatchRequest) -> dict[str, object] | JSONResponse:
        try:
            return service.update_memory(
                memory_id,
                status=request.status,
                content=request.content,
            )
        except Exception as exc:
            return _error(exc)

    @router.get("/gchat/memories/{memory_id}/evidence", response_model=None)
    def memory_evidence(memory_id: str) -> dict[str, object] | JSONResponse:
        try:
            return service.list_memory_evidence(memory_id)
        except Exception as exc:
            return _error(exc)

    @router.get("/gchat/decisions", response_model=None)
    def decisions(projectId: str | None = Query(default=None)) -> dict[str, object] | JSONResponse:
        try:
            return service.list_decisions(project_id=projectId)
        except Exception as exc:
            return _error(exc)

    @router.post("/gchat/decisions", status_code=201, response_model=None)
    def create_decision(request: DecisionCreateRequest) -> dict[str, object] | JSONResponse:
        try:
            return service.create_decision(
                title=request.title,
                decision=request.decision,
                project_id=request.projectId,
                rationale=request.rationale,
                next_action=request.nextAction,
                evidence=[item.model_dump(by_alias=True) for item in request.evidence],
                status=request.status,
                confidence=request.confidence,
            )
        except Exception as exc:
            return _error(exc)

    @router.patch("/gchat/decisions/{decision_id}", response_model=None)
    def patch_decision(
        decision_id: str,
        request: DecisionPatchRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.update_decision(
                decision_id,
                status=request.status,
                decision=request.decision,
                next_action=request.nextAction,
            )
        except Exception as exc:
            return _error(exc)

    @router.get("/gchat/decisions/{decision_id}/evidence", response_model=None)
    def decision_evidence(
        decision_id: str,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.list_decision_evidence(decision_id)
        except Exception as exc:
            return _error(exc)

    return router


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, GchatNotFound):
        return JSONResponse(status_code=404, content={"error": {"code": "GCHAT_NOT_FOUND", "message": str(exc)}})
    if isinstance(exc, (GchatValidationError, ValueError)):
        return JSONResponse(status_code=422, content={"error": {"code": "GCHAT_REQUEST_INVALID", "message": str(exc)}})
    return JSONResponse(status_code=500, content={"error": {"code": "GCHAT_ERROR", "message": "Gchat 操作失败。"}})
