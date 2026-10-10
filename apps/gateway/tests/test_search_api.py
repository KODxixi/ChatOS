from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _seed_search_library(path: Path) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(path)
    try:
        connection.executemany(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind, last_message_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "source-alpha", "Alpha Room", "direct", 3_000),
                ("beta", "source-beta", "Beta Room", "group", 4_000),
            ),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id,
                source_message_id,
                dedupe_key,
                sender_id,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "alpha",
                    "m-1",
                    "alpha:m-1",
                    "alice",
                    "Alice",
                    1_000,
                    "text",
                    "launch checklist",
                ),
                (
                    "alpha",
                    "m-2",
                    "alpha:m-2",
                    "bob",
                    "Bob",
                    2_000,
                    "text",
                    "launch review",
                ),
                (
                    "alpha",
                    "m-3",
                    "alpha:m-3",
                    "alice",
                    "Alice",
                    3_000,
                    "text",
                    "unrelated note",
                ),
                (
                    "beta",
                    "m-4",
                    "beta:m-4",
                    "alice",
                    "Alice",
                    4_000,
                    "text",
                    "launch beta plan",
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _add_chinese_search_messages(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id,
                source_message_id,
                dedupe_key,
                sender_id,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "alpha",
                    "zh-1",
                    "alpha:zh-1",
                    "alice",
                    "Alice",
                    5_000,
                    "text",
                    "首版会话支持引用提问",
                ),
                (
                    "alpha",
                    "zh-2",
                    "alpha:zh-2",
                    "bob",
                    "Bob",
                    6_000,
                    "text",
                    "首版计划没有引用功能",
                ),
                (
                    "alpha",
                    "zh-3",
                    "alpha:zh-3",
                    "alice",
                    "Alice",
                    7_000,
                    "text",
                    "通配验证 A%_完成 \\归档",
                ),
                (
                    "alpha",
                    "zh-4",
                    "alpha:zh-4",
                    "alice",
                    "Alice",
                    8_000,
                    "text",
                    "通配验证 A X 完成 X 归档",
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _make_client(
    database_path: Path,
) -> tuple[TestClient, list[sqlite3.Connection]]:
    from wechat_ai.api.conversations import install_api_error_handlers
    from wechat_ai.api.search import create_search_router

    opened_connections: list[sqlite3.Connection] = []

    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        opened_connections.append(connection)
        return connection

    app = FastAPI()
    install_api_error_handlers(app)
    app.include_router(create_search_router(open_connection), prefix="/api")
    return TestClient(app), opened_connections


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("首版", [6, 5]),
        ("引用提问", [5]),
    ],
)
def test_chinese_keywords_match_message_content(
    tmp_path: Path,
    query: str,
    expected_ids: list[int],
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    _add_chinese_search_messages(database_path)
    client, _connections = _make_client(database_path)

    response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected_ids


def test_chinese_multiword_search_requires_every_term(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    _add_chinese_search_messages(database_path)
    client, _connections = _make_client(database_path)

    response = client.get("/api/search", params={"q": "首版 提问"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [5]


def test_chinese_like_search_treats_wildcards_as_literals(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    _add_chinese_search_messages(database_path)
    client, _connections = _make_client(database_path)

    response = client.get(
        "/api/search",
        params={"q": "通配验证 A%_完成 \\归档"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [7]


def test_search_returns_message_fields_and_conversation_title(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    client, opened_connections = _make_client(database_path)

    response = client.get("/api/search", params={"q": "launch"})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": 4,
                "conversationId": "beta",
                "sourceMessageId": "m-4",
                "senderId": "alice",
                "senderName": "Alice",
                "sentAt": 4_000,
                "type": "text",
                "content": "launch beta plan",
                "conversationTitle": "Beta Room",
            },
            {
                "id": 2,
                "conversationId": "alpha",
                "sourceMessageId": "m-2",
                "senderId": "bob",
                "senderName": "Bob",
                "sentAt": 2_000,
                "type": "text",
                "content": "launch review",
                "conversationTitle": "Alpha Room",
            },
            {
                "id": 1,
                "conversationId": "alpha",
                "sourceMessageId": "m-1",
                "senderId": "alice",
                "senderName": "Alice",
                "sentAt": 1_000,
                "type": "text",
                "content": "launch checklist",
                "conversationTitle": "Alpha Room",
            },
        ],
        "nextCursor": None,
    }
    assert "launch beta plan" not in caplog.text
    assert len(opened_connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened_connections[0].execute("SELECT 1")


@pytest.mark.parametrize(
    ("params", "expected_ids"),
    [
        ({"conversationId": "alpha"}, [2, 1]),
        ({"senderName": "Alice"}, [4, 1]),
        ({"sentAtFrom": 1_500, "sentAtTo": 4_500}, [4, 2]),
    ],
)
def test_search_applies_optional_filters(
    tmp_path: Path,
    params: dict[str, object],
    expected_ids: list[int],
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    client, _connections = _make_client(database_path)

    response = client.get("/api/search", params={"q": "launch", **params})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected_ids


def test_search_service_filters_multiple_conversation_ids(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.search_service import search_messages

    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind, last_message_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("gamma", "source-gamma", "Gamma Room", "group", 5_000),
        )
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, dedupe_key,
                sender_id, sender_name, sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gamma",
                "m-5",
                "gamma:m-5",
                "carol",
                "Carol",
                5_000,
                "text",
                "launch gamma plan",
            ),
        )
        connection.commit()

        page = search_messages(
            connection,
            query="launch",
            conversation_id=None,
            conversation_ids=("alpha", "beta"),
            sender_name=None,
            sent_at_from=None,
            sent_at_to=None,
            limit=50,
            cursor=None,
        )
    finally:
        connection.close()

    assert [item["conversationId"] for item in page["items"]] == [
        "beta",
        "alpha",
        "alpha",
    ]


def test_search_cursor_paginates_without_duplicates_or_gaps(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    database_path = tmp_path / "library.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind, message_count
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("bulk", "source-bulk", "Bulk", "group", 121),
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id,
                dedupe_key,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "bulk",
                    f"bulk-{index:03}",
                    "Synthetic",
                    50_000 - (index // 5),
                    "text",
                    f"needle result {index:03}",
                )
                for index in range(121)
            ),
        )
        connection.commit()
        expected = [
            row[0]
            for row in connection.execute(
                """
                SELECT messages.id
                FROM message_fts
                JOIN messages ON messages.id = message_fts.rowid
                WHERE message_fts MATCH ?
                ORDER BY messages.sent_at DESC, messages.id DESC
                """,
                ('"needle"',),
            )
        ]
    finally:
        connection.close()

    client, _connections = _make_client(database_path)
    seen: list[int] = []
    cursor: str | None = None
    while True:
        response = client.get(
            "/api/search",
            params={
                "q": "needle",
                "limit": 19,
                **({"cursor": cursor} if cursor else {}),
            },
        )
        assert response.status_code == 200
        payload = response.json()
        seen.extend(item["id"] for item in payload["items"])
        cursor = payload["nextCursor"]
        if cursor is None:
            break
        assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor)

    assert seen == expected
    assert len(seen) == len(set(seen)) == 121


def test_punctuation_and_validation_errors_do_not_expose_sql(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    client, _connections = _make_client(database_path)

    punctuation = client.get("/api/search", params={"q": "!!!"})
    invalid_range = client.get(
        "/api/search",
        params={"q": "launch", "sentAtFrom": 5_000, "sentAtTo": 1_000},
    )
    invalid_limit = client.get(
        "/api/search",
        params={"q": "launch", "limit": 201},
    )
    missing_query = client.get("/api/search")

    assert punctuation.status_code == 400
    assert punctuation.json() == {
        "error": {
            "code": "INVALID_SEARCH_QUERY",
            "message": "搜索条件无效。",
            "retryable": False,
            "details": {},
        }
    }
    assert "sqlite" not in punctuation.text.lower()
    assert "syntax" not in punctuation.text.lower()
    for response in (invalid_range, invalid_limit, missing_query):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_search_rejects_cursor_from_another_resource(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_search_library(database_path)
    client, _connections = _make_client(database_path)
    conversations_cursor = client.get(
        "/api/conversations",
        params={"limit": 1},
    )
    assert conversations_cursor.status_code == 404

    from wechat_ai.services.conversation_service import encode_cursor

    cursor = encode_cursor("message", 4_000, 4)
    response = client.get(
        "/api/search",
        params={"q": "launch", "cursor": cursor},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_create_app_registers_search_route(tmp_path: Path) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    database_path = tmp_path / "data" / "library.sqlite"
    _seed_search_library(database_path)
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=database_path,
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    response = TestClient(
        app,
        base_url="http://127.0.0.1",
    ).get("/api/search", params={"q": "launch"})

    assert response.status_code == 200
    # Full-app wiring turns on the relevance path (embed_fn provided), so
    # ASCII queries are ranked by FTS5 BM25 rather than sent_at.  BM25
    # favors shorter docs ("launch checklist" / "launch review") over the
    # 3-token "launch beta plan"; exact tie-break is FTS5-internal.  We only
    # assert that all three hits come back, in any BM25 order.
    returned_ids = sorted(item["id"] for item in response.json()["items"])
    assert returned_ids == [1, 2, 4]
