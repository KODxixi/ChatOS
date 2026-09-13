from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wechat_ai.database import initialize_database
from wechat_ai.services.graph_service import get_graph, project_graph
from wechat_ai.services.knowledge_projection_service import project_knowledge


def _seed(path: Path) -> None:
    with closing(initialize_database(path)) as connection:
        connection.executemany(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind,
                last_message_at, message_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "alpha", "Alpha", "direct", 2_000, 1),
                ("team", "team", "Team", "group", 3_000, 2),
            ),
        )
        connection.executemany(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "alpha:1", "Alice", 2_000, "text", "alpha note"),
                ("team", "team:1", "Bob", 3_000, "text", "launch plan"),
                (
                    "team",
                    "team:2",
                    "Carol",
                    2_500,
                    "text",
                    "Spec https://docs.example.com/spec",
                ),
            ),
        )
        project_knowledge(connection)
        project_graph(connection)
        connection.commit()


def _client(path: Path) -> TestClient:
    from wechat_ai.api.graph import create_graph_router
    from wechat_ai.api.knowledge import create_knowledge_router
    from wechat_ai.api.sources import create_sources_router

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    app = FastAPI()
    app.include_router(create_sources_router(connect), prefix="/api")
    app.include_router(create_knowledge_router(connect), prefix="/api")
    app.include_router(create_graph_router(connect), prefix="/api")
    return TestClient(app)


def _add_synthetic_graph_edges(path: Path, *, count: int = 220) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executemany(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_id, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "team",
                    f"team:synthetic:{index}",
                    f"synthetic-sender-{index}",
                    f"Synthetic Sender {index}",
                    4_000 + index,
                    "text",
                    f"synthetic graph fact {index}",
                )
                for index in range(count)
            ),
        )
        project_knowledge(connection)
        project_graph(connection)
        connection.commit()


