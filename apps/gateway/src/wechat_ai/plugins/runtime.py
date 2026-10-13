"""Runtime plugin: the runtime contract endpoint (/api/runtime)."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.runtime import create_runtime_router

    settings = ctx.get("settings")
    ctx.add_router(
        "/api",
        create_runtime_router(ctx.get("db.connection_factory"),
                              library_db=settings.library_db,
                              config_path=settings.config_path, config_identity=settings.config_identity),
    )
