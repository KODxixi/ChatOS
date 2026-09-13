"""Projection plugin: knowledge/graph projection jobs.

Listens for ``import.completed`` and enqueues the projection pipeline
(retrieval, deterministic graph, media, semantic graph) — the same fan-out
that used to be a closure inside ``create_app``.  The job handlers themselves
run the two projection services.
"""

from __future__ import annotations

from contextlib import closing


def apply(ctx) -> None:
    def on_import_completed(_payload: object = None) -> None:
        ctx.get("gchat.service").suggest_project_assignments_from_archive()
        jobs = ctx.get("jobs.service")
        for job_type in (
            "retrieval",
            "deterministic_graph",
            "media",
            "semantic_graph",
        ):
            jobs.create_job(
                job_type,
                trigger_source="import",
                payload={"cause": "import"},
            )
        ctx.get("jobs.notify")()

    ctx.on("import.completed", on_import_completed)

    registry = ctx.get("jobs.registry")

    def run_retrieval_job(_job: object, _context: object) -> object:
        from ..services.job_worker import JobResult
        from ..services.knowledge_projection_service import project_knowledge

        with closing(ctx.get("db.connection_factory")()) as projection_connection:
            project_knowledge(projection_connection)
            # closing() 只关连接、不 commit；project_knowledge 事务所有权归调用方，
            # 不显式 commit 会在 close 时被回滚，导致检索投影结果丢失。
            projection_connection.commit()
        return JobResult()

    def run_graph_job(_job: object, _context: object) -> object:
        from ..services.graph_service import project_graph
        from ..services.job_worker import JobResult

        with closing(ctx.get("db.connection_factory")()) as graph_connection:
            project_graph(graph_connection)
            graph_connection.commit()
        return JobResult()

    registry["retrieval"] = run_retrieval_job
    registry["deterministic_graph"] = run_graph_job
