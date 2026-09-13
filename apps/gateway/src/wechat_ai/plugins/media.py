"""Media plugin: media/document enrichment and the media job handler.

The enrichment helpers that used to live in ``app.py`` are module-level
functions here so they stay unit-testable; ``wechat_ai.app`` re-exports them
under their historical names for the test-suite.
"""

from __future__ import annotations

from contextlib import closing

from ..services.document_enrichment_service import DocumentEnrichmentResult
from ..services.job_worker import JobResult
from ..services.media_enrichment_service import (
    MediaEnrichmentResult,
    MediaEnrichmentService,
)


def apply(ctx) -> None:
    from ..services.document_enrichment_service import DocumentEnrichmentService

    settings = ctx.get("settings")
    media_service = MediaEnrichmentService(
        asset_root=settings.asset_dir,
        cache_root=settings.cache_dir,
    )
    document_service = DocumentEnrichmentService(asset_root=settings.asset_dir)
    ctx.provide("media.service", media_service)
    ctx.provide("media.document_service", document_service)

    def run_media_job(_job: object, _context: object) -> JobResult:
        with closing(ctx.get("db.connection_factory")()) as media_connection:
            media_result = media_service.enrich_pending(media_connection)
            document_result = document_service.enrich_pending(media_connection)
        result = merge_enrichment_results(media_result, document_result)
        jobs = ctx.get("jobs.service")
        if media_followup_needed(result):
            jobs.create_job(
                "media",
                trigger_source="media",
                payload=media_followup_payload(getattr(_job, "id", "unknown")),
            )
            ctx.get("jobs.notify")()
        if result.ready:
            jobs.create_job(
                "retrieval",
                trigger_source="media",
                payload={"cause": "media"},
            )
            jobs.create_job(
                "deterministic_graph",
                trigger_source="media",
                payload={"cause": "media"},
            )
            ctx.get("jobs.notify")()
        if result.errors:
            return JobResult(
                status="degraded",
                error_code="MEDIA_PROCESSING_FAILED",
            )
        return JobResult()

    ctx.get("jobs.registry")["media"] = run_media_job


def media_followup_needed(result: MediaEnrichmentResult) -> bool:
    return bool(
        result.has_more
        and (int(result.ready) + int(result.no_text) > 0)
    )


def media_followup_payload(job_id: object) -> dict[str, str]:
    return {
        "cause": "media-followup",
        "after": str(job_id),
    }


def merge_enrichment_results(
    media_result: MediaEnrichmentResult,
    document_result: DocumentEnrichmentResult,
) -> MediaEnrichmentResult:
    return MediaEnrichmentResult(
        ready=media_result.ready + document_result.ready,
        skipped=media_result.skipped + document_result.skipped,
        errors=media_result.errors + document_result.errors,
        attempted=media_result.attempted + document_result.attempted,
        no_text=media_result.no_text + document_result.no_text,
        engine_unavailable=(
            media_result.engine_unavailable + document_result.engine_unavailable
        ),
        has_more=media_result.has_more or document_result.has_more,
    )
