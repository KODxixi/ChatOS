from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .operations_service import (
    BackgroundJobService,
    ClaimedJob,
    SyncSettingsService,
)


_LOGGER = logging.getLogger(__name__)
JobHandler = Callable[[ClaimedJob, "JobExecutionContext"], object]
ErrorReporter = Callable[[Exception], None]


@dataclass(frozen=True, slots=True)
class JobResult:
    status: str = "completed"
    error_code: str | None = None


class JobExecutionContext:
    def __init__(self, jobs: BackgroundJobService, job_id: str) -> None:
        self._jobs = jobs
        self._job_id = job_id

    def should_cancel(self) -> bool:
        return self._jobs.should_cancel(self._job_id)


class JobWorker:
    def __init__(
        self,
        jobs: BackgroundJobService,
        *,
        handlers: Mapping[str, JobHandler],
        error_reporter: ErrorReporter | None = None,
        thread_name: str = "gchat-background-jobs",
    ) -> None:
        self._jobs = jobs
        self._handlers = dict(handlers)
        self._error_reporter = error_reporter
        self._thread_name = thread_name
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def run_once(self) -> bool:
        job = self._jobs.claim_next()
        if job is None:
            return False
        context = JobExecutionContext(self._jobs, job.id)
        if context.should_cancel():
            self._jobs.complete(job.id)
            return True
        handler = self._handlers.get(job.type)
        try:
            if handler is None:
                raise RuntimeError("background job handler is not configured")
            raw_result = handler(job, context)
            if context.should_cancel():
                self._jobs.complete(job.id)
                return True
            result = raw_result if isinstance(raw_result, JobResult) else JobResult()
            if result.status == "completed":
                self._jobs.complete(job.id)
            elif result.status == "degraded":
                self._jobs.degrade(
                    job.id,
                    error_code=result.error_code or "JOB_DEGRADED",
                )
            elif result.status == "failed":
                self._jobs.fail(
                    job.id,
                    error_code=result.error_code or "JOB_HANDLER_FAILED",
                )
            else:
                raise ValueError("background job handler returned an invalid status")
        except Exception as exc:
            current = self._jobs.get_job(job.id)
            if current["status"] not in {
                "completed",
                "degraded",
                "failed",
                "cancelled",
                "interrupted",
            }:
                self._jobs.fail(job.id, error_code="JOB_HANDLER_FAILED")
            self._report(exc)
            raise
        return True

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait()
            self._wake.clear()
            while not self._stopping.is_set():
                try:
                    if not self.run_once():
                        break
                except Exception:
                    continue

    def _report(self, exc: Exception) -> None:
        if self._error_reporter is None:
            _LOGGER.exception("background job handler failed", exc_info=exc)
            return
        try:
            self._error_reporter(exc)
        except Exception:
            _LOGGER.exception("background job error reporter failed")


class SyncImportScheduler:
    def __init__(
        self,
        *,
        jobs: BackgroundJobService,
        settings: SyncSettingsService,
        notify_worker: Callable[[], None],
        thread_name: str = "gchat-sync-import",
    ) -> None:
        self._jobs = jobs
        self._settings = settings
        self._notify_worker = notify_worker
        self._thread_name = thread_name
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()

    def notify_settings_changed(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def run_once(self) -> dict[str, Any] | None:
        settings = self._settings.get()
        if settings["enabled"] is not True:
            return None
        job = self._jobs.create_job("import", trigger_source="sync")
        self._notify_worker()
        return job

    def _run(self) -> None:
        while not self._stopping.is_set():
            settings = self._settings.get()
            if settings["enabled"] is not True:
                self._wake.wait()
                self._wake.clear()
                continue
            timeout = int(settings["intervalMinutes"]) * 60
            settings_changed = self._wake.wait(timeout=timeout)
            self._wake.clear()
            if self._stopping.is_set():
                break
            if settings_changed:
                continue
            try:
                self.run_once()
            except Exception:
                self._enqueue_with_retry()

    def _enqueue_with_retry(self) -> None:
        """定时入队在瞬时写锁（database is locked）下会失败；短退避重试，避免错过整个周期。

        BEGIN IMMEDIATE 已有 busy_timeout（operations_service 连接 20s），但全量
        embed（parallelSlots=16）长事务可能仍让入队超时；这里再补 4 次退避重试，
        全部失败才按异常记录，下一周期照常继续。
        """
        delays = (5, 10, 20, 30)
        for attempt, delay in enumerate(delays, start=1):
            try:
                self.run_once()
                return
            except Exception as exc:
                if self._stopping.is_set():
                    return
                _LOGGER.warning(
                    "scheduled import enqueue failed (%s), retry %d/%d in %ds",
                    exc,
                    attempt,
                    len(delays),
                    delay,
                )
                self._stopping.wait(delay)
                if self._stopping.is_set():
                    return
        _LOGGER.exception("scheduled import enqueue failed after %d retries", len(delays))
