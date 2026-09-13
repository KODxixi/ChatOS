from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Connection:
    row_factory = None

    def close(self) -> None:
        return None


def test_graph_defaults_to_80_and_hides_asset_content_noise(monkeypatch) -> None:
    from wechat_ai.api import graph as graph_api

    calls: list[dict[str, object]] = []

    def fake_graph(_connection, **kwargs):
        calls.append(kwargs)
        return {
            "nodes": [
                {
                    "id": "conversation-1",
                    "kind": "conversation",
                    "label": "Project chat",
                    "traceKind": "conversation",
                    "traceId": "conversation-1",
                },
                {
                    "id": "asset-1",
                    "kind": "asset",
                    "label": "private/internal/path.jpg",
                    "traceKind": "asset",
                    "traceId": "33512",
                },
            ],
            "edges": [
                {
                    "id": "has-asset",
                    "sourceNodeId": "conversation-1",
                    "targetNodeId": "asset-1",
                    "kind": "has_asset",
                    "weight": 1,
                    "evidenceCount": 1,
                    "evidence": [],
                }
            ],
        }

    monkeypatch.setattr(graph_api, "get_graph", fake_graph)
    app = FastAPI()
    app.include_router(
        graph_api.create_graph_router(lambda: _Connection()),
        prefix="/api",
    )
    client = TestClient(app)

    defaulted = client.get("/api/graph")
    unfiltered = client.get("/api/graph?hideNoise=false")

    assert calls[0]["limit"] == 80
    assert [node["kind"] for node in defaulted.json()["nodes"]] == [
        "conversation"
    ]
    assert defaulted.json()["edges"] == []
    assert len(unfiltered.json()["nodes"]) == 2
