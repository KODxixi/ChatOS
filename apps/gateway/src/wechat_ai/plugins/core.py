"""Core plugin: settings, database, connection factory, call ledger.

Owns the process-level bootstrap that must happen before anything else:
resolve settings, restore a pending backup if one exists, initialize the
library database, and publish the connection factory.  The maintenance
*service* is created here because the restore must run before the database
initializer; the maintenance *API* lives in the ``maintenance`` plugin.
"""

from __future__ import annotations

from typing import Any


def apply(ctx) -> None:
    settings = ctx.override("settings") or _load_settings()
    ctx.provide("settings", settings)

    maintenance = _create_maintenance(settings)
    maintenance.apply_pending_restore()
    ctx.provide("maintenance.service", maintenance)

    connection = _initialize_database(
        settings,
        initializer=ctx.override("db.initializer"),
    )
    database_initialized = connection is not None
    close = getattr(connection, "close", None)
    if callable(close):
        close()

    connection_factory = _create_connection_factory(settings.library_db)
    ctx.provide("db.connection_factory", connection_factory)
    ctx.provide("db.initialized", database_initialized)
    ctx.provide("db.call_ledger", _create_call_ledger(connection_factory))


def _load_settings() -> Any:
    from ..config import load_settings

    return load_settings()


def _create_maintenance(settings: Any) -> Any:
    from ..services.maintenance_service import MaintenanceService

    return MaintenanceService(
        library_db=settings.library_db,
        backup_dir=settings.backup_dir,
        state_dir=settings.project_root / "data" / "state",
    )


def _initialize_database(settings: Any, *, initializer: Any) -> Any:
    if initializer is not None:
        return initializer(settings.library_db)
    from ..database import initialize_database

    return initialize_database(
        settings.library_db,
        backup_dir=settings.backup_dir,
    )


def _create_connection_factory(library_db: Any) -> Any:
    from ..services.conversation_service import create_connection_factory

    return create_connection_factory(library_db)


def _create_call_ledger(connection_factory: Any) -> Any:
    from ..services.call_ledger_service import CallLedgerService

    return CallLedgerService(connection_factory)
