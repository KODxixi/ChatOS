from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse

from ..services.conversation_service import ConnectionFactory, InvalidCursor
from ..services.knowledge_service import (
    InvalidKnowledgeQuery,
    get_knowledge_facets,
    list_knowledge_items,
    list_knowledge_spaces,
)
from ..services.local_knowledge_index import LocalKnowledgeIndex
from ..wechat_media import add_media_readiness
from .conversations import api_error_response


def create_knowledge_router(
    connection_factory: ConnectionFactory,
    *,
    asset_root: Path | None = None,
    local_index: LocalKnowledgeIndex | None = None,
    retrieval_base_url: str = "http://127.0.0.1:9123/v1",
) -> APIRouter:
    router = APIRouter()
    library_root = asset_root.parent if asset_root is not None else None
    index = local_index or (
        LocalKnowledgeIndex.default(library_root, retrieval_base_url=retrieval_base_url)
        if library_root is not None
        else None
    )

    @router.get("/knowledge/facets")
    def facets() -> dict[str, Any]:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            return get_knowledge_facets(connection)

    @router.get("/knowledge/spaces")
    def spaces(
        sourceAppId: str | None = None,
        sourceAccountId: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            return list_knowledge_spaces(
                connection,
                source_app_id=sourceAppId,
                source_account_id=sourceAccountId,
                kind=kind,
            )

    @router.get("/knowledge/items", response_model=None)
    def items(
        conversationId: str | None = None,
        sourceAppId: str | None = None,
        sourceAccountId: str | None = None,
        kind: str | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                page = list_knowledge_items(
                    connection,
                    conversation_id=conversationId,
                    source_app_id=sourceAppId,
                    source_account_id=sourceAccountId,
                    kind=kind,
                    query=q,
                    limit=limit,
                    cursor=cursor,
                    asset_root=asset_root,
                )
                add_media_readiness(
                    connection,
                    page["items"],
                    asset_root=asset_root,
                )
                return page
        except InvalidCursor:
            return api_error_response(
                status_code=422,
                code="INVALID_CURSOR",
                message="Invalid pagination cursor.",
            )
        except InvalidKnowledgeQuery:
            return api_error_response(
                status_code=400,
                code="INVALID_KNOWLEDGE_QUERY",
                message="Invalid knowledge query.",
            )

    @router.post("/knowledge/archive")
    def archive_knowledge() -> dict[str, Any]:
        """Trigger file + link archival into the local library tree."""
        from ..services.knowledge_archiver import archive_library
        if library_root is None:
            return api_error_response(
                status_code=500, code="NO_ASSET_ROOT",
                message="asset_root not configured.",
            )
        stats = archive_library(
            connection_factory,
            library_root=library_root,
            asset_root=asset_root,
            retrieval_base_url=retrieval_base_url,
        )
        return {"ok": True, "stats": stats}

    @router.get("/knowledge/index/status", response_model=None)
    def index_status() -> dict[str, Any] | JSONResponse:
        if index is None:
            return api_error_response(
                status_code=503,
                code="KNOWLEDGE_INDEX_UNAVAILABLE",
                message="Knowledge index root is not configured.",
            )
        return index.status()

    @router.post("/knowledge/index/start", status_code=202, response_model=None)
    def start_index(background_tasks: BackgroundTasks) -> dict[str, Any] | JSONResponse:
        if index is None:
            return api_error_response(
                status_code=503,
                code="KNOWLEDGE_INDEX_UNAVAILABLE",
                message="Knowledge index root is not configured.",
            )
        if index.start():
            background_tasks.add_task(index.refresh, connection_factory, asset_root=asset_root)
        return index.status()

    return router
