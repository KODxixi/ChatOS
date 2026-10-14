"""Organize plugin: incremental organize job handler + API router.

Registers the ``incremental_organize`` job handler into the jobs
registry and mounts the ``/api/organize/*`` router.

Also owns the **auto refresh cycle**: after every completed import the plugin
decides whether the throttled organize + knowledge-index round is due.  This
replaced the external hourly scheduled task (removed 2026-10-14) — the gateway
no longer relies on anything outside it to keep memory fresh.
"""

from __future__ import annotations

import logging
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..services.job_worker import JobResult

logger = logging.getLogger(__name__)

# 自动整理 + 知识索引的节流间隔。这是「按需刷新」模型里的**节流阀，不是时钟**：
# 网关不自起周期任务，只在每次导入完成后顺带判断「距上轮刷新是否够久」。
_AUTO_REFRESH_INTERVAL_MINUTES = 180


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

    _subscribe_auto_refresh(
        ctx,
        connection_factory=connection_factory,
        jobs_service=jobs_service,
        notify_worker=notify_worker,
        settings=settings,
    )


def _subscribe_auto_refresh(
    ctx,
    *,
    connection_factory,
    jobs_service,
    notify_worker,
    settings,
) -> None:
    """导入完成后，按节流间隔顺带跑一轮「整理 + 知识索引」。

    上轮刷新的标记就是上一轮 ``incremental_organize`` 任务的 ``created_at`` —— 一个
    标记覆盖整个循环，不需要另存状态。
    """

    def _auto_refresh_due() -> bool:
        with closing(connection_factory()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM background_jobs
                WHERE type = 'incremental_organize' AND created_at > ?
                LIMIT 1
                """,
                (_iso_ago(_AUTO_REFRESH_INTERVAL_MINUTES),),
            ).fetchone()
        return row is None

    def _enqueue_organize() -> None:
        try:
            job = jobs_service.create_job(
                "incremental_organize",
                trigger_source="import",
                payload={"cause": "incremental_organize"},
            )
            notify_worker()
            logger.info("organize.auto: enqueued job=%s", job["id"])
        except Exception as exc:  # noqa: BLE001 - 入队失败不该打断导入
            logger.warning("organize.auto: enqueue failed: %s", exc)

    def _kick_knowledge_index() -> None:
        # 运行时才取：本插件在 composition 里排在 query 之前，apply 阶段取不到。
        index = ctx.get("knowledge.index")
        if index is None:
            return

        def _run() -> None:
            try:
                if index.start():
                    index.refresh(
                        connection_factory,
                        asset_root=getattr(settings, "asset_dir", None),
                    )
            except Exception:  # noqa: BLE001 - 后台线程，失败只记录
                logger.exception("knowledge index auto refresh failed")

        threading.Thread(
            target=_run,
            name="knowledge-index-auto",
            daemon=True,
        ).start()

    def on_import_completed(_payload: object = None) -> None:
        if not _auto_refresh_due():
            return
        _enqueue_organize()
        _kick_knowledge_index()

    ctx.on("import.completed", on_import_completed)
    logger.info(
        "organize plugin: auto refresh subscribed (every %d min after import)",
        _AUTO_REFRESH_INTERVAL_MINUTES,
    )


def _iso_ago(minutes: int) -> str:
    """与 background_jobs.created_at 同格式（UTC / 微秒 / Z 结尾）的截止时刻。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
