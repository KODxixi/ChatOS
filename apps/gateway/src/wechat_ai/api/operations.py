from __future__ import annotations

import logging
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..credential_store import CredentialStoreUnavailable
from ..services.operations_service import (
    BackgroundJobService,
    OperationsConflict,
    OperationsNotFound,
    OperationsValidationError,
    SyncSettingsService,
    WechatSourceKeyService,
    parse_wechat_key_file,
)
from ..services.wechat_key_preparer_service import WechatKeyPreparerService


_LOGGER = logging.getLogger(__name__)

_MAX_KEY_FILE_BYTES = 1_000_000


class SyncSettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    intervalMinutes: int | None = Field(default=None, ge=5, le=1440)

    @model_validator(mode="after")
    def validate_patch(self) -> "SyncSettingsPatchRequest":
        if not self.model_fields_set:
            raise ValueError("sync settings patch is empty")
        return self


def create_operations_router(
    *,
    jobs: BackgroundJobService,
    keys: WechatSourceKeyService,
    sync_settings: SyncSettingsService,
    job_created: Callable[[], None] | None = None,
    sync_settings_changed: Callable[[], None] | None = None,
    preparer: WechatKeyPreparerService | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/import/jobs", status_code=202, response_model=None)
    def create_import_job() -> dict[str, Any] | JSONResponse:
        try:
            result = jobs.create_job("import", trigger_source="manual")
            if job_created is not None:
                job_created()
            return result
        except Exception as exc:
            return _error(exc)

    @router.get("/jobs", response_model=None)
    def list_jobs() -> dict[str, Any] | JSONResponse:
        try:
            return jobs.list_jobs()
        except Exception as exc:
            return _error(exc)

    @router.get("/jobs/{job_id}", response_model=None)
    def get_job(job_id: str) -> dict[str, Any] | JSONResponse:
        try:
            return jobs.get_job(job_id)
        except Exception as exc:
            return _error(exc)

    @router.post("/jobs/{job_id}/cancel", response_model=None)
    def cancel_job(job_id: str) -> dict[str, Any] | JSONResponse:
        try:
            return jobs.request_cancel(job_id)
        except Exception as exc:
            return _error(exc)

    @router.get("/settings/sync", response_model=None)
    def get_sync_settings() -> dict[str, Any] | JSONResponse:
        try:
            return sync_settings.get()
        except Exception as exc:
            return _error(exc)

    @router.patch("/settings/sync", response_model=None)
    def patch_sync_settings(
        request: SyncSettingsPatchRequest,
    ) -> dict[str, Any] | JSONResponse:
        try:
            result = sync_settings.patch(
                enabled=request.enabled,
                interval_minutes=request.intervalMinutes,
            )
            if sync_settings_changed is not None:
                sync_settings_changed()
            return result
        except Exception as exc:
            return _error(exc)

    @router.get("/setup/status", response_model=None)
    def setup_status() -> dict[str, Any] | JSONResponse:
        try:
            return keys.status()
        except Exception as exc:
            return _error(exc)

    @router.post("/setup/wechat-keys/import", response_model=None)
    async def import_wechat_keys(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            fields, files = await _multipart(request)
            if "file" in files:
                if fields or len(files) != 1:
                    raise OperationsValidationError(
                        "provide a key file or one key entry"
                    )
                entries = parse_wechat_key_file(files["file"])
            else:
                if files:
                    raise OperationsValidationError("unexpected key import file")
                entries = [
                    {
                        "databaseId": fields.get("databaseId"),
                        "key": fields.get("key", fields.get("encKey")),
                        "salt": fields.get("salt"),
                    }
                ]
            return keys.import_entries(entries)
        except Exception as exc:
            return _error(exc)

    @router.post("/setup/wechat-keys/auto", response_model=None)
    def auto_match_wechat_keys() -> dict[str, Any] | JSONResponse:
        try:
            return keys.auto_match()
        except Exception as exc:
            return _error(exc)

    @router.post("/setup/wechat-keys/preparer", response_model=None)
    def start_wechat_key_preparer() -> dict[str, object] | JSONResponse:
        try:
            return _require_preparer(preparer).start()
        except Exception as exc:
            return _error(exc)

    @router.get("/setup/wechat-keys/preparer", response_model=None)
    def get_wechat_key_preparer_status() -> dict[str, object] | JSONResponse:
        try:
            return _require_preparer(preparer).status()
        except Exception as exc:
            return _error(exc)

    @router.post("/setup/wechat-keys/preparer/import", response_model=None)
    def import_wechat_key_preparer_result() -> dict[str, Any] | JSONResponse:
        try:
            return _require_preparer(preparer).import_result()
        except Exception as exc:
            return _error(exc)

    @router.delete(
        "/setup/wechat-keys/{key_ref}",
        status_code=204,
        response_model=None,
    )
    def delete_wechat_key(key_ref: str) -> Response | JSONResponse:
        try:
            keys.delete(key_ref)
        except Exception as exc:
            return _error(exc)
        return Response(status_code=204)

    return router


def _require_preparer(
    preparer: WechatKeyPreparerService | None,
) -> WechatKeyPreparerService:
    if preparer is None:
        raise OperationsConflict("wechat key preparer is unavailable")
    return preparer


async def _multipart(request: Request) -> tuple[dict[str, str], dict[str, bytes]]:
    content_type = request.headers.get("content-type", "")
    if not content_type.casefold().startswith("multipart/form-data"):
        raise OperationsValidationError("multipart/form-data is required")
    body = await request.body()
    if not body or len(body) > _MAX_KEY_FILE_BYTES:
        raise OperationsValidationError("wechat key import size is invalid")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("ascii", errors="strict")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise OperationsValidationError("multipart/form-data is invalid")
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not name or name in fields or name in files:
            raise OperationsValidationError("multipart field is invalid")
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise OperationsValidationError("multipart field is invalid")
        if part.get_filename() is not None:
            files[name] = payload
            continue
        try:
            fields[name] = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OperationsValidationError("multipart field is invalid") from exc
    return fields, files


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, OperationsNotFound):
        return _error_response(404, exc.code, "未找到对应的后台任务或密钥引用。")
    if isinstance(exc, OperationsConflict):
        return _error_response(409, exc.code, "当前状态不允许执行该操作。")
    if isinstance(exc, OperationsValidationError):
        return _error_response(422, exc.code, str(exc))
    if isinstance(exc, CredentialStoreUnavailable):
        return _error_response(
            503,
            "CREDENTIAL_STORE_UNAVAILABLE",
            "Windows 凭据管理器不可用。",
            retryable=True,
        )
    _LOGGER.error(
        "operations endpoint error",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _error_response(
        500,
        "OPERATIONS_ERROR",
        "后台操作失败。",
        details={"errorType": type(exc).__name__, "message": str(exc)},
    )


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, object] | None = None,
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
