"""Import plugin: the read-only import pipeline and its job handler.

Emits ``import.completed`` after a successful import; the ``projection``
plugin listens and enqueues the projection pipeline, so the import pipeline
never needs to know which downstream projections exist.
"""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.import_data import create_import_router
    from ..services.import_service import ImportService, ImportServiceError
    from ..services.job_worker import JobResult
    from ..wechat_decrypt import refresh_project_sources_from_credentials

    settings = ctx.get("settings")
    connection_factory = ctx.get("db.connection_factory")
    wechat_credential_store = ctx.get("credentials.wechat")

    import_service = ImportService(
        settings,
        source_refresher=lambda: refresh_project_sources_from_credentials(
            settings.project_root,
            settings.source_dir,
            connection_factory=connection_factory,
            credential_store=wechat_credential_store,
        ),
        after_import=lambda: ctx.emit("import.completed"),
    )
    ctx.provide("import.service", import_service)

    def run_import_job(_job: object, _context: object) -> JobResult:
        try:
            import_service.run()
        except ImportServiceError as exc:
            return JobResult(status="failed", error_code=exc.code)
        return JobResult()

    ctx.get("jobs.registry")["import"] = run_import_job
    ctx.add_router(
        "/api",
        create_import_router(import_runner=import_service.run),
    )
