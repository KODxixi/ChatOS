"""Organize plugin: incremental organize job handler + API router.

Registers the ``incremental_organize`` job handler into the jobs
registry and mounts the ``/api/organize/*`` router.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..services.job_worker import JobResult

logger = logging.getLogger(__name__)


def apply(ctx) -> None:
    from ..api.organize import create_organize_router
    from ..services.incremental_organize import run_incremental_organize

    connection_factory = ctx.get("db.connection_factory")
    jobs_service = ctx.get("jobs.service")
    notify_worker = ctx.get("jobs.notify")
    settings = ctx.get("settings")

    # Vault root for project extraction (conversations/project/*.md).
    vault_root = Path(getattr(settings, "vault_root", None) or Path(settings.library_db).parent.parent)

    registry = ctx.get("jobs.registry")

    def handle_incremental_organize(job, context) -> JobResult:
        return run_incremental_organize(
            connection_factory,
            jobs_service,
            job.id,
            vault_root=vault_root,
        )

    registry["incremental_organize"] = handle_incremental_organize
    logger.info("organize plugin: handler registered")

    ctx.add_router(
        "/api",
        create_organize_router(connection_factory, jobs_service, notify_worker),
    )
