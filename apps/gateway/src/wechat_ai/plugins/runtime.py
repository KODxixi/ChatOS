"""Runtime plugin: the runtime contract endpoint (/api/runtime)."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.runtime import create_runtime_router

    settings = ctx.get("settings")
    dist = (ctx.override("web_dist") or settings.web_dist).resolve()
    ctx.add_router(
        "/api",
        create_runtime_router(ctx.get("db.connection_factory"), web_dist=dist,
                              library_db=settings.library_db, frontend_url=settings.frontend_url,
                              config_path=settings.config_path, config_identity=settings.config_identity),
    )
