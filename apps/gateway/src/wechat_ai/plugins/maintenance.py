"""Maintenance plugin: backup/restore API and the backup job handler.

The MaintenanceService itself is created by the ``core`` plugin before the
database is initialized (a pending restore must run first); this plugin owns
the API surface and the background job that creates backups on demand.
"""

from __future__ import annotations

import os

from ..runtime_contract import APP_VERSION


def apply(ctx) -> None:
    from ..api.maintenance import create_maintenance_router
    from ..services.job_worker import JobResult
    from ..services.maintenance_service import UpdateService

    maintenance = ctx.get("maintenance.service")
    updates = UpdateService(
        current_version=APP_VERSION,
        manifest_url=os.environ.get("GCHAT_UPDATE_MANIFEST_URL"),
    )
    ctx.provide("maintenance.updates", updates)

    def run_backup_job(_job: object, _context: object) -> JobResult:
        maintenance.create_backup()
        return JobResult()

    ctx.get("jobs.registry")["backup"] = run_backup_job
    ctx.add_router(
        "/api",
        create_maintenance_router(maintenance=maintenance, updates=updates),
    )