def test_sources_spaces_items_and_graph_are_global_without_current_chat_scope(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    client = _client(database_path)

    sources = client.get("/api/sources")
    facets = client.get("/api/knowledge/facets")
    spaces = client.get("/api/knowledge/spaces")
    group_spaces = client.get("/api/knowledge/spaces", params={"kind": "group"})
    items = client.get("/api/knowledge/items")
    searched = client.get("/api/knowledge/items", params={"q": "launch"})
    graph = client.get("/api/graph")

    assert sources.status_code == 200
    assert sources.json() == {
        "items": [
            {
                "id": "wechat",
                "displayName": "\u5fae\u4fe1",
                "connectorKind": "wechat",
                "accounts": [
                    {
                        "id": "wechat:default",
                        "externalAccountId": "default",
                        "displayName": "\u5fae\u4fe1",
                        "status": "ready",
                        "projectionStatus": "ready",
                        "semanticGraphStatus": "skipped",
                        "semanticGraphErrorCode": None,
                        "semanticGraphUpdatedAt": None,
                        "conversationCount": 2,
                        "messageCount": 3,
                    }
                ],
            }
        ]
    }
    assert spaces.status_code == 200
    assert facets.status_code == 200
    assert facets.json() == {
        "sourceApps": [
            {"id": "wechat", "displayName": "\u5fae\u4fe1", "count": 3}
        ],
        "sourceAccounts": [
            {"id": "wechat:default", "sourceAppId": "wechat", "displayName": "\u5fae\u4fe1", "count": 3}
        ],
        "conversationKinds": [
            {"value": "group", "count": 2},
            {"value": "direct", "count": 1},
        ],
        "contentKinds": [
            {"value": "text", "count": 2},
            {"value": "link", "count": 1},
        ],
    }
    assert {item["conversationId"] for item in spaces.json()["items"]} == {
        "alpha",
        "team",
    }
    assert [item["conversationId"] for item in group_spaces.json()["items"]] == [
        "team"
    ]
    assert items.status_code == 200
    assert {item["conversationId"] for item in items.json()["items"]} == {
        "alpha",
        "team",
    }
    assert searched.status_code == 200
    assert [item["text"] for item in searched.json()["items"]] == ["launch plan"]
    assert graph.status_code == 200
    assert any(node["kind"] == "link_domain" for node in graph.json()["nodes"])
    assert all(edge["evidence"] for edge in graph.json()["edges"])

    edge = graph.json()["edges"][0]
    evidence = client.get(f"/api/graph/edges/{edge['id']}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["edgeId"] == edge["id"]
    assert evidence.json()["evidenceCount"] >= 1
    assert evidence.json()["items"]
    assert {
        "conversationId",
        "messageId",
        "messagePartId",
        "sentAt",
        "senderName",
        "snippet",
    } <= evidence.json()["items"][0]["citation"].keys()

    missing_edge = client.get("/api/graph/edges/missing/evidence")
    assert missing_edge.status_code == 404
    assert missing_edge.json()["error"]["code"] == "GRAPH_EDGE_NOT_FOUND"


def test_knowledge_filters_are_explicit_and_empty_query_is_validation_error(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    client = _client(database_path)

    direct = client.get(
        "/api/knowledge/items",
        params={"conversationId": "alpha"},
    )
    missing = client.get(
        "/api/knowledge/items",
        params={"sourceAccountId": "missing"},
    )
    empty_query = client.get("/api/knowledge/items", params={"q": ""})

    assert [item["conversationId"] for item in direct.json()["items"]] == [
        "alpha"
    ]
    assert missing.json() == {"items": [], "nextCursor": None}
    assert empty_query.status_code == 422


def test_graph_conversation_scope_does_not_expand_through_shared_nodes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    client = _client(database_path)

    scoped = client.get(
        "/api/graph",
        params={"conversationId": "alpha", "evidenceLimit": 1},
    )
    global_graph = client.get("/api/graph", params={"evidenceLimit": 1})

    assert scoped.status_code == 200
    assert scoped.json()["edges"]
    assert {
        evidence["citation"]["conversationId"]
        for edge in scoped.json()["edges"]
        for evidence in edge["evidence"]
    } == {"alpha"}
    assert {
        node["traceId"]
        for node in scoped.json()["nodes"]
        if node["kind"] == "conversation"
    } == {"alpha"}

    assert global_graph.status_code == 200
    assert {
        node["traceId"]
        for node in global_graph.json()["nodes"]
        if node["kind"] == "conversation"
    } == {"alpha", "team"}


def test_graph_global_default_is_bounded_and_stably_ordered(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    _add_synthetic_graph_edges(database_path)
    client = _client(database_path)

    first = client.get("/api/graph", params={"evidenceLimit": 1})
    repeated = client.get("/api/graph", params={"evidenceLimit": 1})
    limited = client.get(
        "/api/graph",
        params={"evidenceLimit": 1, "limit": 3},
    )

    assert first.status_code == 200
    assert 0 < len(first.json()["nodes"]) <= 80
    assert len(first.json()["edges"]) <= 80
    assert [
        (-edge["weight"], edge["id"]) for edge in first.json()["edges"]
    ] == sorted(
        (-edge["weight"], edge["id"]) for edge in first.json()["edges"]
    )
    assert [edge["id"] for edge in repeated.json()["edges"]] == [
        edge["id"] for edge in first.json()["edges"]
    ]
    assert len(limited.json()["nodes"]) <= 3
    assert [edge["id"] for edge in limited.json()["edges"]] == [
        edge["id"] for edge in first.json()["edges"][:2]
    ]


def test_graph_preview_queries_do_not_scale_with_returned_edge_count(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    _add_synthetic_graph_edges(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row

        def select_count(node_limit: int) -> int:
            statements: list[str] = []
            connection.set_trace_callback(statements.append)
            try:
                get_graph(
                    connection,
                    evidence_limit=1,
                        limit=node_limit,
                )
            finally:
                connection.set_trace_callback(None)
            return sum(
                statement.lstrip().upper().startswith(("SELECT", "WITH"))
                for statement in statements
            )

        minimum_graph_queries = select_count(2)
        many_node_queries = select_count(100)

    assert many_node_queries <= minimum_graph_queries + 1


def test_graph_evidence_preview_uses_fact_primary_keys_and_rejects_bad_ids(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        edge_id = str(
            connection.execute(
                """
                SELECT edge_id
                FROM graph_edge_evidence
                WHERE evidence_kind = 'message'
                ORDER BY edge_id
                LIMIT 1
                """
            ).fetchone()[0]
        )
        message_id = str(
            connection.execute(
                """
                SELECT evidence_id
                FROM graph_edge_evidence
                WHERE edge_id = ? AND evidence_kind = 'message'
                ORDER BY evidence_id
                LIMIT 1
                """,
                (edge_id,),
            ).fetchone()[0]
        )
        connection.executemany(
            """
            INSERT INTO graph_edge_evidence(
                edge_id, evidence_kind, evidence_id
            ) VALUES (?, 'message', ?)
            """,
            (
                (edge_id, f"{message_id}junk"),
                (edge_id, f"0{message_id}"),
            ),
        )
        connection.commit()

        plans: list[str] = []

        class ExplainingConnection:
            def execute(
                self,
                statement: str,
                parameters: tuple[object, ...] = (),
            ) -> sqlite3.Cursor:
                if "WITH evidence_rows AS" in statement:
                    plans.extend(
                        str(row[3])
                        for row in connection.execute(
                            f"EXPLAIN QUERY PLAN {statement}",
                            parameters,
                        )
                    )
                return connection.execute(statement, parameters)

        graph = get_graph(
            ExplainingConnection(),  # type: ignore[arg-type]
            evidence_limit=20,
        )

    assert graph["edges"]
    assert any(
        "SEARCH messages USING INTEGER PRIMARY KEY" in detail
        for detail in plans
    )
    assert any(
        "SEARCH message_parts USING INTEGER PRIMARY KEY" in detail
        for detail in plans
    )
    assert not any(detail == "SCAN messages" for detail in plans)
    assert not any(detail == "SCAN message_parts" for detail in plans)
    assert all(
        evidence["id"] not in {f"{message_id}junk", f"0{message_id}"}
        for edge in graph["edges"]
        for evidence in edge["evidence"]
    )


def test_default_knowledge_browsing_keeps_unresolved_media_and_paused_facts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('team', 'team:image', 'Bob', 3500, 'image', '[image]')
            """
        )
        connection.execute(
            "UPDATE conversations SET is_indexing_enabled = 0 WHERE id = 'alpha'"
        )
        project_knowledge(connection)
        project_graph(connection)
        connection.commit()
    client = _client(database_path)

    items = client.get("/api/knowledge/items").json()["items"]
    paused_search = client.get(
        "/api/knowledge/items",
        params={"q": "alpha"},
    ).json()["items"]

    assert any(
        item["conversationId"] == "team"
        and item["kind"] == "image"
        and item["status"] == "unresolved"
        and item["chunkId"] is None
        for item in items
    )
    assert any(
        item["conversationId"] == "alpha"
        and item["text"] == "alpha note"
        and item["chunkId"] is None
        for item in items
    )
    assert paused_search == []


def test_default_knowledge_browsing_excludes_unsupported_facts_and_counts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (
                'team', 'team:unsupported', 'Bob',
                3500, 'unsupported_999', '[不支持的消息类型: 999]'
            )
            """
        )
        project_knowledge(connection)
        connection.commit()
    client = _client(database_path)

    items = client.get("/api/knowledge/items").json()["items"]
    facets = client.get("/api/knowledge/facets").json()
    team_space = next(
        item
        for item in client.get("/api/knowledge/spaces").json()["items"]
        if item["conversationId"] == "team"
    )

    assert {item["kind"] for item in items} == {"text", "link"}
    assert all("999" not in item["text"] for item in items)
    assert facets["contentKinds"] == [
        {"value": "text", "count": 2},
        {"value": "link", "count": 1},
    ]
    assert facets["sourceApps"][0]["count"] == 3
    assert facets["sourceAccounts"][0]["count"] == 3
    assert sum(item["count"] for item in facets["conversationKinds"]) == 3
    assert team_space["partCount"] == 2
    assert team_space["assetCount"] == 0
