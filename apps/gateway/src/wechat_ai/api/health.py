from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal, Mapping

from fastapi import APIRouter
from pydantic import BaseModel


SourceStatus = Literal["ready", "missing", "unsupported"]
LibraryStatus = Literal["empty", "ready"]
LmStatus = Literal["ready", "offline"]
SourceProbe = Callable[[], SourceStatus]
LibraryProbe = Callable[[], LibraryStatus]
LmProbe = Callable[[], LmStatus]
SubsystemStatus = Literal["ready", "degraded", "offline", "disabled", "indexing"]
OperationalProbe = Callable[[], Mapping[str, object]]

# Hard cap for the operational probe (DB-heavy).  Fast probes (source /
# library / lm) run inline and have their own short timeouts; the operational
# probe is the one that can hang under write-lock contention or a slow
# correlated subquery.  4 s keeps the total /api/health well under 10 s.
_OPERATIONAL_TIMEOUT_SECONDS = 4.0

_TIMEOUT_DETAIL = "operational probe timeout"


def _timeout_operational() -> dict[str, object]:
    return {
        "tasks": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
        "retrieval": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
        "media": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
        "deterministicGraph": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
        "semanticGraph": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
        "defaultModel": {"status": "offline"},
        "observability": {"status": "degraded", "detail": _TIMEOUT_DETAIL},
    }


class SubsystemHealth(BaseModel):
    status: SubsystemStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    app: Literal["ready"] = "ready"
    source: SourceStatus
    library: LibraryStatus
    lm: LmStatus
    tasks: SubsystemHealth | None = None
    retrieval: SubsystemHealth | None = None
    media: SubsystemHealth | None = None
    deterministicGraph: SubsystemHealth | None = None
    semanticGraph: SubsystemHealth | None = None
    defaultModel: SubsystemHealth | None = None
    observability: SubsystemHealth | None = None


def create_health_router(
    *,
    source_probe: SourceProbe,
    library_probe: LibraryProbe,
    lm_probe: LmProbe,
    operational_probe: OperationalProbe | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/health",
        response_model=HealthResponse,
        response_model_exclude_none=True,
    )
    async def health() -> HealthResponse:
        # Fast, self-protecting probes run inline.
        source = source_probe()
        library = library_probe()
        lm = lm_probe()

        operational: Mapping[str, object] = {}
        if operational_probe is not None:
            try:
                operational = await asyncio.wait_for(
                    asyncio.to_thread(operational_probe),
                    timeout=_OPERATIONAL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                operational = _timeout_operational()

        return HealthResponse(
            source=source,
            library=library,
            lm=lm,
            **dict(operational),
        )

    return router
