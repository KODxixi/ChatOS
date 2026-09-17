from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services.setup_service import SetupError, SetupService, SetupValidationError


class WechatSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    databaseRoot: str = Field(min_length=1, max_length=32_767)


def create_setup_router(service: SetupService) -> APIRouter:
    router = APIRouter()

    @router.post("/setup/wechat-source", response_model=None)
    def configure_wechat_source(
        request: WechatSourceRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.configure_wechat_source(request.databaseRoot)
        except SetupValidationError as exc:
            return _error(422, exc.code, str(exc))
        except SetupError as exc:
            return _error(500, exc.code, "首次设置失败。")
        except Exception as _e:
            import logging; logging.getLogger(__name__).warning("setup probe failed: %s", _e)
            return _error(500, "SETUP_ERROR", "首次设置失败。")

    return router


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            }
        },
    )
