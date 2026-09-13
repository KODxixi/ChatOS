from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client(database_path: Path) -> TestClient:
    from wechat_ai.api.conversations import (
        create_conversations_router,
        install_api_error_handlers,
    )

    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection

    app = FastAPI()
    install_api_error_handlers(app)
    app.include_router(
        create_conversations_router(open_connection),
        prefix="/api",
    )
    return TestClient(app)


def _initialize(path: Path) -> sqlite3.Connection:
    from wechat_ai.database import initialize_database

    return initialize_database(path)


def test_conversation_cursor_paginates_all_rows_without_gaps(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    connection = _initialize(database_path)
    try:
        rows = [
            (
                f"conversation-{index:03}",
                f"source-{index:03}",
                f"Conversation {index:03}",
                "direct",
                10_000 - (index // 3),
            )
            for index in range(123)
        ]
        connection.executemany(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind, last_message_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
        expected = [
            row[0]
            for row in connection.execute(
                """
                SELECT id
                FROM conversations
                ORDER BY COALESCE(last_message_at, -1) DESC, id DESC
                """
            )
        ]
    finally:
        connection.close()

    client = _make_client(database_path)
    first_page = client.get("/api/conversations")
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 50

    seen: list[str] = []
    cursor: str | None = None
    while True:
        response = client.get(
            "/api/conversations",
            params={"limit": 17, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        payload = response.json()
        seen.extend(item["id"] for item in payload["items"])
        cursor = payload["nextCursor"]
        if cursor is None:
            break
        assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor)
        assert "=" not in cursor

    assert seen == expected
    assert len(seen) == len(set(seen)) == 123


def test_message_cursor_paginates_equal_timestamps_without_gaps(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    connection = _initialize(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations (
                id, source_conversation_id, title, kind, message_count
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("timeline", "source-timeline", "Timeline", "group", 233),
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
                    "timeline",
                    f"message-{index:03}",
                    "Synthetic",
                    20_000 - (index // 4),
                    "text",
                    f"synthetic body {index:03}",
                )
                for index in range(233)
            ),
        )
        connection.commit()
        expected = [
            row[0]
            for row in connection.execute(
                """
                SELECT id
                FROM messages
                WHERE conversation_id = ?
                ORDER BY sent_at DESC, id DESC
                """,
                ("timeline",),
            )
        ]
    finally:
        connection.close()

    client = _make_client(database_path)
    max_page = client.get(
        "/api/conversations/timeline/messages",
        params={"limit": 200},
    )
    assert max_page.status_code == 200
    assert len(max_page.json()["items"]) == 200

    seen: list[int] = []
    cursor: str | None = None
    while True:
        response = client.get(
            "/api/conversations/timeline/messages",
            params={"limit": 31, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        payload = response.json()
        seen.extend(item["id"] for item in payload["items"])
        cursor = payload["nextCursor"]
        if cursor is None:
            break
        assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor)

    assert seen == expected
    assert len(seen) == len(set(seen)) == 233

    invalid_cursor = client.get(
        "/api/conversations/timeline/messages",
        params={"cursor": "not+a+base64url+cursor"},
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "INVALID_CURSOR"
