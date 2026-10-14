"""organize 插件的自动刷新节流：导入完成后按间隔决定要不要排整理任务。

取代了此前的「每小时的 Windows 计划任务」，所以这里锁死两条：够久才排、不够久不排。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_ai.plugins.organize import (
    _AUTO_REFRESH_INTERVAL_MINUTES,
    _subscribe_auto_refresh,
)


def _iso_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


class _FakeJobs:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    def create_job(self, job_type: str, *, trigger_source: str, payload=None):
        self.created.append((job_type, trigger_source))
        return {"id": "job-fake"}


class _FakeCtx:
    """只实现本插件用到的两个方法；knowledge.index 故意缺省（返回 None 即跳过索引）。"""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def get(self, name: str):
        return None


def _make_db(tmp_path: Path, *, last_organize_minutes_ago: int | None) -> Path:
    db_path = tmp_path / "library.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE background_jobs (type TEXT, status TEXT, created_at TEXT)"
    )
    if last_organize_minutes_ago is not None:
        connection.execute(
            "INSERT INTO background_jobs VALUES ('incremental_organize', 'degraded', ?)",
            (_iso_ago(last_organize_minutes_ago),),
        )
    connection.commit()
    connection.close()
    return db_path


def _subscribe(tmp_path: Path, *, minutes_ago: int | None):
    db_path = _make_db(tmp_path, last_organize_minutes_ago=minutes_ago)

    def connection_factory():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    jobs = _FakeJobs()
    ctx = _FakeCtx()
    notified: list[bool] = []
    _subscribe_auto_refresh(
        ctx,
        connection_factory=connection_factory,
        jobs_service=jobs,
        notify_worker=lambda: notified.append(True),
        settings=object(),
    )
    return ctx, jobs, notified


def _fire(ctx: _FakeCtx) -> None:
    for handler in ctx.handlers["import.completed"]:
        handler(None)


def test_subscribes_to_import_completed(tmp_path: Path) -> None:
    ctx, _jobs, _notified = _subscribe(tmp_path, minutes_ago=None)
    assert "import.completed" in ctx.handlers


def test_enqueues_organize_when_last_run_is_older_than_interval(tmp_path: Path) -> None:
    ctx, jobs, notified = _subscribe(
        tmp_path, minutes_ago=_AUTO_REFRESH_INTERVAL_MINUTES + 10
    )
    _fire(ctx)
    assert jobs.created == [("incremental_organize", "import")]
    assert notified == [True]


def test_enqueues_organize_when_no_previous_run_exists(tmp_path: Path) -> None:
    ctx, jobs, _notified = _subscribe(tmp_path, minutes_ago=None)
    _fire(ctx)
    assert jobs.created == [("incremental_organize", "import")]


def test_skips_organize_when_last_run_is_within_interval(tmp_path: Path) -> None:
    ctx, jobs, notified = _subscribe(
        tmp_path, minutes_ago=_AUTO_REFRESH_INTERVAL_MINUTES - 10
    )
    _fire(ctx)
    assert jobs.created == []
    assert notified == []
