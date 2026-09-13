from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.export_service import (
    ExportErrorCode,
    ExportFormat,
    ExportResult,
    ExportServiceError,
)


ExportRunner = Callable[[str, ExportFormat], ExportResult]

_ERROR_STATUS: dict[ExportErrorCode, int] = {
    "CONVERSATION_NOT_FOUND": 404,
    "EXPORT_FAILED": 500,
}


class ExportRequest(BaseModel):
    conversationId: str
    format: ExportFormat


class ExportResponse(BaseModel):
    format: ExportFormat
    fileName: str
    messageCount: int


def create_export_router(*, export_runner: ExportRunner) -> APIRouter:
    router = APIRouter()

    @router.post("/export", response_model=ExportResponse)
    def export_data(request: ExportRequest) -> ExportResponse | JSONResponse:
        try:
            result = export_runner(request.conversationId, request.format)
        except ExportServiceError as exc:
            return JSONResponse(
                status_code=_ERROR_STATUS[exc.code],
                content={
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "details": exc.details,
                    }
                },
            )

        return ExportResponse(
            format=result.format,
            fileName=result.file_name,
            messageCount=result.message_count,
        )

    return router
