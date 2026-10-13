"""Chatbox plugin system (Cordis-style kernel).

The DSH runtime composes every capability from plugin rows in a declarative
composition: each plugin owns an ``apply(ctx)`` build phase plus optional
``start(ctx)`` / ``stop(ctx)`` lifecycle phases, services are published with
``ctx.provide`` and consumed with ``ctx.get``, and plugins communicate through
kernel events.  This package is that model, scaled down to a single FastAPI
process, so the Chatbox service is assembled from the same kind of declarative
plugin rows instead of one hand-wired composition function.

Public surface:

- :class:`wechat_ai.plugins.kernel.Kernel` — loads a composition, applies it,
  and builds the FastAPI application.
- :class:`wechat_ai.plugins.kernel.Context` — per-plugin handle (services,
  events, routers, lifecycle state).
- :data:`wechat_ai.plugins.composition.DEFAULT_COMPOSITION` — the built-in
  declarative plugin rows.
"""

from __future__ import annotations

from .kernel import (
    Context,
    DuplicateServiceError,
    Kernel,
    MissingServiceError,
    PluginError,
    PluginSpec,
)
from .composition import DEFAULT_COMPOSITION

__all__ = [
    "Context",
    "DEFAULT_COMPOSITION",
    "DuplicateServiceError",
    "Kernel",
    "MissingServiceError",
    "PluginError",
    "PluginSpec",
]
