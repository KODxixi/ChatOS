"""Declarative plugin composition for the Chatbox service.

This is the Chatbox equivalent of a DSH ``cordis.yml``: a plain list of plugin
rows.  Every row names a plugin module, its hard dependencies (``inject``) and
optional ``config``; the kernel topologically orders them and applies them in
that order.  Adding a capability means adding a row (or a plugin module),
never editing the composition function.
"""

from __future__ import annotations

from typing import Any

DEFAULT_COMPOSITION: dict[str, Any] = {
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
    ],
}
