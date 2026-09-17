from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from urllib.parse import urlsplit

from ..message_display import safe_message_display


_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_CITATION_EVIDENCE_KINDS = ("message", "message_part")
_GRAPH_EVIDENCE_BATCH_SIZE = 400


def _delete_in_batches(connection: sqlite3.Connection, table: str, column: str,
                       ids: list[str], batch_size: int = 500) -> None:
    """DELETE with a dynamic IN-list, chunked to stay under SQLite's 999-variable
    limit (2026-09-13: >999 edge_ids made the PATCH isIndexingEnabled=false path
    500). Each chunk runs its own statement in the caller's transaction."""
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        placeholders = ", ".join("?" for _item in chunk)
        connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", chunk)
@dataclass(frozen=True, slots=True)
class GraphProjectionResult:
    nodes: int
    edges: int
    evidence: int

class GraphEdgeNotFound(LookupError):
    pass



def project_graph(
    connection: sqlite3.Connection,
    *,
    conversation_ids: Iterable[str] | None = None,
) -> GraphProjectionResult:
    """Project only relationships supported by imported local facts."""

    scope = _normalize_scope(conversation_ids)
    condition, parameters = _condition("conversations.id", scope)

    _delete_unsupported_content_graph(connection)
    _delete_disabled_conversation_graph(connection, condition, parameters)
    condition = (
        f"({condition}) AND COALESCE("
        "(SELECT o.is_indexing_enabled FROM conversation_overrides o "
        "WHERE o.conversation_id = conversations.id), "
        "conversations.is_indexing_enabled, 1) = 1"
    )

    app_nodes: dict[str, str] = {}
    for row in connection.execute(
        "SELECT id, display_name FROM source_apps ORDER BY id"
    ):
        app_id = str(row[0])
        node_id = _stable_id("source_app", app_id)
        app_nodes[app_id] = node_id
        _upsert_node(
            connection,
            node_id=node_id,
            kind="source_app",
            label=str(row[1]),
            trace_kind="source_app",
            trace_id=app_id,
        )

    account_nodes: dict[str, str] = {}
    for row in connection.execute(
        """
        SELECT id, source_app_id, display_name
        FROM source_accounts
        ORDER BY id
        """
    ):
        account_id = str(row[0])
        app_id = str(row[1])
        account_node_id = _stable_id("source_account", account_id)
        account_nodes[account_id] = account_node_id
        _upsert_node(
            connection,
            node_id=account_node_id,
            kind="source_account",
            label=str(row[2]),
            trace_kind="source_account",
            trace_id=account_id,
        )
        app_node_id = app_nodes[app_id]
        edge_id = _stable_id("contains", app_node_id, account_node_id)
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=app_node_id,
            target_node_id=account_node_id,
            kind="contains",
            weight=1,
        )
        _add_evidence(connection, edge_id, "source_account", account_id)

    conversations: dict[str, dict[str, str]] = {}
    for row in connection.execute(
        f"""
        SELECT
            conversations.id,
            conversations.title,
            conversation_origins.source_account_id
        FROM conversations
        JOIN conversation_origins
          ON conversation_origins.conversation_id = conversations.id
        WHERE {condition}
        ORDER BY conversations.id
        """,
        parameters,
    ):
        conversation_id = str(row[0])
        account_id = str(row[2])
        conversation_node_id = _stable_id("conversation", conversation_id)
        conversations[conversation_id] = {
            "account_id": account_id,
            "node_id": conversation_node_id,
        }
        _upsert_node(
            connection,
            node_id=conversation_node_id,
            kind="conversation",
            label=str(row[1]),
            trace_kind="conversation",
            trace_id=conversation_id,
        )
        account_node_id = account_nodes[account_id]
        edge_id = _stable_id(
            "contains",
            account_node_id,
            conversation_node_id,
        )
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=account_node_id,
            target_node_id=conversation_node_id,
            kind="contains",
            weight=1,
        )
        _add_evidence(connection, edge_id, "conversation", conversation_id)

    message_condition, message_parameters = _condition(
        "messages.conversation_id",
        scope,
    )
    message_condition = (
        f"({message_condition}) AND EXISTS ("
        "SELECT 1 FROM conversations "
        "WHERE conversations.id = messages.conversation_id "
        "AND COALESCE("
        "(SELECT o.is_indexing_enabled FROM conversation_overrides o "
        "WHERE o.conversation_id = conversations.id), "
        "conversations.is_indexing_enabled, 1) = 1)"
    )
    participant_edges: dict[tuple[str, str, str], str] = {}
    for row in connection.execute(
        f"""
        SELECT
            conversation_origins.source_account_id,
            messages.conversation_id,
            COALESCE(
                NULLIF(messages.sender_id, ''),
                NULLIF(messages.sender_name, ''),
                'unknown'
            ) AS sender_key,
            COALESCE(MAX(NULLIF(messages.sender_name, '')), 'Unknown') AS label,
            COUNT(*) AS weight
        FROM messages
        JOIN conversation_origins
          ON conversation_origins.conversation_id = messages.conversation_id
        WHERE {message_condition}
        GROUP BY
            conversation_origins.source_account_id,
            messages.conversation_id,
            sender_key
        """,
        message_parameters,
    ):
        account_id = str(row[0])
        conversation_id = str(row[1])
        sender_key = str(row[2])
        participant_trace_id = f"{account_id}:{sender_key}"
        participant_node_id = _stable_id("participant", participant_trace_id)
        _upsert_node(
            connection,
            node_id=participant_node_id,
            kind="participant",
            label=str(row[3]),
            trace_kind="participant",
            trace_id=participant_trace_id,
        )
        conversation_node_id = conversations[conversation_id]["node_id"]
        edge_id = _stable_id(
            "participates_in",
            participant_node_id,
            conversation_node_id,
        )
        participant_edges[(account_id, conversation_id, sender_key)] = edge_id
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=participant_node_id,
            target_node_id=conversation_node_id,
            kind="participates_in",
            weight=int(row[4]),
        )

    for row in connection.execute(
        f"""
        SELECT
            conversation_origins.source_account_id,
            messages.conversation_id,
            COALESCE(
                NULLIF(messages.sender_id, ''),
                NULLIF(messages.sender_name, ''),
                'unknown'
            ) AS sender_key,
            messages.id
        FROM messages
        JOIN conversation_origins
          ON conversation_origins.conversation_id = messages.conversation_id
        WHERE {message_condition}
        """,
        message_parameters,
    ):
        edge_id = participant_edges[(str(row[0]), str(row[1]), str(row[2]))]
        _add_evidence(connection, edge_id, "message", str(row[3]))

    part_condition, part_parameters = _condition(
        "messages.conversation_id",
        scope,
    )
    part_condition = (
        f"({part_condition}) AND EXISTS ("
        "SELECT 1 FROM conversations "
        "WHERE conversations.id = messages.conversation_id "
        "AND COALESCE("
        "(SELECT o.is_indexing_enabled FROM conversation_overrides o "
        "WHERE o.conversation_id = conversations.id), "
        "conversations.is_indexing_enabled, 1) = 1)"
    )
    content_edges: dict[tuple[str, str], str] = {}
    for row in connection.execute(
        f"""
        SELECT messages.conversation_id, message_parts.kind, COUNT(*)
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {part_condition}
          AND message_parts.kind <> 'unsupported'
        GROUP BY messages.conversation_id, message_parts.kind
        """,
        part_parameters,
    ):
        conversation_id = str(row[0])
        content_kind = str(row[1])
        kind_node_id = _stable_id("content_kind", content_kind)
        _upsert_node(
            connection,
            node_id=kind_node_id,
            kind="content_kind",
            label=content_kind,
            trace_kind="content_kind",
            trace_id=content_kind,
        )
        conversation_node_id = conversations[conversation_id]["node_id"]
        edge_id = _stable_id(
            "has_content",
            conversation_node_id,
            kind_node_id,
        )
        content_edges[(conversation_id, content_kind)] = edge_id
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=conversation_node_id,
            target_node_id=kind_node_id,
            kind="has_content",
            weight=int(row[2]),
        )

    for row in connection.execute(
        f"""
        SELECT messages.conversation_id, message_parts.kind, message_parts.id
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {part_condition}
          AND message_parts.kind <> 'unsupported'
        """,
        part_parameters,
    ):
        edge_id = content_edges[(str(row[0]), str(row[1]))]
        _add_evidence(connection, edge_id, "message_part", str(row[2]))

    for row in connection.execute(
        f"""
        SELECT
            messages.conversation_id,
            assets.id,
            assets.kind,
            COALESCE(assets.file_name, assets.kind),
            message_parts.id
        FROM assets
        JOIN message_parts ON message_parts.id = assets.part_id
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {part_condition}
          AND message_parts.kind <> 'unsupported'
        """,
        part_parameters,
    ):
        conversation_id = str(row[0])
        asset_id = str(row[1])
        asset_node_id = _stable_id("asset", asset_id)
        _upsert_node(
            connection,
            node_id=asset_node_id,
            kind="asset",
            label=str(row[3]),
            trace_kind="asset",
            trace_id=asset_id,
        )
        conversation_node_id = conversations[conversation_id]["node_id"]
        edge_id = _stable_id(
            "has_asset",
            conversation_node_id,
            asset_node_id,
        )
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=conversation_node_id,
            target_node_id=asset_node_id,
            kind="has_asset",
            weight=1,
        )
        _add_evidence(connection, edge_id, "asset", asset_id)
        _add_evidence(connection, edge_id, "message_part", str(row[4]))

    link_facts: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in connection.execute(
        f"""
        SELECT
            messages.conversation_id,
            message_parts.id,
            messages.id,
            message_parts.text_content,
            message_parts.metadata_json
        FROM message_parts
        JOIN messages ON messages.id = message_parts.message_id
        WHERE {part_condition}
          AND message_parts.kind = 'link'
          AND message_parts.status = 'resolved'
        """,
        part_parameters,
    ):
        domain = _link_domain(str(row[3]), str(row[4]))
        if domain is None:
            continue
        link_facts.setdefault((str(row[0]), domain), []).append(
            (str(row[2]), str(row[1]))
        )

    for (conversation_id, domain), citations in sorted(link_facts.items()):
        domain_node_id = _stable_id("link_domain", domain)
        _upsert_node(
            connection,
            node_id=domain_node_id,
            kind="link_domain",
            label=domain,
            trace_kind="link_domain",
            trace_id=domain,
        )
        conversation_node_id = conversations[conversation_id]["node_id"]
        edge_id = _stable_id("links_to", conversation_node_id, domain_node_id)
        _upsert_edge(
            connection,
            edge_id=edge_id,
            source_node_id=conversation_node_id,
            target_node_id=domain_node_id,
            kind="links_to",
            weight=len(citations),
        )
        for _message_id, part_id in citations:
            _add_evidence(connection, edge_id, "message_part", part_id)

    return GraphProjectionResult(
        nodes=int(connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]),
        edges=int(connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]),
        evidence=int(
            connection.execute("SELECT COUNT(*) FROM graph_edge_evidence").fetchone()[0]
        ),
    )


