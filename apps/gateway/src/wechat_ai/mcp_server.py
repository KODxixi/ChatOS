"""Model Context Protocol (MCP) stdio server for ChatOS.

Exposes ChatOS's read-only memory/retrieval capabilities as MCP tools so any
local agent (Claude Code, OpenClaw, ...) can query WeChat memory with a single
config line.  Zero third-party dependencies: implements the MCP stdio transport
(newline-delimited JSON-RPC 2.0) with the standard library, matching the gateway
"standard library first" discipline.

All tools are strictly read-only: the library is opened with SQLite URI
``mode=ro``.  This process is independent from the gateway; it needs no
long-running service.

Run with:  python -m wechat_ai mcp
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import load_settings
from .runtime_contract import APP_VERSION

SERVER_NAME = "chatos-mcp"
PROTOCOL_VERSION = "2025-06-18"


def _readonly_connection(library_db: str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _open_library() -> sqlite3.Connection:
    settings = load_settings()
    return _readonly_connection(str(settings.library_db))


def _conn_factory() -> Callable[[], sqlite3.Connection]:
    settings = load_settings()
    library_db = str(settings.library_db)
    return lambda: _readonly_connection(library_db)


# --------------------------------------------------------------------------- tools


def _tool_search(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """Search WeChat messages by dual-channel (keyword BM25 + vector) with
    optional cross-encoder rerank.  Degrades gracefully if retrieval models
    are unavailable."""
    from .services.search_service import search_messages

    query = str(args["query"]).strip()
    if not query:
        raise ValueError("query 不能为空")

    embed_fn, reranker, rerank_enabled = _build_retrieval_deps()

    result = search_messages(
        conn,
        query=query,
        conversation_id=args.get("conversationId"),
        sender_name=args.get("senderName"),
        sent_at_from=None,
        sent_at_to=None,
        limit=int(args.get("limit", 20)),
        cursor=None,
        embed_fn=embed_fn,
        reranker=reranker,
        rerank_enabled=rerank_enabled,
    )
    items = result.get("items", [])
    mode = result.get("mode", "legacy")
    note = result.get("note") or ("消息按时间倒序；content 为消息原文" if mode == "legacy" else "")
    return {
        "count": len(items),
        "query": query,
        "items": items,
        "mode": mode,
        "note": note,
    }


_RETRIEVAL_DEPS_CACHE: dict[str, Any] = {}


def _build_retrieval_deps() -> tuple[Any, Any, bool]:
    """Construct embed_fn / reranker from settings, cached across calls.

    Returns (embed_fn, reranker, rerank_enabled).  Any construction failure
    degrades to (None, None, False) -> legacy time-ordered path.
    """
    if "resolved" in _RETRIEVAL_DEPS_CACHE:
        return _RETRIEVAL_DEPS_CACHE["resolved"]  # type: ignore[return-value]
    try:
        from .services.embedding_service import (
            EmbeddingClient,
            configure_from_settings,
        )
        from .services.reranker_service import RerankerClient

        settings = load_settings()
        configure_from_settings(settings)
        embed_client = EmbeddingClient(settings.retrieval_base_url)
        embed_fn = lambda text: embed_client.embed([text])[0]  # noqa: E731
        reranker = (
            RerankerClient(settings.retrieval_base_url)
            if settings.rerank_enabled
            else None
        )
        resolved = (embed_fn, reranker, bool(settings.rerank_enabled))
    except Exception:
        resolved = (None, None, False)
    _RETRIEVAL_DEPS_CACHE["resolved"] = resolved
    return resolved


def _tool_conversations(
    conn: sqlite3.Connection, args: dict[str, Any]
) -> dict[str, Any]:
    """List WeChat conversations (groups / direct chats / accounts)."""
    limit = max(1, min(int(args.get("limit", 50)), 200))
    where: list[str] = []
    params: list[object] = []
    kind = args.get("kind")
    if kind:
        where.append("kind = ?")
        params.append(kind)
    favorite = args.get("favorite")
    if favorite is not None:
        where.append("is_favorite = ?")
        params.append(1 if favorite else 0)
    archived = args.get("archived")
    if archived is not None:
        where.append("is_archived = ?")
        params.append(1 if archived else 0)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""
        SELECT id, title, kind, message_count, last_message_at, is_favorite, is_archived
        FROM conversations{clause}
        ORDER BY COALESCE(last_message_at, 0) DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return {
        "count": len(rows),
        "items": [
            {
                "id": r["id"],
                "title": r["title"],
                "kind": r["kind"],
                "messageCount": r["message_count"],
                "lastMessageAt": r["last_message_at"],
                "isFavorite": bool(r["is_favorite"]),
                "isArchived": bool(r["is_archived"]),
            }
            for r in rows
        ],
    }


def _tool_recent_messages(
    conn: sqlite3.Connection, args: dict[str, Any]
) -> dict[str, Any]:
    """Read the most recent messages of one conversation."""
    conversation_id = str(args["conversationId"]).strip()
    if not conversation_id:
        raise ValueError("conversationId 不能为空")
    limit = max(1, min(int(args.get("limit", 20)), 100))
    row = conn.execute(
        "SELECT title, kind FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"未找到会话 {conversation_id}")
    messages = conn.execute(
        """
        SELECT id, sender_name, sent_at, type, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY sent_at DESC, id DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    ).fetchall()
    return {
        "conversationId": conversation_id,
        "conversationTitle": row["title"],
        "kind": row["kind"],
        "count": len(messages),
        "messages": [
            {
                "id": m["id"],
                "senderName": m["sender_name"],
                "sentAt": m["sent_at"],
                "type": m["type"],
                "content": m["content"],
            }
            for m in reversed(messages)
        ],
    }


