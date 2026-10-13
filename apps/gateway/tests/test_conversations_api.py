from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import closing

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _seed_library(path: Path) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(path)
    try:
        connection.executemany(
            """
            INSERT INTO conversations (
                id,
                source_conversation_id,
                title,
                kind,
                last_message_at,
                message_count,
                is_favorite,
                is_archived,
                note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "alpha",
                    "source-alpha",
                    "Alpha",
                    "direct",
                    2_000,
                    2,
                    0,
                    0,
                    "",
                ),
                (
                    "beta",
                    "source-beta",
                    "Beta",
                    "group",
                    3_000,
                    1,
                    1,
                    0,
                    "keep",
                ),
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
                    2_000,
                    "text",
                    "first synthetic message",
                ),
                (
                    "alpha",
                    "m-2",
                    "alpha:m-2",
                    "bob",
                    "Bob",
                    2_000,
                    "text",
                    "second synthetic message",
                ),
                (
                    "beta",
                    "m-3",
                    "beta:m-3",
                    "carol",
                    "Carol",
                    3_000,
                    "text",
                    "group synthetic message",
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _make_client(
    database_path: Path,
    *,
    asset_root: Path | None = None,
) -> tuple[TestClient, list[sqlite3.Connection]]:
    from wechat_ai.api.conversations import (
        create_conversations_router,
        install_api_error_handlers,
    )

    opened_connections: list[sqlite3.Connection] = []

    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        opened_connections.append(connection)
        return connection

    app = FastAPI()
    install_api_error_handlers(app)
    router_options = (
        {"asset_root": asset_root}
        if asset_root is not None
        else {}
    )
    app.include_router(
        create_conversations_router(open_connection, **router_options),
        prefix="/api",
    )
    return TestClient(app), opened_connections


def test_conversation_list_and_message_response_use_camel_case(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    client, opened_connections = _make_client(database_path)

    conversations = client.get("/api/conversations")
    messages = client.get("/api/conversations/alpha/messages")

    assert conversations.status_code == 200
    assert conversations.json() == {
        "items": [
            {
                "id": "beta",
                "sourceConversationId": "source-beta",
                "sourceAppId": "wechat",
                "sourceAccountId": "wechat:default",
                "title": "Beta",
                "kind": "group",
                "lastMessageAt": 3_000,
                "messageCount": 1,
                "isFavorite": True,
                "isArchived": False,
                "isIndexingEnabled": True,
                "note": "keep",
                "domain": None,
                "projectId": None,
                "projectRole": None,
                "classificationStatus": "needs_review",
                "groupId": None,
            },
            {
                "id": "alpha",
                "sourceConversationId": "source-alpha",
                "sourceAppId": "wechat",
                "sourceAccountId": "wechat:default",
                "title": "Alpha",
                "kind": "direct",
                "lastMessageAt": 2_000,
                "messageCount": 2,
                "isFavorite": False,
                "isArchived": False,
                "isIndexingEnabled": True,
                "note": "",
                "domain": None,
                "projectId": None,
                "projectRole": None,
                "classificationStatus": "needs_review",
                "groupId": None,
            },
        ],
        "nextCursor": None,
    }
    assert messages.status_code == 200
    assert messages.json()["items"][0] == {
        "id": 2,
        "conversationId": "alpha",
        "sourceMessageId": "m-2",
        "senderId": "bob",
        "senderName": "Bob",
        "sentAt": 2_000,
        "type": "text",
        "content": "second synthetic message",
        "assets": [],
    }
    assert len(opened_connections) == 2
    for connection in opened_connections:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            continue
        raise AssertionError("request connection was not closed")


def test_summary_retains_ten_source_backed_important_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from wechat_ai.services import llm_service

    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, dedupe_key, sender_id,
                sender_name, sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "alpha",
                    f"important-{index}",
                    f"alpha:important-{index}",
                    "alice",
                    "Alice",
                    3_000 + index,
                    "text",
                    f"请在明天完成第{index}项关键交付并同步确认结果",
                )
                for index in range(1, 13)
            ],
        )
        connection.commit()

    monkeypatch.setattr(llm_service, "_use_local_llm", False)
    client, _ = _make_client(database_path)

    response = client.get("/api/conversations/alpha/summary")

    assert response.status_code == 200
    important_messages = response.json()["importantMessages"]
    assert len(important_messages) == 10
    assert len({item["messageId"] for item in important_messages}) == 10
    assert all(
        set(item) == {"messageId", "senderName", "sentAt", "content"}
        for item in important_messages
    )
    assert all("关键交付" in item["content"] for item in important_messages)


def test_media_endpoint_keeps_type_records_without_resolved_assets(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, dedupe_key, sender_id,
                sender_name, sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ("alpha", "m-image", "alpha:m-image", "alice", "Alice", 4_000, "image", "图片消息"),
                ("alpha", "m-voice", "alpha:m-voice", "bob", "Bob", 3_500, "voice", "语音 00:12"),
                ("alpha", "m-file", "alpha:m-file", "alice", "Alice", 3_000, "attachment", "设计说明.pdf"),
            ),
        )
        connection.commit()

    client, _ = _make_client(database_path)
    response = client.get("/api/conversations/alpha/media?kind=all")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["type"] for item in items] == ["image", "voice", "attachment"]
    assert all(item["hasLocalFile"] is False for item in items)


def test_messages_include_archived_assets_and_sanitize_unsupported_content(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.services.knowledge_projection_service import project_knowledge

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    _seed_library(database_path)
    archived_bytes = b"archived-image-bytes"
    archived_path = asset_root / "archived-image.dat"
    archived_path.write_bytes(archived_bytes)

    with closing(initialize_database(database_path)) as connection:
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id,
                source_message_id,
                dedupe_key,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "alpha",
                    "m-image",
                    "alpha:m-image",
                    "Alice",
                    4_000,
                    "image",
                    "[图片]",
                ),
                (
                    "alpha",
                    "m-unsupported",
                    "alpha:m-unsupported",
                    "Alice",
                    5_000,
                    "unsupported_999",
                    "[不支持的消息类型: 999]",
                ),
            ),
        )
        project_knowledge(connection, conversation_ids=("alpha",))
        asset_row = connection.execute(
            """
            SELECT assets.id, assets.part_id
            FROM assets
            JOIN message_parts ON message_parts.id = assets.part_id
            JOIN messages ON messages.id = message_parts.message_id
            WHERE messages.source_message_id = 'm-image'
            """
        ).fetchone()
        assert asset_row is not None
        asset_id, message_part_id = map(int, asset_row)
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                file_name = 'archived-image.dat',
                mime_type = 'application/octet-stream',
                byte_size = ?,
                local_path = 'archived-image.dat'
            WHERE id = ?
            """,
            (len(archived_bytes), asset_id),
        )
        connection.execute(
            "UPDATE conversations SET is_archived = 1 WHERE id = 'alpha'"
        )
        connection.commit()

    client, _connections = _make_client(
        database_path,
        asset_root=asset_root,
    )
    response = client.get("/api/conversations/alpha/messages")

    assert response.status_code == 200
    messages = {
        item["sourceMessageId"]: item
        for item in response.json()["items"]
    }
    assert messages["m-image"]["assets"] == [
        {
            "id": asset_id,
            "messagePartId": message_part_id,
            "kind": "image",
            "status": "resolved",
            "externalUrl": None,
            "hasLocalFile": True,
            "fileName": "archived-image.dat",
            "mimeType": "application/octet-stream",
            "byteSize": len(archived_bytes),
            "contentUrl": f"/api/assets/{asset_id}",
            "readinessState": "resolved",
            "processingUrl": None,
            "playbackUrl": None,
            "errorCode": None,
            "derivative": None,
        }
    ]
    assert messages["m-unsupported"]["type"] == "unsupported"
    assert messages["m-unsupported"]["content"] == "暂无法预览此消息"