def get_graph(
    connection: sqlite3.Connection,
    *,
    source_app_id: str | None = None,
    source_account_id: str | None = None,
    conversation_id: str | None = None,
    evidence_limit: int = 20,
    limit: int = 80,
    hide_noise: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    node_ids = _graph_scope_node_ids(
        connection,
        source_app_id=source_app_id,
        source_account_id=source_account_id,
        conversation_id=conversation_id,
    )
    if node_ids is not None and not node_ids:
        return {"nodes": [], "edges": []}

    node_limit = max(0, limit)
    if node_limit == 0:
        return {"nodes": [], "edges": []}
    candidate_limit = max(node_limit, node_limit * 8)
    noise_clause = (
        """
          AND NOT EXISTS (
              SELECT 1 FROM graph_nodes AS noise_node
              WHERE noise_node.id IN (
                  graph_edges.source_node_id,
                  graph_edges.target_node_id
              )
                AND noise_node.kind IN ('asset', 'content_kind')
          )
        """
        if hide_noise
        else ""
    )

    if node_ids is None:
        edge_rows = connection.execute(
            f"""
            SELECT id, source_node_id, target_node_id, kind, weight
            FROM graph_edges
            WHERE EXISTS (
                SELECT 1 FROM graph_edge_evidence
                WHERE graph_edge_evidence.edge_id = graph_edges.id
                  AND evidence_kind IN ('message', 'message_part')
            )
            {noise_clause}
            ORDER BY weight DESC, id
            LIMIT ?
            """,
            (candidate_limit,),
        ).fetchall()
    else:
        placeholders = ", ".join("?" for _item in node_ids)
        edge_rows = connection.execute(
            f"""
            SELECT id, source_node_id, target_node_id, kind, weight
            FROM graph_edges
            WHERE (
                source_node_id IN ({placeholders})
                OR target_node_id IN ({placeholders})
            )
              AND EXISTS (
                SELECT 1 FROM graph_edge_evidence
                WHERE graph_edge_evidence.edge_id = graph_edges.id
                  AND evidence_kind IN ('message', 'message_part')
              )
              {noise_clause}
            ORDER BY weight DESC, id
            LIMIT ?
            """,
            (*node_ids, *node_ids, candidate_limit),
        ).fetchall()
    edge_rows, included_node_ids = _bounded_graph_rows(
        edge_rows,
        node_limit=node_limit,
    )
    if node_ids is not None:
        for node_id in sorted(node_ids):
            if len(included_node_ids) >= node_limit:
                break
            included_node_ids.add(node_id)
    nodes = _load_nodes(connection, included_node_ids)
    citations_by_edge = _load_graph_edge_citations(
        connection,
        edge_ids=(str(row[0]) for row in edge_rows),
        limit=max(0, evidence_limit),
    )
    edges: list[dict[str, Any]] = []
    for row in edge_rows:
        edge_id = str(row[0])
        evidence_rows, evidence_count = citations_by_edge.get(edge_id, ([], 0))
        edges.append(
            {
                "id": edge_id,
                "sourceNodeId": str(row[1]),
                "targetNodeId": str(row[2]),
                "kind": str(row[3]),
                "weight": int(row[4]),
                "evidenceCount": evidence_count,
                "evidence": evidence_rows,
            }
        )
    return {"nodes": nodes, "edges": edges}


def _bounded_graph_rows(
    rows: Iterable[sqlite3.Row],
    *,
    node_limit: int,
) -> tuple[list[sqlite3.Row], set[str]]:
    selected: list[sqlite3.Row] = []
    node_ids: set[str] = set()
    for row in rows:
        endpoints = {str(row[1]), str(row[2])}
        if len(node_ids | endpoints) > node_limit:
            continue
        selected.append(row)
        node_ids.update(endpoints)
    return selected, node_ids


def _load_graph_edge_citations(
    connection: sqlite3.Connection,
    *,
    edge_ids: Iterable[str],
    limit: int,
) -> dict[str, tuple[list[dict[str, Any]], int]]:
    normalized_ids = tuple(dict.fromkeys(str(item) for item in edge_ids))
    result: dict[str, tuple[list[dict[str, Any]], int]] = {
        edge_id: ([], 0) for edge_id in normalized_ids
    }
    preview_limit = max(0, limit)
    query_limit = max(1, preview_limit)

    for offset in range(0, len(normalized_ids), _GRAPH_EVIDENCE_BATCH_SIZE):
        batch = normalized_ids[offset : offset + _GRAPH_EVIDENCE_BATCH_SIZE]
        placeholders = ", ".join("?" for _item in batch)
        rows = connection.execute(
            f"""
            WITH evidence_rows AS (
                SELECT
                    evidence.edge_id AS edge_id,
                    evidence.evidence_kind AS evidence_kind,
                    evidence.evidence_id AS evidence_id,
                    messages.conversation_id AS conversation_id,
                    messages.id AS message_id,
                    NULL AS message_part_id,
                    messages.sent_at AS sent_at,
                    messages.sender_name AS sender_name,
                    messages.content AS snippet,
                    messages.type AS message_type
                FROM graph_edge_evidence AS evidence
                JOIN messages
                  ON evidence.evidence_kind = 'message'
                 AND messages.id = CAST(evidence.evidence_id AS INTEGER)
                 AND evidence.evidence_id = CAST(messages.id AS TEXT)
                WHERE evidence.edge_id IN ({placeholders})
                UNION ALL
                SELECT
                    evidence.edge_id,
                    evidence.evidence_kind,
                    evidence.evidence_id,
                    messages.conversation_id,
                    messages.id,
                    message_parts.id,
                    messages.sent_at,
                    messages.sender_name,
                    CASE
                        WHEN message_parts.text_content <> ''
                            THEN message_parts.text_content
                        ELSE messages.content
                    END,
                    messages.type
                FROM graph_edge_evidence AS evidence
                JOIN message_parts
                  ON evidence.evidence_kind = 'message_part'
                 AND message_parts.id = CAST(evidence.evidence_id AS INTEGER)
                 AND evidence.evidence_id = CAST(message_parts.id AS TEXT)
                JOIN messages ON messages.id = message_parts.message_id
                WHERE evidence.edge_id IN ({placeholders})
            ),
            ranked AS (
                SELECT
                    evidence_rows.*,
                    COUNT(*) OVER (
                        PARTITION BY edge_id
                    ) AS evidence_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY edge_id
                        ORDER BY sent_at DESC, evidence_kind, evidence_id
                    ) AS evidence_rank
                FROM evidence_rows
            )
            SELECT
                edge_id,
                evidence_kind,
                evidence_id,
                conversation_id,
                message_id,
                message_part_id,
                sent_at,
                sender_name,
                snippet,
                message_type,
                evidence_count,
                evidence_rank
            FROM ranked
            WHERE evidence_rank <= ?
            ORDER BY edge_id, evidence_rank
            """,
            (*batch, *batch, query_limit),
        ).fetchall()

        for row in rows:
            edge_id = str(row[0])
            citations, _count = result[edge_id]
            evidence_count = int(row[10])
            if preview_limit > 0 and int(row[11]) <= preview_limit:
                _message_type, safe_snippet = safe_message_display(
                    row[9],
                    row[8],
                )
                citations.append(
                    {
                        "kind": str(row[1]),
                        "id": str(row[2]),
                        "citation": {
                            "conversationId": str(row[3]),
                            "messageId": int(row[4]),
                            "messagePartId": (
                                int(row[5]) if row[5] is not None else None
                            ),
                            "sentAt": int(row[6]),
                            "senderName": str(row[7]),
                            "snippet": _snippet(safe_snippet),
                        },
                    }
                )
            result[edge_id] = (citations, evidence_count)

    return result




def get_edge_evidence(
    connection: sqlite3.Connection,
    edge_id: str,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    exists = connection.execute(
        "SELECT 1 FROM graph_edges WHERE id = ?",
        (edge_id,),
    ).fetchone()
    if exists is None:
        raise GraphEdgeNotFound(edge_id)
    items, evidence_count = _load_edge_citations(
        connection,
        edge_id=edge_id,
        limit=limit,
    )
    return {"edgeId": edge_id, "evidenceCount": evidence_count, "items": items}

def _load_edge_citations(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = connection.execute(
        """
        SELECT
            evidence.evidence_kind AS evidence_kind,
            evidence.evidence_id AS evidence_id,
            messages.conversation_id AS conversation_id,
            messages.id AS message_id,
            NULL AS message_part_id,
            messages.sent_at AS sent_at,
            messages.sender_name AS sender_name,
            messages.content AS snippet,
            messages.type AS message_type
        FROM graph_edge_evidence AS evidence
        JOIN messages
          ON evidence.evidence_kind = 'message'
         AND messages.id = CAST(evidence.evidence_id AS INTEGER)
         AND evidence.evidence_id = CAST(messages.id AS TEXT)
        WHERE evidence.edge_id = ?
        UNION ALL
        SELECT
            evidence.evidence_kind,
            evidence.evidence_id,
            messages.conversation_id,
            messages.id,
            message_parts.id,
            messages.sent_at,
            messages.sender_name,
            CASE
                WHEN message_parts.text_content <> ''
                    THEN message_parts.text_content
                ELSE messages.content
            END,
            messages.type
        FROM graph_edge_evidence AS evidence
        JOIN message_parts
          ON evidence.evidence_kind = 'message_part'
         AND message_parts.id = CAST(evidence.evidence_id AS INTEGER)
         AND evidence.evidence_id = CAST(message_parts.id AS TEXT)
        JOIN messages ON messages.id = message_parts.message_id
        WHERE evidence.edge_id = ?
        ORDER BY sent_at DESC, evidence_kind, evidence_id
        LIMIT ?
        """,
        (edge_id, edge_id, limit),
    ).fetchall()
    count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT evidence.evidence_id
                FROM graph_edge_evidence AS evidence
                JOIN messages
                  ON evidence.evidence_kind = 'message'
                 AND messages.id = CAST(evidence.evidence_id AS INTEGER)
                 AND evidence.evidence_id = CAST(messages.id AS TEXT)
                WHERE evidence.edge_id = ?
                UNION ALL
                SELECT evidence.evidence_id
                FROM graph_edge_evidence AS evidence
                JOIN message_parts
                  ON evidence.evidence_kind = 'message_part'
                 AND message_parts.id = CAST(evidence.evidence_id AS INTEGER)
                 AND evidence.evidence_id = CAST(message_parts.id AS TEXT)
                JOIN messages ON messages.id = message_parts.message_id
                WHERE evidence.edge_id = ?
            )
            """,
            (edge_id, edge_id),
        ).fetchone()[0]
    )
    citations = []
    for row in rows:
        _message_type, safe_snippet = safe_message_display(row[8], row[7])
        citations.append({
            "kind": str(row[0]),
            "id": str(row[1]),
            "citation": {
                "conversationId": str(row[2]),
                "messageId": int(row[3]),
                "messagePartId": int(row[4]) if row[4] is not None else None,
                "sentAt": int(row[5]),
                "senderName": str(row[6]),
                "snippet": _snippet(safe_snippet),
            },
        })
    return citations, count


def _snippet(value: str, *, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "\u2026"


def _delete_disabled_conversation_graph(
    connection: sqlite3.Connection,
    condition: str,
    parameters: tuple[str, ...],
) -> None:
    conversation_ids = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT conversations.id
            FROM conversations
            WHERE {condition}
              AND COALESCE(
                    (SELECT o.is_indexing_enabled FROM conversation_overrides o
                     WHERE o.conversation_id = conversations.id),
                    conversations.is_indexing_enabled, 1) = 0
            """,
            parameters,
        )
    ]
    if not conversation_ids:
        return
    node_ids = [_stable_id("conversation", item) for item in conversation_ids]
    placeholders = ", ".join("?" for _item in node_ids)
    edge_ids = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT id FROM graph_edges
            WHERE source_node_id IN ({placeholders})
               OR target_node_id IN ({placeholders})
            """,
            (*node_ids, *node_ids),
        )
    ]
    if edge_ids:
        _delete_in_batches(connection, "graph_edge_evidence", "edge_id", edge_ids)
        _delete_in_batches(connection, "graph_edges", "id", edge_ids)
    connection.execute(
        f"DELETE FROM graph_nodes WHERE id IN ({placeholders})",
        node_ids,
    )


def _delete_unsupported_content_graph(connection: sqlite3.Connection) -> None:
    node_ids = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT id
            FROM graph_nodes
            WHERE kind = 'content_kind'
              AND trace_kind = 'content_kind'
              AND trace_id = 'unsupported'
            """
        )
    ]
    if not node_ids:
        return
    placeholders = ", ".join("?" for _item in node_ids)
    edge_ids = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT id
            FROM graph_edges
            WHERE source_node_id IN ({placeholders})
               OR target_node_id IN ({placeholders})
            """,
            (*node_ids, *node_ids),
        )
    ]
    if edge_ids:
        _delete_in_batches(connection, "graph_edge_evidence", "edge_id", edge_ids)
        _delete_in_batches(connection, "graph_edges", "id", edge_ids)
    connection.execute(
        f"DELETE FROM graph_nodes WHERE id IN ({placeholders})",
        node_ids,
    )


def _link_domain(text_content: str, metadata_json: str) -> str | None:
    try:
        metadata = json.loads(metadata_json)
    except (TypeError, ValueError):
        metadata = {}
    if isinstance(metadata, dict):
        raw_domain = metadata.get("domain")
        if isinstance(raw_domain, str):
            normalized = _normalize_domain(raw_domain)
            if normalized is not None:
                return normalized
        raw_url = metadata.get("url")
        if isinstance(raw_url, str):
            normalized = _domain_from_url(raw_url)
            if normalized is not None:
                return normalized

    match = _URL_PATTERN.search(text_content)
    if match is None:
        return None
    return _domain_from_url(match.group(0).rstrip(".,;:!?)]}"))


def _domain_from_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    return _normalize_domain(parsed.hostname)


def _normalize_domain(value: str) -> str | None:
    candidate = value.strip().casefold().rstrip(".")
    if not candidate or any(character.isspace() for character in candidate):
        return None
    parsed = urlsplit(f"//{candidate}")
    if parsed.hostname != candidate:
        return None
    return candidate
def _graph_scope_node_ids(
    connection: sqlite3.Connection,
    *,
    source_app_id: str | None,
    source_account_id: str | None,
    conversation_id: str | None,
) -> set[str] | None:
    if source_app_id is None and source_account_id is None and conversation_id is None:
        return None
    clauses: list[str] = []
    parameters: list[str] = []
    if source_app_id is not None:
        clauses.append("source_accounts.source_app_id = ?")
        parameters.append(source_app_id)
    if source_account_id is not None:
        clauses.append("conversation_origins.source_account_id = ?")
        parameters.append(source_account_id)
    if conversation_id is not None:
        clauses.append("conversations.id = ?")
        parameters.append(conversation_id)
    rows = connection.execute(
        f"""
        SELECT conversations.id
        FROM conversations
        JOIN conversation_origins
          ON conversation_origins.conversation_id = conversations.id
        JOIN source_accounts
          ON source_accounts.id = conversation_origins.source_account_id
        WHERE {' AND '.join(clauses)}
        """,
        parameters,
    ).fetchall()
    conversation_ids = [str(row[0]) for row in rows]
    if not conversation_ids:
        return set()
    return {_stable_id("conversation", item) for item in conversation_ids}


def _load_nodes(
    connection: sqlite3.Connection,
    node_ids: set[str],
) -> list[dict[str, Any]]:
    if not node_ids:
        return []
    placeholders = ", ".join("?" for _item in node_ids)
    rows = connection.execute(
        f"""
        SELECT id, kind, label, trace_kind, trace_id
        FROM graph_nodes
        WHERE id IN ({placeholders})
        ORDER BY kind, label, id
        """,
        tuple(sorted(node_ids)),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "kind": str(row[1]),
            "label": str(row[2]),
            "traceKind": str(row[3]),
            "traceId": str(row[4]),
        }
        for row in rows
    ]


def _upsert_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    kind: str,
    label: str,
    trace_kind: str,
    trace_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO graph_nodes(id, kind, label, trace_kind, trace_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label = excluded.label,
            trace_kind = excluded.trace_kind,
            trace_id = excluded.trace_id
        """,
        (node_id, kind, label, trace_kind, trace_id),
    )


def _upsert_edge(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    source_node_id: str,
    target_node_id: str,
    kind: str,
    weight: int,
) -> None:
    connection.execute(
        """
        INSERT INTO graph_edges(
            id,
            source_node_id,
            target_node_id,
            kind,
            weight
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET weight = excluded.weight
        """,
        (edge_id, source_node_id, target_node_id, kind, weight),
    )


def _add_evidence(
    connection: sqlite3.Connection,
    edge_id: str,
    evidence_kind: str,
    evidence_id: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO graph_edge_evidence(
            edge_id,
            evidence_kind,
            evidence_id
        ) VALUES (?, ?, ?)
        """,
        (edge_id, evidence_kind, evidence_id),
    )


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"


def _normalize_scope(
    conversation_ids: Iterable[str] | None,
) -> tuple[str, ...] | None:
    if conversation_ids is None:
        return None
    return tuple(dict.fromkeys(str(item) for item in conversation_ids))


def _condition(
    column: str,
    scope: tuple[str, ...] | None,
) -> tuple[str, tuple[str, ...]]:
    if scope is None:
        return "1 = 1", ()
    if not scope:
        return "0 = 1", ()
    placeholders = ", ".join("?" for _item in scope)
    return f"{column} IN ({placeholders})", scope