def _tool_tasks(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """List extracted to-dos (待办) from conversations."""
    limit = max(1, min(int(args.get("limit", 50)), 200))
    where: list[str] = []
    params: list[object] = []
    status = args.get("status")
    if status:
        where.append("status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        rows = conn.execute(
            f"""
            SELECT task_id, conversation_title, content, sender_name, sent_at,
                   status, priority, due_date
            FROM tasks{clause}
            ORDER BY COALESCE(sent_at, 0) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"count": 0, "items": [], "note": "待办表尚未生成"}
    return {
        "count": len(rows),
        "items": [
            {
                "taskId": r["task_id"],
                "conversationTitle": r["conversation_title"],
                "content": r["content"],
                "senderName": r["sender_name"],
                "sentAt": r["sent_at"],
                "status": r["status"],
                "priority": r["priority"],
                "dueDate": r["due_date"],
            }
            for r in rows
        ],
    }


def _tool_memory_status(
    conn: sqlite3.Connection, args: dict[str, Any]
) -> dict[str, Any]:
    """Memory health: schema version, message counts, vector index coverage."""
    schema_version = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'schema_version'"
    ).fetchone()
    conversations = conn.execute(
        "SELECT count(*) FROM conversations"
    ).fetchone()[0]
    messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    indexed: list[dict[str, Any]] = []
    try:
        for row in conn.execute(
            """
            SELECT model_version, length(embedding)/4 AS dims, count(*) AS n
            FROM message_embeddings
            WHERE status = 'indexed'
            GROUP BY model_version, length(embedding)
            """
        ):
            indexed.append(
                {
                    "modelVersion": row["model_version"],
                    "dimensions": int(row["dims"]),
                    "indexedCount": int(row["n"]),
                }
            )
    except sqlite3.OperationalError:
        indexed = []
    return {
        "appVersion": APP_VERSION,
        "schemaVersion": int(schema_version[0]) if schema_version else None,
        "conversations": int(conversations),
        "messages": int(messages),
        "vectorIndex": indexed,
        "readOnly": True,
    }


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "chatos_search",
        "description": (
            "搜索微信聊天记录中的消息（关键词，支持中文）。返回命中的消息原文、发送人、时间与会话标题。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如「咏滨名邸 价格」"},
                "conversationId": {"type": "string", "description": "限定单个会话（可选）"},
                "senderName": {"type": "string", "description": "限定发送人（可选）"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "chatos_conversations",
        "description": "列出微信会话（群聊/私聊），按最近活跃排序。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["direct", "group", "system", "unknown"],
                    "description": "会话类型：私聊/群聊/公众号（可选）",
                },
                "favorite": {"type": "boolean", "description": "只看星标（可选）"},
                "archived": {"type": "boolean", "description": "只看已归档（可选）"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    },
    {
        "name": "chatos_recent_messages",
        "description": "读取指定会话最近的聊天消息（按时间正序返回）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {"type": "string", "description": "会话 ID（来自 chatos_conversations）"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["conversationId"],
        },
    },
    {
        "name": "chatos_tasks",
        "description": "列出从聊天中提取的待办事项。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "按状态过滤（可选），如 pending/done"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    },
    {
        "name": "chatos_memory_status",
        "description": "查看微信记忆库的健康状态：版本、会话/消息数、向量索引覆盖。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_TOOL_IMPLS: dict[str, Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]] = {
    "chatos_search": _tool_search,
    "chatos_conversations": _tool_conversations,
    "chatos_recent_messages": _tool_recent_messages,
    "chatos_tasks": _tool_tasks,
    "chatos_memory_status": _tool_memory_status,
}


# --------------------------------------------------------------------------- protocol


def handle_request(
    conn_factory: Callable[[], sqlite3.Connection],
    message: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; return the response dict (None = notification)."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        client_version = (
            params.get("protocolVersion")
            if isinstance(params, dict)
            else None
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": client_version or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": APP_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOL_SPECS},
        }
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") or {}
        impl = _TOOL_IMPLS.get(name)
        if impl is None:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        try:
            with conn_factory() as conn:
                result = impl(conn, arguments)
        except (ValueError, KeyError) as exc:
            return _tool_error(request_id, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _tool_error(request_id, f"{type(exc).__name__}: {exc}")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False),
                    }
                ],
                "isError": False,
            },
        }
    if is_notification:
        return None
    return _error(request_id, -32601, "Method not found")


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}],
            "isError": True,
        },
    }


def run_stdio_server(conn_factory: Callable[[], sqlite3.Connection] | None = None) -> None:
    """Serve MCP over stdio: one JSON message per line, UTF-8."""
    factory = conn_factory or _conn_factory()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw_line in stdin:
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            stdout.write(
                (
                    json.dumps(
                        _error(None, -32700, "Parse error"),
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            stdout.flush()
            continue
        response = handle_request(factory, message)
        if response is not None:
            stdout.write(
                (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
            )
            stdout.flush()


def main() -> None:
    run_stdio_server()


if __name__ == "__main__":
    main()
