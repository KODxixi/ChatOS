from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter
from fastapi.responses import JSONResponse


class OpenClawStatusApi(Protocol):
    def status(self) -> dict[str, object]: ...


def create_channels_router(service: OpenClawStatusApi) -> APIRouter:
    router = APIRouter()

    @router.get("/channels/openclaw/status", response_model=None)
    def openclaw_status() -> dict[str, object] | JSONResponse:
        try:
            return service.status()
        except Exception as _e:
            import logging; logging.getLogger(__name__).warning("channels probe failed: %s", _e)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "OPENCLAW_STATUS_UNAVAILABLE",
                        "message": "暂时无法检查 OpenClaw，请稍后重试。",
                        "retryable": True,
                        "details": {},
                    }
                },
            )

    return router
