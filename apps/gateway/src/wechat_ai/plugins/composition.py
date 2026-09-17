"""Declarative plugin composition for the Chatbox service.

This is the Chatbox equivalent of a DSH ``cordis.yml``: a plain list of plugin
rows.  Every row names a plugin module, its hard dependencies (``inject``) and
optional ``config``; the kernel topologically orders them and applies them in
that order.  Adding a capability means adding a row (or a plugin module),
never editing the composition function.

The built-in composition is the single source of truth.  A deployment may
override it with a TOML file at ``config/composition.toml`` (parsed with the
standard library), which is useful for presets that add or remove plugins
without touching the repository.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_COMPOSITION: dict[str, Any] = {
    "app": {
        "title": "Chatbox",
    },
    "plugins": [
        # -- infrastructure -------------------------------------------
        # settings, database restore/init, connection factory, call ledger
        {"id": "core"},
        # Windows Credential Manager stores (wechat keys)
        {"id": "credentials", "inject": ["core"]},
        # background job queue, sync scheduler, startup seeding
        {"id": "jobs", "inject": ["core"]},
        # backup/restore API + backup job handler
        {"id": "maintenance", "inject": ["core", "jobs"]},
        # -- business services ----------------------------------------
        # projects, memories, decisions (Gchat API surface)
        {"id": "gchat", "inject": ["core", "jobs"]},
        # model profiles + default model status
        {"id": "models", "inject": ["core", "credentials"]},
        # media/document enrichment + media job handler
        {"id": "media", "inject": ["core", "jobs"]},
        # knowledge/graph projection jobs + import-completed listener
        {"id": "projection", "inject": ["core", "jobs", "gchat"]},
        # semantic graph enrichment job
        {"id": "semantic", "inject": ["core", "jobs", "models"]},
        # read-only import pipeline + import job handler
        {
            "id": "import",
            "module": "wechat_ai.plugins.importer",
            "inject": ["core", "jobs", "credentials", "gchat"],
        },
        # first-run setup probes
        {"id": "setup", "inject": ["core"]},
        # wechat key operations + job/backup management API
        {"id": "operations", "inject": ["core", "jobs", "setup", "models"]},
        # -- API surface -----------------------------------------------
        # agent runs, threads, gateway, channels
        {"id": "agent", "inject": ["core", "models", "gchat", "jobs"]},
        # two-stage RAG evidence streaming (/api/ask/stream)
        {"id": "answer", "inject": ["core"]},
        # read-only data APIs (conversations/search/sources/knowledge/...)
        {"id": "query", "inject": ["core"]},
        # incremental organize pipeline (activity/classify/tasks/projects)
        {"id": "organize", "inject": ["core", "jobs"]},
        # conversation export
        {"id": "export", "inject": ["core"]},
        # runtime contract (/api/runtime)
        {"id": "runtime", "inject": ["core"]},
        # health probes and /api/health
        {"id": "health", "inject": ["core", "models"]},
        # web frontend catch-all mount
        {"id": "frontend", "inject": ["core"]},
    ],
}


def load_composition(path: Path | None = None) -> dict[str, Any]:
    """Return the composition, optionally merged with a TOML override file.

    The override may only add/override ``app`` metadata and ``plugins`` rows;
    it must keep the same shape as :data:`DEFAULT_COMPOSITION`.  Plugin rows
    in the override replace rows with the same ``id`` and are appended after
    the built-in rows otherwise.
    """
    composition = copy.deepcopy(DEFAULT_COMPOSITION)
    if path is None or not path.is_file():
        return composition
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("composition override must be a TOML table")
    if isinstance(raw.get("app"), dict):
        composition["app"].update(raw["app"])
    override_rows = raw.get("plugins")
    if override_rows is None:
        return composition
    if not isinstance(override_rows, list):
        raise ValueError("composition override 'plugins' must be an array")
    by_id = {row["id"]: index for index, row in enumerate(composition["plugins"])}
    for row in override_rows:
        if not isinstance(row, dict) or "id" not in row:
            raise ValueError("composition plugin rows must have an 'id'")
        plugin_id = row["id"]
        if plugin_id in by_id:
            composition["plugins"][by_id[plugin_id]] = row
        else:
            composition["plugins"].append(row)
    return composition
