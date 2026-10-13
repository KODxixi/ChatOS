"""Application bootstrap: compose the Chatbox service from plugin rows.

``create_app`` keeps the historical signature used by the CLI, the launcher
and the test-suite.  The actual assembly is a Cordis-style kernel
(:mod:`wechat_ai.plugins`) that applies a declarative plugin composition
(:data:`wechat_ai.plugins.composition.DEFAULT_COMPOSITION`): every capability
is a plugin row that publishes services, subscribes to events and registers
routers.  Adding a capability means adding a row, never editing this file.

The module also keeps a few historical names alive for the test-suite and for
the CLI monkeypatch seam (``EmbeddingClient`` / ``RerankerClient`` /
``LmStudioClient`` and the ``_``-prefixed health/media helpers that moved into
plugins).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .config import Settings
from .credential_store import CredentialStore
from .lm_studio import LmStudioClient  # noqa: F401  (test monkeypatch seam)
from .plugins.composition import DEFAULT_COMPOSITION
from .plugins.health import media_health as _media_health  # noqa: F401
from .plugins.health import probe_source as _probe_source  # noqa: F401
from .plugins.kernel import Kernel
from .plugins.media import (  # noqa: F401
    media_followup_needed as _media_followup_needed,
)
from .plugins.media import media_followup_payload as _media_followup_payload  # noqa: F401
from .plugins.media import merge_enrichment_results as _merge_enrichment_results  # noqa: F401
from .runtime_contract import APP_VERSION
from .services.embedding_service import EmbeddingClient  # noqa: F401
from .services.reranker_service import RerankerClient  # noqa: F401

DatabaseInitializer = Callable[[Path], Any]
SemanticEnricher = Callable[[], object]


def create_app(
    settings: Settings | None = None,
    *,
    source_probe: Callable[[], str] | None = None,
    library_probe: Callable[[], str] | None = None,
    lm_probe: Callable[[], str] | None = None,
    initialize_database: DatabaseInitializer | None = None,
    credential_store: CredentialStore | None = None,
    agent_provider_factory: Any | None = None,
    semantic_enricher: SemanticEnricher | None = None,
) -> FastAPI:
    """Compose and return the Chatbox FastAPI application.

    Keyword overrides are bootstrap seams (tests and the CLI): when provided
    they replace the plugin's default construction for settings, the database
    initializer, credential store, agent provider factory, semantic enricher,
    health probes.  A ``None`` keyword means
    "use the plugin default", so only non-``None`` values enter the override
    table.
    """
    overrides: dict[str, Any] = {
        "answer.embedding_factory": EmbeddingClient,
        "answer.reranker_factory": RerankerClient,
    }
    provided = {
        "settings": settings,
        "probe.source": source_probe,
        "probe.library": library_probe,
        "probe.lm": lm_probe,
        "db.initializer": initialize_database,
        "credentials.store": credential_store,
        "agent.provider_factory": agent_provider_factory,
        "semantic.enricher": semantic_enricher,
    }
    for key, value in provided.items():
        if value is not None:
            overrides[key] = value
    kernel = Kernel(
        DEFAULT_COMPOSITION["plugins"],
        overrides=overrides,
    )
    kernel.apply()
    return kernel.build_app(title="Chatbox", version=APP_VERSION)
