from __future__ import annotations

import threading
from pathlib import Path

import pytest


def _services(tmp_path: Path):
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.operations_service import (
        BackgroundJobService,
        SyncSettingsService,
    )

    database_path = tmp_path / "library.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    identifiers = iter(("job-1", "job-2", "job-3", "job-4"))
    jobs = BackgroundJobService(
        connection_factory,
        clock=lambda: "2026-08-02T00:00:00Z",
        id_factory=lambda: next(identifiers),
    )
    return jobs, SyncSettingsService(connection_factory)


def test_worker_runs_injected_handler_and_persists_completion(tmp_path: Path) -> None:
    from wechat_ai.services.job_worker import JobWorker

    jobs, _sync_settings = _services(tmp_path)
    calls: list[tuple[str, bool]] = []

    def import_handler(job, context):
        calls.append((job.id, context.should_cancel()))

    worker = JobWorker(jobs, handlers={"import": import_handler})
    jobs.create_job("import", trigger_source="manual")

    assert worker.run_once() is True
    assert calls == [("job-1", False)]
    assert jobs.get_job("job-1")["status"] == "completed"
    assert worker.run_once() is False


def test_worker_event_wakes_single_background_thread(tmp_path: Path) -> None:
    from wechat_ai.services.job_worker import JobWorker

    jobs, _sync_settings = _services(tmp_path)
    completed = threading.Event()
    worker = JobWorker(
        jobs,
        handlers={"import": lambda _job, _context: completed.set()},
    )
    jobs.create_job("import", trigger_source="manual")

    worker.start()
    try:
        worker.notify()
        assert completed.wait(timeout=2)
    finally:
        worker.stop()

    assert jobs.get_job("job-1")["status"] == "completed"


def test_worker_persists_stable_failure_code_and_reports_handler_exception(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.job_worker import JobWorker

    jobs, _sync_settings = _services(tmp_path)
    reported: list[Exception] = []

    def fail_handler(_job, _context):
        raise RuntimeError("private handler details")

    worker = JobWorker(
        jobs,
        handlers={"media": fail_handler},
        error_reporter=reported.append,
    )
    jobs.create_job("media", trigger_source="manual")

    with pytest.raises(RuntimeError, match="private handler details"):
        worker.run_once()

    assert jobs.get_job("job-1")["status"] == "failed"
    assert jobs.get_job("job-1")["errorCode"] == "JOB_HANDLER_FAILED"
    assert len(reported) == 1


def test_worker_honors_cancel_at_handler_boundary(tmp_path: Path) -> None:
    from wechat_ai.services.job_worker import JobWorker

    jobs, _sync_settings = _services(tmp_path)
    called = False

    def handler(_job, _context):
        nonlocal called
        called = True

    worker = JobWorker(jobs, handlers={"backup": handler})
    created = jobs.create_job("backup", trigger_source="manual")
    jobs.request_cancel(created["id"])

    assert worker.run_once() is False
    assert called is False
    assert jobs.get_job(created["id"])["status"] == "cancelled"


def test_sync_scheduler_defaults_off_and_merges_enabled_import_jobs(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.job_worker import SyncImportScheduler

    jobs, sync_settings = _services(tmp_path)
    notifications: list[bool] = []
    scheduler = SyncImportScheduler(
        jobs=jobs,
        settings=sync_settings,
        notify_worker=lambda: notifications.append(True),
    )

    assert scheduler.run_once() is None
    assert jobs.list_jobs() == {"items": [], "total": 0}
    sync_settings.patch(enabled=True, interval_minutes=5)
    first = scheduler.run_once()
    second = scheduler.run_once()

    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]
    assert len(jobs.list_jobs()["items"]) == 1
    assert notifications == [True, True]
