from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request

import pytest


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


def test_reranker_posts_single_rerank_call_and_maps_scores_by_index() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    requests: list[Request] = []
    response = FakeResponse(
        {
            "model": "text-reranker-8b",
            "results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.3},
                {"index": 1, "relevance_score": 0.1},
            ],
        }
    )

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 2.0
        return response

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
        timeout=2.0,
    )

    scores = client.score_many("蔡敏昨天找我干嘛", ["d0", "d1", "d2"])

    assert scores == pytest.approx([0.3, 0.1, 0.9])
    assert response.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:9123/v1/rerank"
    body = json.loads(request.data or b"{}")
    assert body == {
        "model": "text-reranker-8b",
        "query": "蔡敏昨天找我干嘛",
        "documents": ["d0", "d1", "d2"],
    }


def test_reranker_defaults_to_the_llama_swap_rerank_model() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"results": [{"index": 0, "relevance_score": 0.5}]})

    RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    ).score_many("query", ["document"])

    body = json.loads(requests[0].data or b"{}")
    assert body["model"] == "text-reranker-8b"


def test_reranker_fills_missing_documents_with_none_keeping_order() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: FakeResponse(
            {"results": [{"index": 1, "relevance_score": 0.8}]}
        ),
    )

    scores = client.score_many("query", ["a", "b", "c"])

    assert scores == [None, pytest.approx(0.8), None]


def test_reranker_ignores_out_of_range_indexes_and_invalid_scores() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: FakeResponse(
            {
                "results": [
                    {"index": 0, "relevance_score": 0.7},
                    {"index": 99, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": "not-a-number"},
                    {"index": 2, "relevance_score": float("nan")},
                ]
            }
        ),
    )

    scores = client.score_many("query", ["a", "b", "c"])

    assert scores[0] == pytest.approx(0.7)
    assert scores[1] is None
    assert scores[2] is None


def test_reranker_returns_empty_for_no_documents() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    opened = False

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        nonlocal opened
        opened = True
        return FakeResponse({"results": []})

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    )

    assert client.score_many("query", []) == []
    assert opened is False


def test_reranker_rejects_empty_query() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: FakeResponse({"results": []}),
    )

    with pytest.raises(ValueError):
        client.score_many("   ", ["document"])


def test_reranker_maps_transport_failure_to_unavailable() -> None:
    from wechat_ai.services.reranker_service import (
        RerankerClient,
        RerankerUnavailable,
    )

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        raise URLError("connection refused")

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    )

    with pytest.raises(RerankerUnavailable):
        client.score_many("query", ["document"])


def test_reranker_maps_non_success_status_to_unavailable() -> None:
    from wechat_ai.services.reranker_service import (
        RerankerClient,
        RerankerUnavailable,
    )

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: FakeResponse(
            {"results": []},
            status=500,
        ),
    )

    with pytest.raises(RerankerUnavailable):
        client.score_many("query", ["document"])


def test_reranker_maps_malformed_payload_to_unavailable() -> None:
    from wechat_ai.services.reranker_service import (
        RerankerClient,
        RerankerUnavailable,
    )

    client = RerankerClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: FakeResponse({"foo": "bar"}),
    )

    with pytest.raises(RerankerUnavailable):
        client.score_many("query", ["document"])


def test_reranker_rejects_non_loopback_endpoints() -> None:
    from wechat_ai.services.reranker_service import RerankerClient

    with pytest.raises(ValueError):
        RerankerClient("https://example.com/v1")
