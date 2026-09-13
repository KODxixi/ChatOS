"""Health plugin: /api/health and the probe functions.

The probe helpers (source/library/lm/operational/media health) moved here from
``app.py`` unchanged; ``wechat_ai.app`` re-exports ``probe_source`` and
``media_health`` under their historical ``_``-prefixed names for the
test-suite.  Bootstrap overrides (``probe.source`` etc.) let tests and the CLI
inject synthetic probe results.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def _ro_connection(library_db: Path) -> sqlite3.Connection:
    """Open a read-only connection so health probes never contend with writers.

    In WAL mode a read-only connection uses snapshot isolation and cannot
    block or be blocked by writers; the shared read-write factory used
    elsewhere can still queue behind a long write transaction (busy_timeout
    30 s), which previously made /api/health hang for minutes.
    """
    uri = f"{library_db.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    # Match the 30 s busy_timeout used by the read-write factory; a read-only
    # connection in WAL mode should never need it, but consistency avoids
    # surprises if the underlying file is briefly locked during checkpoint.
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def apply(ctx) -> None:
    from ..api.health import create_health_router

    settings = ctx.get("settings")
    model_profiles = ctx.get("models.service")
    call_ledger = ctx.get("db.call_ledger")

    source_probe = ctx.override(
        "probe.source",
        lambda: probe_source(settings.source_dir),
    )
    library_probe = ctx.override(
        "probe.library",
        lambda: probe_library(settings.library_db),
    )
    lm_probe = ctx.override(
        "probe.lm",
        lambda: probe_lm(settings.lm_base_url),
    )

    def operational_probe() -> dict[str, object]:
        return probe_operational_health(
            settings.library_db, model_profiles, call_ledger
        )

    ctx.add_router(
        "/api",
        create_health_router(
            source_probe=source_probe,
            library_probe=library_probe,
            lm_probe=lm_probe,
            operational_probe=operational_probe,
        ),
    )


def probe_library(library_db: Path) -> str:
    with closing(_ro_connection(library_db)) as connection:
        row = connection.execute(
            "SELECT 1 FROM conversations UNION ALL SELECT 1 FROM messages LIMIT 1"
        ).fetchone()
    return "ready" if row is not None else "empty"


def probe_source(
    source_dir: Path,
    *,
    discover_sources: Callable[[Path], list[Any]] | None = None,
) -> str:
    if not source_dir.is_dir():
        return "missing"
    try:
        has_sqlite = any(
            path.is_file()
            and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            for path in source_dir.iterdir()
        )
    except OSError:
        return "missing"
    if not has_sqlite:
        return "missing"

    if discover_sources is None:
        from ..wechat_db import discover_sources as discover_sources_adapter

        discover_sources = discover_sources_adapter
    try:
        sources = discover_sources(source_dir)
    except Exception:
        return "unsupported"
    return "ready" if sources else "unsupported"


def probe_lm(base_url: str) -> str:
    request = Request(f"{base_url}/models", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=0.5) as response:
            return "ready" if 200 <= response.status < 300 else "offline"
    except (OSError, TimeoutError, URLError):
        return "offline"


def probe_operational_health(
    library_db: Path,
    model_profiles: Any,
    call_ledger: Any,
) -> dict[str, object]:
    fallback = {
        "tasks": {"status": "disabled"},
        "retrieval": {"status": "disabled"},
        "media": {"status": "disabled"},
        "deterministicGraph": {"status": "disabled"},
        "semanticGraph": {"status": "disabled"},
        "defaultModel": {"status": "offline"},
        "observability": {"status": "disabled"},
    }
    try:
        with closing(_ro_connection(library_db)) as connection:
            # Only each type's newest job decides tasks health; otherwise a
            # superseded `degraded` run (already succeeded by a later one)
            # keeps the aggregate degraded forever.
            #
            # Optimised: use a window function to pick the latest row per type
            # in one pass instead of streaming every row into Python.
            latest_job_status: dict[str, str] = {
                str(row["type"]): str(row["status"])
                for row in connection.execute(
                    """
                    SELECT type, status FROM (
                        SELECT type, status,
                               ROW_NUMBER() OVER (
                                   PARTITION BY type
                                   ORDER BY created_at DESC, rowid DESC
                               ) AS rn
                        FROM background_jobs
                    ) WHERE rn = 1
                    """
                )
            }
            projection_statuses = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT projection_status FROM source_accounts"
                )
            }
            semantic_statuses = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT semantic_graph_status FROM source_accounts"
                )
            }
            media_health_result = media_health(connection)
            message_count = int(
                connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            )
            edge_count = int(
                connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            )
    except (OSError, sqlite3.DatabaseError):
        return fallback

    latest_statuses = set(latest_job_status.values())
    if latest_statuses & {"failed", "degraded"}:
        tasks_status = "degraded"
    elif latest_statuses & {"queued", "running", "retrying"}:
        tasks_status = "indexing"
    else:
        tasks_status = "ready"

    retrieval_status = projection_health(projection_statuses)
    semantic_status = projection_health(semantic_statuses)
    graph_status = "ready" if edge_count or not message_count else "indexing"

    default_status = "offline"
    try:
        default_profile = next(
            (
                profile
                for profile in model_profiles.list_profiles()
                if profile.get("isDefault") is True
            ),
            None,
        )
        if default_profile is None:
            default_status = "disabled"
        elif default_profile.get("status") == "ready":
            default_status = "ready"
        elif default_profile.get("status") == "error":
            default_status = "degraded"
    except Exception:
        default_status = "offline"

    return {
        "tasks": {"status": tasks_status},
        "retrieval": {"status": retrieval_status},
        "media": media_health_result,
        "deterministicGraph": {"status": graph_status},
        "semanticGraph": {"status": semantic_status},
        "defaultModel": {"status": default_status},
        "observability": call_ledger.health(),
    }


def media_health(connection: sqlite3.Connection) -> dict[str, object]:
    """Report media processing state from actual derivative coverage."""
    media_errors = int(
        connection.execute(
            "SELECT COUNT(*) FROM media_derivatives WHERE status = 'error'"
        ).fetchone()[0]
    )
    unavailable = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM media_derivatives
            WHERE status = 'skipped'
              AND error_code IN (
                    'ENGINE_UNAVAILABLE',
                    'PLAYBACK_CACHE_UNAVAILABLE',
                    'ASSET_MISSING'
                  )
            """
        ).fetchone()[0]
    )
    unsupported = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM media_derivatives
            WHERE status = 'skipped'
              AND error_code = 'UNSUPPORTED_FILE_TYPE'
            """
        ).fetchone()[0]
    )
    pending = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM assets
            WHERE status = 'resolved'
              AND kind IN ('image', 'audio', 'file', 'attachment')
              AND NOT EXISTS (
                    SELECT 1
                    FROM media_derivatives
                    WHERE media_derivatives.asset_id = assets.id
                      AND media_derivatives.kind = CASE
                            WHEN assets.kind = 'image' THEN 'ocr'
                            WHEN assets.kind = 'audio' THEN 'transcript'
                            ELSE 'document'
                          END
                      AND media_derivatives.content_hash = COALESCE(assets.sha256, '')
                      AND (
                            media_derivatives.status = 'ready'
                            OR (
                                media_derivatives.status = 'skipped'
                                AND media_derivatives.error_code IN (
                                    'NO_TEXT',
                                    'UNSUPPORTED_FILE_TYPE',
                                    'ASSET_MISSING'
                                )
                            )
                          )
                )
            """
        ).fetchone()[0]
    )
    if media_errors or unavailable or unsupported:
        status = "degraded"
    elif pending:
        status = "indexing"
    else:
        status = "ready"
    details = []
    if pending:
        details.append(f"pending={pending}")
    if unavailable:
        details.append(f"unavailable={unavailable}")
    if unsupported:
        details.append(f"unsupported={unsupported}")
    if media_errors:
        details.append(f"errors={media_errors}")
    return {"status": status, "detail": ",".join(details) or None}


def projection_health(statuses: set[str]) -> str:
    if not statuses:
        return "ready"
    if statuses & {"degraded", "error", "failed"}:
        return "degraded"
    if statuses & {"indexing", "pending"}:
        return "indexing"
    if statuses == {"skipped"}:
        return "disabled"
    return "ready"
