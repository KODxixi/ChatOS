"""Vector index API."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel
from typing import Optional

from ._errors import api_error_response
from ..services import embedding_service as es
from ..services.conversation_service import ConnectionFactory



class IndexBody(BaseModel):
    conversation_id: Optional[str] = None
    full: bool = False


def create_vector_router(connection_factory: ConnectionFactory, db_path: str) -> APIRouter:
    router = APIRouter()

    def _do_index(conversation_id: Optional[str], full: bool) -> dict[str, Any]:
        if not es.is_available():
            return api_error_response(status_code=503, code="EMBEDDING_UNAVAILABLE",
                                      message="no embedding credential configured")
        if conversation_id:
            es.index_conversation(db_path, conversation_id)
            return {"status": "started", "conversationId": conversation_id}
        if full:
            es.index_all_conversations(db_path)
            return {"status": "started", "scope": "all"}
        es.index_top_conversations(db_path, limit=20)
        return {"status": "started", "scope": "top20"}

    @router.post("/vector/index")
    def trigger_index(body: IndexBody = Body(default=None)) -> dict[str, Any]:
        if body is None:
            body = IndexBody()
        return _do_index(body.conversation_id, body.full)

    @router.get("/vector/index/start")
    def trigger_index_get(
        conversation_id: Optional[str] = Query(default=None),
        full: bool = Query(default=False),
    ) -> dict[str, Any]:
        return _do_index(conversation_id, full)

    @router.get("/vector/status")
    def index_status() -> dict[str, Any]:
        with closing(connection_factory()) as db:
            return es.get_index_status(db)

    @router.delete("/vector/index")
    def clear_index(conversation_id: str | None = None) -> dict[str, Any]:
        with closing(connection_factory()) as db:
            if conversation_id:
                db.execute("DELETE FROM message_embeddings WHERE conversation_id=?", (conversation_id,))
            else:
                db.execute("DELETE FROM message_embeddings")
            db.commit()
        return {"ok": True}

    return router
