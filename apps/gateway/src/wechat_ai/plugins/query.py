"""Query plugin: read-only data APIs (conversations/search/sources/...).

All of these routers are pure connection-factory consumers, so they are
grouped in one plugin; they expose the data pool to the WebUI and to
Gchat/OpenClaw over the loopback API.
"""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.assets import create_assets_router
    from ..api.conversations import create_conversations_router
    from ..api.graph import create_graph_router
    from ..api.knowledge import create_knowledge_router
    from ..api.search import create_search_router
    from ..api.sources import create_sources_router
    from ..api.tasks import create_tasks_router
    from ..api.audit import create_audit_router
    from ..api.vector import create_vector_router

    connection_factory = ctx.get("db.connection_factory")
    settings = ctx.get("settings")
    ctx.add_router(
        "/api",
        create_conversations_router(
            connection_factory,
            asset_root=settings.asset_dir,
        ),
    )
    ctx.add_router("/api", create_search_router(connection_factory))
    ctx.add_router("/api", create_sources_router(connection_factory))
    ctx.add_router(
        "/api",
        create_knowledge_router(
            connection_factory,
            asset_root=settings.asset_dir,
        ),
    )
    ctx.add_router(
        "/api",
        create_assets_router(
            connection_factory,
            asset_root=settings.asset_dir,
            cache_root=settings.cache_dir,
        ),
    )
    ctx.add_router("/api", create_graph_router(connection_factory))
    ctx.add_router("/api", create_tasks_router(connection_factory))
    ctx.add_router("/api", create_audit_router(connection_factory))
    ctx.add_router("/api", create_vector_router(connection_factory, str(settings.library_db)))
