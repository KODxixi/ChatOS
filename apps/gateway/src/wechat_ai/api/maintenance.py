from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..services.maintenance_service import (
    MaintenanceError,
    MaintenanceNotFound,
    MaintenanceService,
    MaintenanceValidationError,
    UpdateService,
)


class RestorePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backupId: str = Field(min_length=1, max_length=250)
    confirmed: bool


def create_maintenance_router(
    *,
    maintenance: MaintenanceService,
    updates: UpdateService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/backups", response_model=None)
    def list_backups() -> dict[str, object] | JSONResponse:
        try:
            return maintenance.list_backups()
        except Exception as exc:
            return _error(exc)

    @router.post("/backups", status_code=201, response_model=None)
    def create_backup() -> dict[str, object] | JSONResponse:
        try:
            return maintenance.create_backup()
        except Exception as exc:
            return _error(exc)

    @router.post("/restore-plan", response_model=None)
    def plan_restore(
        request: RestorePlanRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return maintenance.plan_restore(
                request.backupId,
                confirmed=request.confirmed,
            )
        except Exception as exc:
            return _error(exc)

    @router.post("/diagnostics", response_model=None)
    def diagnostics() -> Response | JSONResponse:
        try:
            payload = maintenance.diagnostic_zip()
        except Exception as exc:
            return _error(exc)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="chatbox-diagnostic.zip"'
            },
        )

    @router.get("/updates/check", response_model=None)
    def check_updates() -> dict[str, object] | JSONResponse:
        try:
            return updates.check()
        except Exception as exc:
            return _error(exc)

    return router


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, MaintenanceNotFound):
        return _response(404, exc.code, "未找到备份或本地知识库。")
    if isinstance(exc, MaintenanceValidationError):
        return _response(422, exc.code, str(exc))
    if isinstance(exc, MaintenanceError):
        return _response(503, exc.code, "维护操作暂时不可用。", retryable=True)
    return _response(500, "MAINTENANCE_ERROR", "维护操作失败。")


def _response(
    status_code: int,
    code: str,
    message: str,
    *,
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