def test_get_and_patch_conversation_preserves_non_local_fields(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    client, _connections = _make_client(database_path)

    detail = client.get("/api/conversations/alpha")
    updated = client.patch(
        "/api/conversations/alpha",
        json={
            "isFavorite": True,
            "isArchived": True,
            "note": "local note",
        },
    )

    assert detail.status_code == 200
    assert detail.json()["isFavorite"] is False
    assert updated.status_code == 200
    assert updated.json() == {
        "id": "alpha",
        "sourceConversationId": "source-alpha",
        "sourceAppId": "wechat",
        "sourceAccountId": "wechat:default",
        "title": "Alpha",
        "kind": "direct",
        "lastMessageAt": 2_000,
        "messageCount": 2,
        "isFavorite": True,
        "isArchived": True,
        "isIndexingEnabled": True,
        "note": "local note",
        "domain": None,
        "projectId": None,
        "projectRole": None,
        "classificationStatus": "needs_review",
        "groupId": None,
    }

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT source_conversation_id, title, kind, message_count,
                   is_favorite, is_archived, note
            FROM conversations
            WHERE id = ?
            """,
            ("alpha",),
        ).fetchone()
        override = connection.execute(
            """
            SELECT is_favorite, is_archived, note
            FROM conversation_overrides
            WHERE conversation_id = ?
            """,
            ("alpha",),
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "source-alpha",
        "Alpha",
        "direct",
        2,
        0,
        0,
        "",
    )
    assert override == (1, 1, "local note")


def test_conversation_groups_persist_without_writing_raw_conversations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    client, _connections = _make_client(database_path)

    defaults = client.get("/api/conversation-groups")
    created = client.post("/api/conversation-groups", json={"name": "客户"})
    group_id = created.json()["id"]
    assigned = client.patch(
        "/api/conversations/alpha",
        json={"groupId": group_id},
    )
    listed = client.get("/api/conversation-groups")
    deleted = client.delete(f"/api/conversation-groups/{group_id}")
    after_delete = client.get("/api/conversations/alpha")

    assert defaults.status_code == 200
    assert [item["name"] for item in defaults.json()["items"]] == ["个人", "工作", "学习"]
    assert created.status_code == 201
    assert assigned.status_code == 200
    assert assigned.json()["groupId"] == group_id
    assert next(item for item in listed.json()["items"] if item["id"] == group_id)["count"] == 1
    assert deleted.status_code == 204
    assert after_delete.json()["groupId"] is None

    with sqlite3.connect(database_path) as connection:
        raw = connection.execute(
            "SELECT title, is_favorite, is_archived FROM conversations WHERE id = 'alpha'"
        ).fetchone()
        override = connection.execute(
            "SELECT group_id FROM conversation_overrides WHERE conversation_id = 'alpha'"
        ).fetchone()
    assert raw == ("Alpha", 0, 0)
    assert override == (None,)


def test_not_found_and_validation_errors_use_stable_contract(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    client, _connections = _make_client(database_path)

    missing = client.get("/api/conversations/not-found")
    invalid_patch = client.patch(
        "/api/conversations/alpha",
        json={"title": "must not change"},
    )
    invalid_limit = client.get("/api/conversations", params={"limit": 201})

    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "CONVERSATION_NOT_FOUND",
            "message": "未找到会话。",
            "retryable": False,
            "details": {},
        }
    }
    for response in (invalid_patch, invalid_limit):
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数无效。",
                "retryable": False,
                "details": {},
            }
        }




def test_source_filters_and_index_pause_remove_only_agent_projections(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    from wechat_ai.database import initialize_database
    from wechat_ai.services.graph_service import project_graph
    from wechat_ai.services.knowledge_projection_service import project_knowledge

    with closing(initialize_database(database_path)) as connection:
        project_knowledge(connection)
        project_graph(connection)
        connection.commit()
    client, _connections = _make_client(database_path)

    filtered = client.get(
        "/api/conversations",
        params={
            "sourceAppId": "wechat",
            "sourceAccountId": "wechat:default",
            "kind": "group",
        },
    )
    paused = client.patch(
        "/api/conversations/beta",
        json={"isIndexingEnabled": False},
    )

    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["items"]] == ["beta"]
    assert paused.status_code == 200
    assert paused.json()["isIndexingEnabled"] is False
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = 'beta'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE conversation_id = 'beta'"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM graph_nodes
            WHERE trace_kind = 'conversation' AND trace_id = 'beta'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM graph_edge_evidence
            WHERE evidence_kind IN ('message', 'message_part')
              AND evidence_id IN (
                  SELECT CAST(messages.id AS TEXT)
                  FROM messages
                  WHERE messages.conversation_id = 'beta'
                  UNION
                  SELECT CAST(message_parts.id AS TEXT)
                  FROM message_parts
                  JOIN messages ON messages.id = message_parts.message_id
                  WHERE messages.conversation_id = 'beta'
              )
            """
        ).fetchone()[0] == 0

    resumed = client.patch(
        "/api/conversations/beta",
        json={"isIndexingEnabled": True},
    )
    assert resumed.status_code == 200
    assert resumed.json()["isIndexingEnabled"] is True
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE conversation_id = 'beta'"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM graph_nodes
            WHERE trace_kind = 'conversation' AND trace_id = 'beta'
            """
        ).fetchone()[0] == 1


def test_archived_conversations_are_hidden_by_default_but_remain_recoverable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    client, _connections = _make_client(database_path)

    archived = client.patch(
        "/api/conversations/alpha",
        json={"isArchived": True},
    )
    default_list = client.get("/api/conversations")
    including_archived = client.get(
        "/api/conversations",
        params={"includeArchived": True},
    )

    assert archived.status_code == 200
    assert [item["id"] for item in default_list.json()["items"]] == ["beta"]
    assert [item["id"] for item in including_archived.json()["items"]] == [
        "beta",
        "alpha",
    ]


def test_message_deep_link_page_starts_at_requested_message(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    _seed_library(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
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
            [
                (
                    "alpha",
                    f"deep-{index}",
                    f"alpha:deep-{index}",
                    "alice",
                    "Alice",
                    3_000 + index,
                    "text",
                    f"deep link message {index}",
                )
                for index in range(75)
            ],
        )
        target_id = int(
            connection.execute(
                "SELECT id FROM messages WHERE source_message_id = 'deep-5'"
            ).fetchone()[0]
        )
        connection.commit()

    client, _connections = _make_client(database_path)
    response = client.get(
        "/api/conversations/alpha/messages",
        params={"targetMessageId": target_id},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == target_id


def test_create_app_registers_conversation_routes(tmp_path: Path) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    database_path = tmp_path / "data" / "library.sqlite"
    _seed_library(database_path)
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=database_path,
        output_dir=tmp_path / "output",
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
    ).get("/api/conversations")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        "beta",
        "alpha",
    ]
