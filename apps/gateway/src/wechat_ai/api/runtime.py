from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel

from ..database import current_schema_version, required_schema_version
from ..runtime_contract import API_CONTRACT_VERSION, APP_VERSION, web_build_id


ConnectionFactory = Callable[[], sqlite3.Connection]


class RuntimeResponse(BaseModel):
    appVersion: str
    apiContractVersion: str
    schemaVersion: int
    requiredSchemaVersion: int
    webBuildId: str


def create_runtime_router(
    connection_factory: ConnectionFactory,
    *,
    web_dist: Path,
) -> APIRouter:
    router = APIRouter()
    build_id = web_build_id(web_dist)
    required_version = required_schema_version()

    @router.get("/runtime", response_model=RuntimeResponse)
    def runtime() -> RuntimeResponse:
        with closing(connection_factory()) as connection:
            schema_version = current_schema_version(connection)
        return RuntimeResponse(
            appVersion=APP_VERSION,
            apiContractVersion=API_CONTRACT_VERSION,
            schemaVersion=schema_version,
            requiredSchemaVersion=required_version,
            webBuildId=build_id,
        )

    return router
