from __future__ import annotations

import json
from urllib.request import Request

import pytest


@pytest.fixture(autouse=True)
def embedding_defaults(monkeypatch):
    from wechat_ai.services import embedding_service as es
    monkeypatch.setattr(es, "MODEL", "text-embedding-qwen3-embedding-0.6b")
    monkeypatch.setattr(es, "_DIM", 1024)


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
                {"index": 0, "embedding": [1.0, 0.0] + [0.0] * 1022},
                {"index": 1, "embedding": [1.0, 0.0] + [0.0] * 1022},
                {"index": 2, "embedding": [0.0, 1.0] + [0.0] * 1022},
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
        "model": "text-embedding-qwen3-embedding-0.6b",
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
                    {"index": 0, "embedding": [1.0, 0.0] + [0.0] * 1022},
                    {"index": 1, "embedding": [1.0, 0.0] + [0.0] * 1022},
                ]
            }
        )

    EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    ).similarities("query", ["document"])

    body = json.loads(requests[0].data or b"{}")
    assert body["model"] == "text-embedding-qwen3-embedding-0.6b"


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


def test_existing_vector_space_blocks_query_and_write(monkeypatch):
    import sqlite3
    from wechat_ai.services import embedding_service as es
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE message_embeddings (status TEXT, model_version TEXT, embedding BLOB)")
        for model, dimension in [("old-model", 1024), (es.MODEL, 4096)]:
            conn.execute("DELETE FROM message_embeddings")
            conn.execute("INSERT INTO message_embeddings VALUES ('indexed',?,?)", (model, bytes(dimension * 4)))
            with pytest.raises(es.EmbeddingUnavailable, match="shadow"):
                es.search(conn, "query")
            with pytest.raises(es.EmbeddingUnavailable, match="shadow"):
                es.embed_and_store(conn, 1, "conversation", "message text")
            assert conn.execute("SELECT length(embedding) FROM message_embeddings").fetchone()[0] == dimension * 4


def test_registry_refresh_preserves_active_configuration(tmp_path):
    from wechat_ai.services.knowledge_archiver import _write_registry
    registry = tmp_path / "local-model-registry.yaml"
    registry.write_text("active-old-model-and-schema", encoding="utf-8")
    _write_registry(tmp_path)
    assert registry.read_text(encoding="utf-8") == "active-old-model-and-schema"


def test_new_registry_uses_explicit_endpoint_without_reading_host_settings(tmp_path, monkeypatch):
    from wechat_ai import config
    from wechat_ai.services.knowledge_archiver import _write_registry
    def forbidden(*args, **kwargs):
        raise AssertionError("host settings must not be loaded during archival")
    monkeypatch.setattr(config, "load_settings", forbidden)
    _write_registry(tmp_path, retrieval_base_url="http://127.0.0.1:9999/v1")
    value = (tmp_path / "local-model-registry.yaml").read_text(encoding="utf-8")
    assert "http://127.0.0.1:9999" in value
    assert "dimensions: 1024" in value
