"""Jobs plugin: background job queue, sync scheduler and startup seeding.

Owns the ``jobs.registry`` contribution service: every plugin that can run
background work registers its handler into that dict during ``apply`` (the
dict is populated before ``start`` builds the worker, so the worker always
sees the complete handler map).  Also owns the startup sequence that used to
live in ``create_app``: prune the call ledger, recover interrupted jobs, seed
startup projection jobs, and start the worker and the sync scheduler.
"""

from __future__ import annotations

import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)


def apply(ctx) -> None:
    from ..services.job_worker import JobWorker, SyncImportScheduler
    from ..services.operations_service import (
        BackgroundJobService,
        SyncSettingsService,
    )

    connection_factory = ctx.get("db.connection_factory")
    registry: dict[str, Any] = {}
    ctx.provide("jobs.registry", registry)

    jobs = BackgroundJobService(connection_factory)
    ctx.provide("jobs.service", jobs)

    sync_settings = SyncSettingsService(connection_factory)
    ctx.provide("jobs.sync_settings", sync_settings)

    holder: dict[str, Any] = {}
    ctx.state["holder"] = holder

    def notify() -> None:
        worker = holder.get("worker")
        if worker is not None:
            worker.notify()

    # Constructed now (no side effects); threads start in start().
    scheduler = SyncImportScheduler(
        jobs=jobs,
        settings=sync_settings,
        notify_worker=notify,
    )
    holder["scheduler"] = scheduler
    ctx.provide("jobs.notify", notify)
    ctx.provide("jobs.notify_settings_changed", scheduler.notify_settings_changed)
    ctx.provide("jobs.first_import_status", _first_import_status(jobs))


def start(ctx) -> None:
    from ..services.job_worker import JobWorker

    holder: dict[str, Any] = ctx.state["holder"]
    t0 = time.perf_counter()
    worker = JobWorker(
        ctx.get("jobs.service"),
        handlers=ctx.get("jobs.registry"),
    )
    holder["worker"] = worker
    ctx.state["worker"] = worker
    _LOGGER.info("jobs.start: worker constructed in %.1fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    ctx.get("db.call_ledger").prune_if_due()
    _LOGGER.info("jobs.start: prune_if_due in %.1fs", time.perf_counter() - t0)

    jobs = ctx.get("jobs.service")
    t0 = time.perf_counter()
    recovered = jobs.recover_interrupted()
    _LOGGER.info(
        "jobs.start: recover_interrupted %s in %.1fs",
        recovered,
        time.perf_counter() - t0,
    )

    t0 = time.perf_counter()
    jobs.create_job(
        "semantic_graph",
        trigger_source="startup",
        payload={"cause": "startup"},
    )
    jobs.create_job(
        "media",
        trigger_source="startup",
        payload={"cause": "startup"},
    )
    _LOGGER.info("jobs.start: seed jobs in %.1fs", time.perf_counter() - t0)

    worker.start()
    worker.notify()
    holder["scheduler"].start()
    _LOGGER.info("jobs.start: worker+scheduler started")


def stop(ctx) -> None:
    holder: dict[str, Any] = ctx.state["holder"]
    scheduler = holder.get("scheduler")
    if scheduler is not None:
        scheduler.stop()
    worker = holder.get("worker")
    if worker is not None:
        worker.stop()


def _first_import_status(jobs: Any) -> Any:
    def first_import_status() -> dict[str, object]:
        import_jobs = [
            item
            for item in jobs.list_jobs()["items"]
            if item.get("type") == "import"
        ]
        if not import_jobs:
            return {"started": False, "completed": False, "jobId": None}
        latest = import_jobs[0]
        return {
            "started": latest.get("status") != "queued",
            "completed": latest.get("status") == "completed",
            "jobId": latest.get("id"),
        }

    return first_import_status
