"""Setup plugin: first-run setup probes and the setup API."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.setup import create_setup_router
    from ..services.setup_service import SetupService

    setup_service = SetupService(project_root=ctx.get("settings").project_root)
    ctx.provide("setup.service", setup_service)
    ctx.add_router("/api", create_setup_router(setup_service))
