from __future__ import annotations

import json
from urllib.request import Request

import pytest


class FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


def test_embedding_client_batches_query_and_documents_for_cosine_scores() -> None:
    from wechat_ai.services.embedding_service import EmbeddingClient

    response = FakeResponse(
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [1.0, 0.0]},
                {"index": 2, "embedding": [0.0, 1.0]},
            ]
        }
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 3.0
        return response

    client = EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
        timeout=3.0,
    )

    scores = client.similarities("query", ["same", "different"])

    assert scores == pytest.approx([1.0, 0.0])
    assert response.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:9123/v1/embeddings"
    body = json.loads(request.data or b"{}")
    assert body == {
        "model": "text-embedding-qwen3-embedding-8b",
        "input": ["query", "same", "different"],
    }


def test_embedding_client_defaults_to_the_llama_swap_embedding_model() -> None:
    from wechat_ai.services.embedding_service import EmbeddingClient

    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        return FakeResponse(
            {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [1.0, 0.0]},
                ]
            }
        )

    EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    ).similarities("query", ["document"])

    body = json.loads(requests[0].data or b"{}")
    assert body["model"] == "text-embedding-qwen3-embedding-8b"


def test_embedding_client_rejects_mismatched_dimensions() -> None:
    from wechat_ai.services.embedding_service import (
        EmbeddingClient,
        EmbeddingUnavailable,
    )

    response = FakeResponse(
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [1.0]},
            ]
        }
    )
    client = EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: response,
    )

    with pytest.raises(EmbeddingUnavailable):
        client.similarities("query", ["document"])
