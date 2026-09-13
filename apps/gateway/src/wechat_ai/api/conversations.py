from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services.activity_filter import evaluate_activity
from ..services.conversation_service import (
    ConnectionFactory,
    ConversationNotFound,
    InvalidCursor,
    MessageNotFound,
    get_conversation,
    list_conversations,
    list_messages,
    update_conversation,
)
from ..wechat_media import add_media_readiness


class ConversationResponse(BaseModel):
    id: str
    sourceConversationId: str
    sourceAppId: str
    sourceAccountId: str
    title: str
    kind: str
    lastMessageAt: int | None
    messageCount: int
    isFavorite: bool
    isArchived: bool
    isIndexingEnabled: bool
    note: str


class MediaDerivativeResponse(BaseModel):
    kind: Literal["ocr", "transcript"]
    status: Literal["ready", "skipped", "error"]
    errorCode: str | None
    indexed: bool


class MessageAssetResponse(BaseModel):
    id: int
    messagePartId: int
    kind: str
    status: str
    externalUrl: None
    hasLocalFile: bool
    fileName: str | None
    mimeType: str | None
    byteSize: int | None
    contentUrl: str | None
    readinessState: Literal[
        "discovered", "resolved", "previewable", "processed", "error"
    ]
    processingUrl: str | None
    playbackUrl: str | None
    errorCode: str | None
    derivative: MediaDerivativeResponse | None


class MessageResponse(BaseModel):
    id: int
    conversationId: str
    sourceMessageId: str | None
    senderId: str | None
    senderName: str
    sentAt: int
    type: str
    content: str
    assets: list[MessageAssetResponse] = Field(default_factory=list)


class ConversationPage(BaseModel):
    items: list[ConversationResponse]
    nextCursor: str | None


class MessagePage(BaseModel):
    items: list[MessageResponse]
    nextCursor: str | None


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isFavorite: bool | None = None
    isArchived: bool | None = None
    isIndexingEnabled: bool | None = None
    note: str | None = Field(default=None, max_length=20_000)

    @field_validator(
        "isFavorite",
        "isArchived",
        "isIndexingEnabled",
        "note",
        mode="before",
    )
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("null is not allowed")
        return value

    @model_validator(mode="after")
    def require_update(self) -> ConversationPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


def install_api_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        RequestValidationError,
        _validation_error_handler,
    )


def create_conversations_router(
    connection_factory: ConnectionFactory,
    *,
    asset_root: Path | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/conversations", response_model=ConversationPage)
    def conversations(
        sourceAppId: str | None = None,
        sourceAccountId: str | None = None,
        kind: str | None = None,
        includeArchived: bool = False,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return list_conversations(
                    connection,
                    limit=limit,
                    cursor=cursor,
                    source_app_id=sourceAppId,
                    source_account_id=sourceAccountId,
                    kind=kind,
                    include_archived=includeArchived,
                )
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="分页游标无效。",
            )

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
    )
    def conversation(
        conversation_id: str,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return get_conversation(connection, conversation_id)
        except ConversationNotFound:
            return _not_found_response()

    @router.patch(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
    )
    def patch_conversation(
        conversation_id: str,
        patch: ConversationPatch,
    ) -> dict[str, Any] | JSONResponse:
        updates = {
            key: value
            for key, value in patch.model_dump().items()
            if key in patch.model_fields_set
        }
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return update_conversation(
                    connection,
                    conversation_id,
                    updates,
                )
        except ConversationNotFound:
            return _not_found_response()

    @router.get(
        "/conversations/{conversation_id}/messages",
        response_model=MessagePage,
    )
    def messages(
        conversation_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        targetMessageId: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                page = list_messages(
                    connection,
                    conversation_id,
                    limit=limit,
                    cursor=cursor,
                    target_message_id=targetMessageId,
                    asset_root=asset_root,
                )
                add_media_readiness(
                    connection,
                    page["items"],
                    asset_root=asset_root,
                )
                return page
        except ConversationNotFound:
            return _not_found_response()
        except MessageNotFound:
            return api_error_response(
                status_code=404,
                code="MESSAGE_NOT_FOUND",
                message="未找到引用消息。",
            )
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="分页游标无效。",
            )

    @router.post("/conversations/evaluate-activity")
    def evaluate_activity_endpoint(
        inactiveDays: Annotated[int, Query(ge=1, le=365)] = 30,
    ) -> dict[str, Any]:
        """Evaluate all conversations by user activity and mark inactive ones.

        Conversations where the user has sent zero messages in the last
        *inactiveDays* days are marked ``isIndexingEnabled=false`` and their
        retrieval chunks are removed immediately.
        """
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            result = evaluate_activity(
                connection,
                inactive_days=inactiveDays,
            )
        return {
            "userWxid": result.user_wxid,
            "inactiveDays": result.inactive_days,
            "evaluated": result.evaluated,
            "markedInactive": result.marked_inactive,
            "alreadyInactive": result.already_inactive,
            "active": result.active,
            "markedIds": list(result.marked_ids),
        }

    return router


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


def _not_found_response() -> JSONResponse:
    return api_error_response(
        status_code=404,
        code="CONVERSATION_NOT_FOUND",
        message="未找到会话。",
    )


async def _validation_error_handler(
    _request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return api_error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="请求参数无效。",
    )
