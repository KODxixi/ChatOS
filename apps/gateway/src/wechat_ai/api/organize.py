"""Incremental organize API (ChatOS v0.4.0).

POST /api/organize/incremental — enqueue an incremental organize job
and return HTTP 202 immediately.  The actual pipeline runs in the
background JobWorker; poll GET /api/jobs/{job_id} for status and result.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..services.conversation_service import ConnectionFactory
from ..services.operations_service import (
    BackgroundJobService,
    OperationsError,
)

logger = logging.getLogger(__name__)


def create_organize_router(
    connection_factory: ConnectionFactory,
    jobs_service: BackgroundJobService,
    notify_worker: Any,
) -> APIRouter:
    router = APIRouter()

    @router.post("/organize/incremental", status_code=202, response_model=None)
    def start_incremental_organize() -> dict[str, Any] | JSONResponse:
        """Enqueue an incremental organize job.

        Returns immediately with 202 + job info.  The pipeline runs
        asynchronously; poll GET /api/jobs/{id} for progress and result.
        """
        try:
            job = jobs_service.create_job(
                "incremental_organize",
                trigger_source="manual",
                payload={"cause": "incremental_organize"},
            )
            notify_worker()
            logger.info("organize.incremental: enqueued job=%s", job["id"])
            return {
                "id": job["id"],
                "type": job["type"],
                "status": job["status"],
            }
        except OperationsError as exc:
            logger.warning("organize.incremental failed: %s", exc)
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": str(exc),
                    "code": getattr(exc, "code", "ORGANIZE_ENQUEUE_FAILED"),
                },
            )

    return router
