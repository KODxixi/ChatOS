from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..services.conversation_service import ConnectionFactory
from ..services.graph_service import GraphEdgeNotFound, get_edge_evidence, get_graph
from .conversations import api_error_response


def create_graph_router(connection_factory: ConnectionFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/graph")
    def graph(
        sourceAppId: str | None = None,
        sourceAccountId: str | None = None,
        conversationId: str | None = None,
        evidenceLimit: Annotated[int, Query(ge=1, le=100)] = 20,
        limit: Annotated[int, Query(ge=1, le=80)] = 80,
        hideNoise: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            payload = get_graph(
                connection,
                source_app_id=sourceAppId,
                source_account_id=sourceAccountId,
                conversation_id=conversationId,
                evidence_limit=evidenceLimit,
                limit=limit,
                hide_noise=hideNoise,
            )
        return _hide_graph_noise(payload) if hideNoise else payload

    @router.get("/graph/edges/{edge_id}/evidence", response_model=None)
    def edge_evidence(
        edge_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any] | JSONResponse:
        try:
            with closing(connection_factory()) as connection:
                connection.row_factory = sqlite3.Row
                return get_edge_evidence(connection, edge_id, limit=limit)
        except GraphEdgeNotFound:
            return api_error_response(
                status_code=404,
                code="GRAPH_EDGE_NOT_FOUND",
                message="Graph relationship was not found.",
            )

    return router


def _hide_graph_noise(
    payload: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    nodes = [
        node
        for node in payload.get("nodes", [])
        if node.get("kind") not in {"asset", "content_kind"}
    ]
    node_ids = {
        str(node["id"])
        for node in nodes
        if isinstance(node.get("id"), str)
    }
    edges = [
        edge
        for edge in payload.get("edges", [])
        if edge.get("sourceNodeId") in node_ids
        and edge.get("targetNodeId") in node_ids
    ]
    return {"nodes": nodes, "edges": edges}
