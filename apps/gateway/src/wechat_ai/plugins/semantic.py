"""Semantic plugin: semantic graph enrichment and its job handler."""

from __future__ import annotations

from contextlib import closing


def apply(ctx) -> None:
    from ..services.job_worker import JobResult

    semantic_enricher = ctx.override("semantic.enricher")
    if semantic_enricher is None:
        semantic_enricher = _default_enricher(ctx)
    ctx.provide("semantic.enricher", semantic_enricher)

    def run_semantic_job(_job: object, _context: object) -> JobResult:
        result = semantic_enricher()
        if getattr(result, "degraded_accounts", 0):
            return JobResult(
                status="degraded",
                error_code="SEMANTIC_GRAPH_DEGRADED",
            )
        return JobResult()

    ctx.get("jobs.registry")["semantic_graph"] = run_semantic_job


def _default_enricher(ctx) -> object:
    from ..services.semantic_graph_service import SemanticGraphService

    semantic_graph_service = SemanticGraphService(
        model_profile_service=ctx.get("models.service"),
        batch_retries=3,
    )

    def run_semantic_enrichment() -> object:
        with closing(ctx.get("db.connection_factory")()) as semantic_connection:
            return semantic_graph_service.enrich(semantic_connection)

    return run_semantic_enrichment
