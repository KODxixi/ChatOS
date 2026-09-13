"""Operations plugin: WeChat key operations, jobs and backups management API."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.operations import create_operations_router
    from ..services.operations_service import (
        WechatSourceKeyService,
        create_project_wechat_key_verifier,
    )
    from ..services.wechat_key_preparer_service import (
        WechatKeyPreparerService,
        launch_wechat_key_preparer,
    )

    settings = ctx.get("settings")
    connection_factory = ctx.get("db.connection_factory")
    jobs = ctx.get("jobs.service")

    wechat_key_service = WechatSourceKeyService(
        connection_factory,
        credential_store=ctx.get("credentials.wechat"),
        verifier=create_project_wechat_key_verifier(settings.project_root),
        data_root_probe=lambda: settings.project_root.is_dir(),
        archive_root_probe=lambda: settings.library_db.parent.is_dir(),
        wechat_source_probe=ctx.get("setup.service").wechat_source_detected,
        default_model_probe=ctx.get("models.default_status"),
        first_import_probe=ctx.get("jobs.first_import_status"),
        project_root=settings.project_root,
    )
    wechat_key_preparer = WechatKeyPreparerService(
        result_dir=settings.project_root / "data" / "key-preparer",
        key_service=wechat_key_service,
        launcher=launch_wechat_key_preparer,
    )
    ctx.provide("operations.keys", wechat_key_service)
    ctx.provide("operations.preparer", wechat_key_preparer)
    ctx.add_router(
        "/api",
        create_operations_router(
            jobs=jobs,
            keys=wechat_key_service,
            sync_settings=ctx.get("jobs.sync_settings"),
            job_created=ctx.get("jobs.notify"),
            sync_settings_changed=ctx.get("jobs.notify_settings_changed"),
            preparer=wechat_key_preparer,
        ),
    )
