from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client(streamer) -> TestClient:
    from wechat_ai.api.ask import create_ask_router

    app = FastAPI()
    app.include_router(
        create_ask_router(answer_streamer=streamer),
        prefix="/api",
    )
    return TestClient(app)


def test_ask_endpoint_maps_request_and_streams_ndjson() -> None:
    from wechat_ai.services.answer_service import AnswerQuery

    observed: list[AnswerQuery] = []

    def stream(query: AnswerQuery) -> Iterator[dict[str, object]]:
        observed.append(query)
        yield {"type": "delta", "text": "Synthetic answer"}
        yield {"type": "citations", "items": []}
        yield {"type": "done"}

    response = _make_client(stream).post(
        "/api/ask/stream",
        json={
            "question": "What changed?",
            "conversationIds": ["synthetic-room"],
            "senderName": "Synthetic Sender",
            "sentAfter": 1_700_000_000_000,
            "sentBefore": 1_700_100_000_000,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/x-ndjson"
    )
    assert observed == [
        AnswerQuery(
            question="What changed?",
            conversation_ids=("synthetic-room",),
            sender_name="Synthetic Sender",
            sent_after=1_700_000_000_000,
            sent_before=1_700_100_000_000,
        )
    ]
    assert [json.loads(line) for line in response.text.splitlines()] == [
        {"type": "delta", "text": "Synthetic answer"},
        {"type": "citations", "items": []},
        {"type": "done"},
    ]


def test_ask_endpoint_rejects_empty_question_before_streaming() -> None:
    called = False

    def stream(_query) -> Iterator[dict[str, object]]:
        nonlocal called
        called = True
        yield {"type": "done"}

    response = _make_client(stream).post(
        "/api/ask/stream",
        json={"question": "   "},
    )

    assert response.status_code == 422
    assert called is False


def test_ask_endpoint_closes_answer_iterator_on_asgi_disconnect() -> None:
    class CloseTrackingStream:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def __iter__(self) -> CloseTrackingStream:
            return self

        def __next__(self) -> dict[str, object]:
            return {"type": "delta", "text": "Synthetic answer"}

        def close(self) -> None:
            self.closed.set()

    stream = CloseTrackingStream()

    async def invoke_until_disconnect() -> None:
        from wechat_ai.api.ask import create_ask_router

        app = FastAPI()
        app.include_router(
            create_ask_router(answer_streamer=lambda _query: stream),
            prefix="/api",
        )
        request_body = json.dumps(
            {"question": "What changed?"}
        ).encode("utf-8")
        first_body = asyncio.Event()
        request_received = False

        async def receive() -> dict[str, Any]:
            nonlocal request_received
            if not request_received:
                request_received = True
                return {
                    "type": "http.request",
                    "body": request_body,
                    "more_body": False,
                }
            await first_body.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if (
                message["type"] == "http.response.body"
                and message.get("body")
            ):
                first_body.set()

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ask/stream",
            "raw_path": b"/api/ask/stream",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"127.0.0.1"),
                (b"content-type", b"application/json"),
                (
                    b"content-length",
                    str(len(request_body)).encode("ascii"),
                ),
            ],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 80),
        }

        await asyncio.wait_for(app(scope, receive, send), timeout=1)
        assert stream.closed.wait(timeout=0.1)

    asyncio.run(invoke_until_disconnect())


def test_create_app_registers_ask_route_without_requiring_online_model(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/api/health")
    response = client.post(
        "/api/ask/stream",
        json={"question": "synthetic missing evidence"},
    )

    assert response.status_code == 200
    assert [json.loads(line) for line in response.text.splitlines()] == [
        {"type": "citations", "items": []},
        {"type": "done"},
    ]


def test_create_app_injects_reranker_into_ask_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from wechat_ai import app as app_module
    from wechat_ai.config import Settings
    from wechat_ai.database import initialize_database

    database_path = tmp_path / "data" / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind
            ) VALUES (?, ?, ?, ?)
            """,
            ("alpha", "source-alpha", "Alpha Room", "group"),
        )
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("alpha", "message-1", "Alice", 2_000, "text", "launch scope"),
        )
        connection.commit()
    finally:
        connection.close()

    reranker_urls: list[str] = []
    reranker_calls: list[tuple[str, list[str]]] = []
    embedding_urls: list[str] = []
    embedding_calls: list[tuple[str, list[str]]] = []
    answer_scores: list[float | None] = []

    class Embedding:
        def similarities(self, query: str, documents: list[str]) -> list[float]:
            embedding_calls.append((query, list(documents)))
            return [1.0]

    def create_embedding(base_url: str, *, model: str, dimensions: int) -> Embedding:
        assert model == "text-embedding-qwen3-embedding-0.6b"
        assert dimensions == 1024
        embedding_urls.append(base_url)
        return Embedding()

    class Reranker:
        def score_many(self, query: str, documents: list[str]) -> list[float]:
            reranker_calls.append((query, list(documents)))
            return [0.91]

    def create_reranker(base_url: str) -> Reranker:
        reranker_urls.append(base_url)
        return Reranker()

    class AnswerClient:
        def __init__(self, _base_url: str) -> None:
            pass

        def stream_answer(self, _question: str, contexts) -> Iterator[str]:
            answer_scores.extend(item.relevance_score for item in contexts)
            yield "Synthetic answer"

    monkeypatch.setattr(app_module, "EmbeddingClient", create_embedding)
    monkeypatch.setattr(app_module, "RerankerClient", create_reranker)
    monkeypatch.setattr(app_module, "LmStudioClient", AnswerClient)
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=database_path,
        output_dir=tmp_path / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
        retrieval_base_url="http://127.0.0.1:9123/v1",
    )
    application = app_module.create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    with TestClient(application, base_url="http://127.0.0.1") as client:
        client.get("/api/health")
        response = client.post(
            "/api/ask/stream",
            json={"question": "launch"},
        )

    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.status_code == 200
    assert embedding_urls == ["http://127.0.0.1:9123/v1"]
    assert embedding_calls == [
        ("launch", ["发送者: Alice\n内容: launch scope"])
    ]
    assert reranker_urls == ["http://127.0.0.1:9123/v1"]
    assert reranker_calls == [
        ("launch", ["发送者: Alice\n内容: launch scope"])
    ]
    assert answer_scores == []
    assert events[-2]["items"][0]["score"] == 0.91
    assert events[-1] == {"type": "done"}
