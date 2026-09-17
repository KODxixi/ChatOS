"""Vector index + semantic search + RAG API."""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from ._errors import api_error_response
from ..services import embedding_service as es
from ..services import llm_service as llm
from ..services.conversation_service import ConnectionFactory

logger = logging.getLogger(__name__)


def create_vector_router(connection_factory: ConnectionFactory, db_path: str) -> APIRouter:
    router = APIRouter()

    class IndexBody(BaseModel):
        conversation_id: Optional[str] = None
        full: bool = False

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

    @router.get("/search/semantic")
    def semantic_search(
        q: str = Query(...),
        conversation_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.3,
    ) -> dict[str, Any]:
        with closing(connection_factory()) as db:
            results = es.search(db, q, conversation_id=conversation_id, limit=limit, threshold=threshold)
        # highlight query terms
        terms = [t for t in re.split(r"\s+", q) if len(t) >= 2]
        for r in results:
            c = r["content"]
            for t in terms:
                c = re.sub(f"({re.escape(t)})", r"<mark>\1</mark>", c)
            r["content"] = c
        return {"query": q, "results": results, "count": len(results)}

    class RagBody(BaseModel):
        question: str
        conversationId: str | None = None
        limit: int = 8

    @router.post("/rag/ask")
    def rag_ask(body: RagBody) -> dict[str, Any]:
        with closing(connection_factory()) as db:
            hits = es.search(db, body.question, conversation_id=body.conversationId,
                             limit=body.limit, threshold=0.25)
        if not hits:
            return {"answer": None, "semanticResults": [], "citations": []}
        ctx = "\n".join(f"[{h['senderName']}] {h['content']}" for h in hits)
        sys = ("你是一个基于微信对话记录的问答助手。根据以下对话片段回答问题，引用来源。"
               "如果对话中没有相关信息，如实说不知道。")
        user = f"对话片段：\n{ctx}\n\n问题：{body.question}"
        try:
            answer = llm.complete(sys, user, max_tokens=400, temperature=0.2)
        except Exception as e:
            logger.warning("RAG LLM failed: %s", e)
            return {"answer": None, "semanticResults": hits, "citations": []}
        return {
            "answer": answer,
            "citations": [
                {"messageId": h["messageId"], "senderName": h["senderName"],
                 "createdAt": h["createdAt"], "snippet": h["content"][:80]}
                for h in hits[:5]
            ],
            "sourcesUsed": len(hits),
        }

    return router
