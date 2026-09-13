"""Answer plugin: two-stage RAG evidence streaming (/api/ask/stream).

Embedding and rerank are served by llama-swap (retrieval backend) per the
local-model architecture; LM Studio only runs dialogue/generation models.
The embedding/reranker client classes are resolved from bootstrap overrides
so tests can keep monkeypatching ``wechat_ai.app.EmbeddingClient`` /
``wechat_ai.app.RerankerClient`` as the historical seam; the defaults talk to
the llama-swap retrieval endpoint.
"""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.ask import create_ask_router
    from ..services.answer_service import AnswerService, create_evidence_retriever

    settings = ctx.get("settings")
    embedding_factory = ctx.override("answer.embedding_factory")
    reranker_factory = ctx.override("answer.reranker_factory")
    if embedding_factory is None:
        from ..services.embedding_service import EmbeddingClient

        embedding_factory = EmbeddingClient
    if reranker_factory is None:
        from ..services.reranker_service import RerankerClient

        reranker_factory = RerankerClient

    answer_service = AnswerService(
        create_evidence_retriever(
            ctx.get("db.connection_factory"),
            embedding=embedding_factory(settings.retrieval_base_url),
            reranker=(
                reranker_factory(settings.retrieval_base_url)
                if settings.rerank_enabled
                else None
            ),
        ),
        provider=None,
    )
    ctx.provide("answer.service", answer_service)
    ctx.add_router(
        "/api",
        create_ask_router(answer_streamer=answer_service.stream_evidence),
    )
