from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.import_service import ImportErrorCode, ImportServiceError
from ..wechat_db import ImportResult


ImportRunner = Callable[[], ImportResult]

_ERROR_STATUS: dict[ImportErrorCode, int] = {
    "SOURCE_NOT_FOUND": 404,
    "SOURCE_SCHEMA_UNSUPPORTED": 422,
}


class ImportResponse(BaseModel):
    discoveredSources: int
    normalizedConversations: int
    insertedMessages: int


def create_import_router(*, import_runner: ImportRunner) -> APIRouter:
    router = APIRouter()

    @router.post("/import", response_model=ImportResponse)
    def import_data() -> ImportResponse | JSONResponse:
        try:
            result = import_runner()
        except ImportServiceError as exc:
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

        return ImportResponse(
            discoveredSources=result.discovered_sources,
            normalizedConversations=result.normalized_conversations,
            insertedMessages=result.inserted_messages,
        )

    return router
