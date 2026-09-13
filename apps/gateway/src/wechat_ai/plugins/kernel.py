"""A small Cordis-style plugin kernel for the Chatbox service.

Model (mirrors the DSH runtime composition):

- **Composition** — a declarative list of plugin rows.  Every capability of the
  service is a row (``id``, ``inject`` dependencies, optional ``config``).
- **Plugin** — a module under ``wechat_ai.plugins`` that defines ``apply(ctx)``
  and optionally ``start(ctx)`` / ``stop(ctx)``.
- **Context** — the per-plugin handle.  ``apply`` builds the plugin's world:
  publish services with ``ctx.provide``, consume dependencies with
  ``ctx.get``, subscribe to events with ``ctx.on``, and register FastAPI
  routers with ``ctx.add_router``.
- **Lifecycle** — ``apply`` runs in dependency order (topological sort over
  ``inject``); ``start`` then runs in the same order and ``stop`` in reverse,
  so background workers begin after every plugin has published its services.

Failures are loud: a missing service, a duplicate service, an unknown injected
plugin, or a dependency cycle raises :class:`PluginError` subclasses instead of
silently degrading the composition.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, FastAPI

_LOGGER = logging.getLogger(__name__)


class PluginError(RuntimeError):
    """Base class for kernel loading and composition failures."""


class MissingServiceError(PluginError):
    """A plugin requested a service that no plugin has provided."""


class DuplicateServiceError(PluginError):
    """A plugin tried to provide a service name that is already provided."""


@dataclass(frozen=True, slots=True)
class PluginSpec:
    """One declarative plugin row."""

    id: str
    module: str
    inject: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "PluginSpec":
        try:
            plugin_id = str(row["id"])
        except KeyError as exc:
            raise PluginError("plugin row is missing 'id'") from exc
        module = str(row.get("module") or f"wechat_ai.plugins.{plugin_id}")
        inject_raw = row.get("inject") or ()
        if not isinstance(inject_raw, (list, tuple)):
            raise PluginError(f"plugin {plugin_id!r}: inject must be a list")
        config = row.get("config") or {}
        if not isinstance(config, dict):
            raise PluginError(f"plugin {plugin_id!r}: config must be an object")
        return cls(
            id=plugin_id,
            module=module,
            inject=tuple(str(item) for item in inject_raw),
            config=config,
        )

    def load(self) -> Any:
        return importlib.import_module(self.module)


def _order_plugins(specs: list[PluginSpec]) -> list[PluginSpec]:
    """Return plugins in topological order over their ``inject`` edges."""
    by_id = {spec.id: spec for spec in specs}
    if len(by_id) != len(specs):
        raise PluginError("composition contains duplicate plugin ids")
    incoming: dict[str, set[str]] = {}
    for spec in specs:
        deps = set()
        for dep in spec.inject:
            if dep not in by_id:
                raise PluginError(
                    f"plugin {spec.id!r} injects unknown plugin {dep!r}"
                )
            deps.add(dep)
        incoming[spec.id] = deps
    ready = [spec for spec in specs if not incoming[spec.id]]
    ordered: list[PluginSpec] = []
    while ready:
        spec = ready.pop(0)
        ordered.append(spec)
        for other in specs:
            if spec.id in incoming[other.id]:
                incoming[other.id].discard(spec.id)
                if not incoming[other.id]:
                    ready.append(other)
    if len(ordered) != len(specs):
        raise PluginError("plugin dependency cycle detected in composition")
    return ordered


class Context:
    """Per-plugin handle handed to ``apply`` / ``start`` / ``stop``.

    The same Context instance survives the three lifecycle phases, so
    ``ctx.state`` is the plugin's scratch space across them (for example, a
    worker created in ``apply`` and started in ``start``).
    """

    def __init__(self, kernel: "Kernel", spec: PluginSpec) -> None:
        self._kernel = kernel
        self.spec = spec
        self.id = spec.id
        self.config = spec.config
        self.state: dict[str, Any] = {}
        self._routers: list[tuple[str, APIRouter]] = []
        self._catchalls: list[Callable[[FastAPI], None]] = []

    # -- services -----------------------------------------------------

    def provide(self, name: str, value: Any) -> None:
        self._kernel._provide(name, value, owner=self.id)

    def get(self, name: str) -> Any:
        return self._kernel._get(name, owner=self.id)

    def override(self, name: str, default: Any = None) -> Any:
        """Return a bootstrap override (test seam / CLI) or ``default``."""
        overrides = self._kernel._overrides
        if name in overrides:
            return overrides[name]
        return default

    # -- events -------------------------------------------------------

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self._kernel._subscribe(event, handler, owner=self.id)

    def emit(self, event: str, payload: Any = None) -> None:
        self._kernel._emit(event, payload)

    # -- application contributions ------------------------------------

    def add_router(self, prefix: str, router: APIRouter) -> None:
        self._routers.append((prefix, router))

    def add_catchall(self, fn: Callable[[FastAPI], None]) -> None:
        """Register a late app-level hook (for example the web frontend mount)."""
        self._catchalls.append(fn)


class Kernel:
    """Composes a FastAPI application from declarative plugin rows."""

    def __init__(
        self,
        plugin_rows: Iterable[Mapping[str, Any]],
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._overrides: dict[str, Any] = dict(overrides or {})
        self._specs = _order_plugins(
            [PluginSpec.from_mapping(row) for row in plugin_rows]
        )
        self._services: dict[str, Any] = {}
        self._service_owner: dict[str, str] = {}
        self._listeners: dict[str, list[tuple[str, Callable[[Any], None]]]] = (
            defaultdict(list)
        )
        self._applied: list[tuple[PluginSpec, Context]] = []
        self._routers: list[tuple[str, APIRouter]] = []
        self._catchalls: list[Callable[[FastAPI], None]] = []
        self._started = False

    @property
    def plugin_ids(self) -> list[str]:
        return [spec.id for spec in self._specs]

    def _provide(self, name: str, value: Any, *, owner: str) -> None:
        if name in self._services:
            raise DuplicateServiceError(
                f"service {name!r} is already provided by "
                f"plugin {self._service_owner[name]!r}"
            )
        self._services[name] = value
        self._service_owner[name] = owner

    def _get(self, name: str, *, owner: str) -> Any:
        try:
            return self._services[name]
        except KeyError as exc:
            raise MissingServiceError(
                f"service {name!r} requested by plugin {owner!r} is not provided"
            ) from exc

    def _subscribe(
        self,
        event: str,
        handler: Callable[[Any], None],
        *,
        owner: str,
    ) -> None:
        self._listeners[event].append((owner, handler))

    def _emit(self, event: str, payload: Any = None) -> None:
        for owner, handler in self._listeners.get(event, ()):
            handler(payload)

    # -- lifecycle ----------------------------------------------------

    def apply(self) -> None:
        """Run every plugin's ``apply(ctx)`` in dependency order."""
        for spec in self._specs:
            module = spec.load()
            apply_fn = getattr(module, "apply", None)
            if apply_fn is None:
                raise PluginError(
                    f"plugin {spec.id!r} module {spec.module!r} "
                    "must define apply(ctx)"
                )
            ctx = Context(self, spec)
            apply_fn(ctx)
            self._applied.append((spec, ctx))
            self._routers.extend(ctx._routers)
            self._catchalls.extend(ctx._catchalls)

    def start(self) -> None:
        """Begin background work in apply order (FastAPI startup handler)."""
        total_start = time.perf_counter()
        for spec, ctx in self._applied:
            start_fn = getattr(spec.load(), "start", None)
            if start_fn is None:
                continue
            step_start = time.perf_counter()
            start_fn(ctx)
            elapsed = time.perf_counter() - step_start
            if elapsed > 0.5:
                _LOGGER.info(
                    "plugin %s start() took %.1fs", spec.id, elapsed
                )
        _LOGGER.info(
            "kernel startup complete in %.1fs",
            time.perf_counter() - total_start,
        )
        self._started = True

    def stop(self) -> None:
        """Tear down background work in reverse order (FastAPI shutdown)."""
        for spec, ctx in reversed(self._applied):
            stop_fn = getattr(spec.load(), "stop", None)
            if stop_fn is not None:
                stop_fn(ctx)
        self._started = False

    # -- application assembly -----------------------------------------

    def build_app(self, *, title: str, version: str) -> FastAPI:
        """Assemble the FastAPI app from every plugin contribution."""
        from ..api.conversations import install_api_error_handlers
        from ..security import install_local_security

        app = FastAPI(title=title, version=version)
        install_local_security(app)
        install_api_error_handlers(app)
        for prefix, router in self._routers:
            app.include_router(router, prefix=prefix)
        for catchall in self._catchalls:
            catchall(app)
        app.router.add_event_handler("startup", self.start)
        app.router.add_event_handler("shutdown", self.stop)
        return app
