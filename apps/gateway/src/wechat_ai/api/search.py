from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.conversation_service import ConnectionFactory, InvalidCursor
from ..services.search_service import (
    EmbedFn,
    InvalidSearchQuery,
    search_messages,
)
from .conversations import MessageResponse, api_error_response


class SearchItem(MessageResponse):
    conversationTitle: str


class SearchPage(BaseModel):
    items: list[SearchItem]
    nextCursor: str | None
    mode: str | None = None
    note: str | None = None


def create_search_router(
    connection_factory: ConnectionFactory,
    *,
    embed_fn: EmbedFn | None = None,
    reranker: Any | None = None,
    rerank_enabled: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/search",
        response_model=SearchPage,
        response_model_exclude_defaults=True,
    )
    def search(
        q: Annotated[str, Query(min_length=1, max_length=500)],
        conversationId: str | None = None,
        senderName: str | None = None,
        sentAtFrom: int | None = None,
        sentAtTo: int | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, Any] | JSONResponse:
        if (
            sentAtFrom is not None
            and sentAtTo is not None
            and sentAtFrom > sentAtTo
        ):
            return api_error_response(
                status_code=422,
                code="VALIDATION_ERROR",
                message="请求参数无效。",
            )

        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return search_messages(
                    connection,
                    query=q,
                    conversation_id=conversationId,
                    sender_name=senderName,
                    sent_at_from=sentAtFrom,
                    sent_at_to=sentAtTo,
                    limit=limit,
                    cursor=cursor,
                    embed_fn=embed_fn,
                    reranker=reranker,
                    rerank_enabled=rerank_enabled,
                )
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="分页游标无效。",
            )
        except InvalidSearchQuery:
            return api_error_response(
                status_code=400,
                code="INVALID_SEARCH_QUERY",
                message="搜索条件无效。",
            )

    return router
