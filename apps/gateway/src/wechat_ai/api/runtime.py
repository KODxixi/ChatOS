from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
import sqlite3
import os

from fastapi import APIRouter
from pydantic import BaseModel

from ..database import current_schema_version, required_schema_version
from ..runtime_contract import API_CONTRACT_VERSION, APP_VERSION, GATEWAY_SOURCE_ROOT, GATEWAY_STARTUP_IDENTITY


ConnectionFactory = Callable[[], sqlite3.Connection]


class RuntimeResponse(BaseModel):
    appVersion: str
    apiContractVersion: str
    schemaVersion: int
    requiredSchemaVersion: int
    service: str = "chatos-gateway"
    sourceRoot: str
    startupCodeIdentity: str
    libraryDb: str
    pid: int
    configPath: str | None
    configIdentity: str | None


def create_runtime_router(
    connection_factory: ConnectionFactory,
    *,
    library_db: Path,
    config_path: Path | None = None,
    config_identity: str | None = None,
) -> APIRouter:
    router = APIRouter()
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
            sourceRoot=str(GATEWAY_SOURCE_ROOT),
            startupCodeIdentity=GATEWAY_STARTUP_IDENTITY,
            libraryDb=str(library_db.resolve()),
            pid=os.getpid(),
            configPath=str(config_path) if config_path else None,
            configIdentity=config_identity,
        )

    return router
