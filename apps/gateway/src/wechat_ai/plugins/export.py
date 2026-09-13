"""Export plugin: user-initiated conversation export."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.export_data import create_export_router
    from ..services.export_service import ExportService

    settings = ctx.get("settings")
    ctx.add_router(
        "/api",
        create_export_router(
            export_runner=ExportService(settings).export_conversation,
        ),
    )
