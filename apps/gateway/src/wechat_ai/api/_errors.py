"""Shared API error response helpers (extracted to break circular imports)."""
from __future__ import annotations

from fastapi.responses import JSONResponse


def api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
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


def not_found_response() -> JSONResponse:
    return api_error_response(
        status_code=404,
        code="CONVERSATION_NOT_FOUND",
        message="未找到会话。",
    )
