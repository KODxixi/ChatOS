"""Gchat plugin: projects, memories and decisions API surface.

The Gchat service is the consumer-facing projection of the data pool; this
plugin also registers the (not yet configured) insights job handler.
"""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.gchat import create_gchat_router
    from ..services.gchat_service import GchatService
    from ..services.job_worker import JobResult

    gchat_service = GchatService(ctx.get("db.connection_factory"))
    ctx.provide("gchat.service", gchat_service)

    ctx.get("jobs.registry")["gchat_insights"] = (
        lambda _job, _context: JobResult(
            status="degraded",
            error_code="GCHAT_INSIGHTS_NOT_CONFIGURED",
        )
    )
    ctx.add_router("/api", create_gchat_router(gchat_service))
