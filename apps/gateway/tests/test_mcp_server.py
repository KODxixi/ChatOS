"""MCP stdio server protocol tests (zero-dependency JSON-RPC over stdio)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from wechat_ai.mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    TOOL_SPECS,
    handle_request,
)


def _build_library(tmp_path) -> str:
    db_path = tmp_path / "memory.sqlite"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            source_conversation_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            last_message_at INTEGER,
            message_count INTEGER NOT NULL DEFAULT 0,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            is_indexing_enabled INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            source_message_id TEXT,
            dedupe_key TEXT NOT NULL UNIQUE,
            sender_id TEXT,
            sender_name TEXT NOT NULL DEFAULT '',
            sent_at INTEGER NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            raw_payload_json TEXT
        );
        CREATE VIRTUAL TABLE message_fts USING fts5(
            content, sender_name, conversation_title, tokenize = 'unicode61'
        );
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conversation_overrides (
            conversation_id TEXT PRIMARY KEY,
            is_indexing_enabled INTEGER
        );
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            conversation_title TEXT,
            content TEXT,
            sender_name TEXT,
            sent_at INTEGER,
            status TEXT,
            priority TEXT,
            due_date TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            source_message_id INTEGER
        );
        INSERT INTO app_meta(key, value) VALUES ('schema_version', '22');
        INSERT INTO conversations
            (id, source_conversation_id, title, kind, last_message_at, message_count)
        VALUES
            ('c1', 'wx_c1', '项目讨论', 'group', 1700000003, 3),
            ('c2', 'wx_c2', '家人群', 'group', 1700000001, 1);
        INSERT INTO messages
            (conversation_id, dedupe_key, sender_name, sent_at, type, content)
        VALUES
            ('c1', 'm1', '甲方', 1700000001, 'text', '咏滨名邸报价单你发我一下'),
            ('c1', 'm2', 'Gary', 1700000002, 'text', '水泥标号用 425，明天到货'),
            ('c1', 'm3', 'Gary', 1700000003, 'text', '记得跟进报审节点'),
            ('c2', 'm4', '老妈', 1700000001, 'text', '周末回家吃饭');
        INSERT INTO message_fts(rowid, content, sender_name, conversation_title)
        SELECT id, content, sender_name,
               (SELECT title FROM conversations WHERE id = messages.conversation_id)
        FROM messages;
        INSERT INTO tasks
            (task_id, conversation_id, conversation_title, content, status, priority)
        VALUES ('t1', 'c1', '项目讨论', '跟进报审节点', 'pending', 'high');
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _readonly_factory(db_path: str):
    def factory() -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    return factory


@pytest.fixture()
def conn_factory(tmp_path):
    db_path = _build_library(tmp_path)
    return _readonly_factory(db_path)


def _call(conn_factory, method: str, params: dict | None = None, request_id: object = 1):
    message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return handle_request(conn_factory, message)


def test_initialize(conn_factory):
    response = _call(conn_factory, "initialize", {"protocolVersion": PROTOCOL_VERSION})
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == SERVER_NAME
    assert result["capabilities"]["tools"]["listChanged"] is False


def test_initialized_notification_gets_no_response(conn_factory):
    response = handle_request(conn_factory, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response is None


def test_ping(conn_factory):
    response = _call(conn_factory, "ping")
    assert response["result"] == {}


def test_tools_list(conn_factory):
    response = _call(conn_factory, "tools/list")
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [spec["name"] for spec in TOOL_SPECS]
    assert "chatos_search" in names
    assert "chatos_memory_status" in names


def test_tool_search_hits_chinese_keyword(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_search", "arguments": {"query": "水泥", "limit": 10}},
    )
    assert response["result"]["isError"] is False
    text = response["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["count"] == 1
    assert "水泥标号用 425" in payload["items"][0]["content"]
    assert payload["items"][0]["conversationTitle"] == "项目讨论"


def test_tool_search_respects_indexing_disable(tmp_path):
    db_path = _build_library(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO conversation_overrides(conversation_id, is_indexing_enabled) VALUES ('c1', 0)"
    )
    conn.commit()
    conn.close()
    factory = _readonly_factory(db_path)
    response = _call(
        factory,
        "tools/call",
        {"name": "chatos_search", "arguments": {"query": "水泥"}},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["count"] == 0


def test_tool_conversations(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_conversations", "arguments": {"kind": "group"}},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["count"] == 2
    assert payload["items"][0]["title"] == "项目讨论"  # most recent first


def test_tool_recent_messages(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_recent_messages", "arguments": {"conversationId": "c1", "limit": 3}},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["conversationTitle"] == "项目讨论"
    contents = [m["content"] for m in payload["messages"]]
    assert contents == ["咏滨名邸报价单你发我一下", "水泥标号用 425，明天到货", "记得跟进报审节点"]


def test_tool_recent_messages_unknown_conversation(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_recent_messages", "arguments": {"conversationId": "nope"}},
    )
    assert response["result"]["isError"] is True


def test_tool_tasks(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_tasks", "arguments": {"status": "pending"}},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["count"] == 1
    assert payload["items"][0]["content"] == "跟进报审节点"


def test_tool_memory_status(conn_factory):
    response = _call(conn_factory, "tools/call", {"name": "chatos_memory_status", "arguments": {}})
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["schemaVersion"] == 22
    assert payload["conversations"] == 2
    assert payload["messages"] == 4
    assert payload["readOnly"] is True


def test_unknown_tool_returns_error(conn_factory):
    response = _call(
        conn_factory,
        "tools/call",
        {"name": "chatos_nope", "arguments": {}},
    )
    assert "error" in response


def test_unknown_method(conn_factory):
    response = _call(conn_factory, "prompts/list")
    assert response["error"]["code"] == -32601


def test_invalid_request_not_dict(conn_factory):
    response = handle_request(conn_factory, "not-a-dict")
    assert response["error"]["code"] == -32600
