from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..credential_store import CredentialStoreUnavailable
from ..services.model_profile_service import (
    DefaultModelProfileDeleteForbidden,
    AuthKind,
    ModelCredentialMissing,
    ModelProfileError,
    ModelProfileDefaultNotReady,
    ModelProfileInUse,
    ModelProfileNotFound,
    ModelProfileService,
    ModelProfileTestFailed,
    ModelProfileValidationError,
    ProviderKind,
    RemotePrivacyAcknowledgementRequired,
)


class ModelProfileResponse(BaseModel):
    id: str
    name: str
    provider: ProviderKind
    baseUrl: str
    model: str | None
    authKind: AuthKind
    isDefault: bool
    isRemote: bool
    hasCredential: bool
    privacyAcknowledged: bool
    status: Literal["unknown", "ready", "offline"]
    lastCheckedAt: int | None
    createdAt: int
    updatedAt: int


class ModelProfileListResponse(BaseModel):
    items: list[ModelProfileResponse]


class ModelProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    provider: ProviderKind
    baseUrl: str = Field(min_length=1, max_length=2_000)
    model: str | None = Field(default=None, max_length=500)
    authKind: AuthKind | None = None
    isDefault: bool = False


class ModelProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider: ProviderKind | None = None
    baseUrl: str | None = Field(default=None, min_length=1, max_length=2_000)
    model: str | None = Field(default=None, max_length=500)
    authKind: AuthKind | None = None
    isDefault: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> ModelProfilePatchRequest:
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        for field_name in self.model_fields_set - {"model"}:
            if getattr(self, field_name) is None:
                raise ValueError("profile field must not be null")
        return self


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=32_768)


class PrivacyAcknowledgementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    policyVersion: str = Field(min_length=1, max_length=100)


class ModelProfileTestResponse(BaseModel):
    profile: ModelProfileResponse
    models: list[str]


class DeepSeekV4FlashPresetResponse(ModelProfileResponse):
    presetId: Literal["deepseek-v4-flash"]
    reasoningMode: Literal["auto"]


def create_model_profiles_router(
    service: ModelProfileService,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/model-profiles",
        response_model=ModelProfileListResponse,
    )
    def list_profiles() -> dict[str, object] | JSONResponse:
        try:
            return {"items": service.list_profiles()}
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.post(
        "/model-profiles",
        response_model=ModelProfileResponse,
        status_code=201,
    )
    def create_profile(
        request: ModelProfileCreateRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.create_profile(
                name=request.name,
                provider=request.provider,
                base_url=request.baseUrl,
                model=request.model,
                auth_kind=request.authKind,
                is_default=request.isDefault,
            )
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.post(
        "/model-profiles/presets/deepseek-v4-flash",
        response_model=DeepSeekV4FlashPresetResponse,
        status_code=201,
    )
    def create_deepseek_v4_flash_preset(
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.create_deepseek_v4_flash_preset()
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.patch(
        "/model-profiles/{profile_id}",
        response_model=ModelProfileResponse,
    )
    def patch_profile(
        profile_id: str,
        request: ModelProfilePatchRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.update_profile(
                profile_id,
                request.model_dump(exclude_unset=True),
            )
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.put(
        "/model-profiles/{profile_id}/credential",
        response_model=ModelProfileResponse,
    )
    def put_credential(
        profile_id: str,
        request: CredentialRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.set_credential(profile_id, request.secret)
        except (ModelProfileError, CredentialStoreUnavailable) as exc:
            return _profile_error_response(exc)

    @router.post(
        "/model-profiles/{profile_id}/acknowledge-remote",
        response_model=ModelProfileResponse,
    )
    def acknowledge_remote(
        profile_id: str,
        request: PrivacyAcknowledgementRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.acknowledge_remote(
                profile_id,
                accepted=request.accepted,
                policy_version=request.policyVersion,
            )
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.post(
        "/model-profiles/{profile_id}/test",
        response_model=ModelProfileTestResponse,
    )
    def test_profile(
        profile_id: str,
    ) -> dict[str, object] | JSONResponse:
        try:
            return service.test_profile(profile_id)
        except ModelProfileError as exc:
            return _profile_error_response(exc)

    @router.delete(
        "/model-profiles/{profile_id}",
        status_code=204,
        response_model=None,
        response_class=Response,
    )
    def delete_profile(profile_id: str) -> Response | JSONResponse:
        try:
            service.delete_profile(profile_id)
        except (ModelProfileError, CredentialStoreUnavailable) as exc:
            return _profile_error_response(exc)
        return Response(status_code=204)


    return router


def _profile_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, ModelProfileNotFound):
        return _error_response(
            status_code=404,
            code=exc.code,
            message="未找到模型配置。",
        )
    if isinstance(exc, DefaultModelProfileDeleteForbidden):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="默认模型配置不能删除。",
        )
    if isinstance(exc, ModelProfileInUse):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="模型配置仍被 Agent 记录使用，不能删除。",
        )
    if isinstance(exc, ModelProfileDefaultNotReady):
        return _error_response(
            status_code=409,
            code=exc.code,
            message="需先写入 Key、确认隐私提示并完成连接测试。",
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
    if isinstance(exc, CredentialStoreUnavailable):
        return _error_response(
            status_code=503,
            code="CREDENTIAL_STORE_UNAVAILABLE",
            message="Windows 凭据管理器不可用。",
            retryable=True,
        )
    if isinstance(exc, ModelProfileTestFailed):
        return _error_response(
            status_code=503,
            code=exc.code,
            message="模型当前不可用。",
            retryable=True,
        )
    if isinstance(exc, ModelProfileValidationError):
        return _error_response(
            status_code=422,
            code=exc.code,
            message="模型配置无效。",
        )
    return _error_response(
        status_code=500,
        code="MODEL_PROFILE_ERROR",
        message="模型配置操作失败。",
    )


def _error_response(
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
