from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from fastapi import APIRouter

from ..services.conversation_service import ConnectionFactory
from ..services.knowledge_service import list_sources


def create_sources_router(connection_factory: ConnectionFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/sources")
    def sources() -> dict[str, Any]:
        with closing(connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            return list_sources(connection)

    return router
