from __future__ import annotations

from contextlib import closing
from pathlib import Path

from wechat_ai.database import initialize_database
from wechat_ai.services.graph_service import (
    get_edge_evidence,
    get_graph,
    project_graph,
)
from wechat_ai.services.knowledge_projection_service import project_knowledge


def test_graph_exposes_only_message_backed_edges_and_projects_link_domains(
    tmp_path: Path,
) -> None:
    with closing(initialize_database(tmp_path / "library.sqlite")) as connection:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('team', 'team', 'Team', 'group')
            """
        )
        message_id = connection.execute(
            """
            INSERT INTO messages(
                conversation_id,
                dedupe_key,
                sender_id,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "team",
                "team:1",
                "alice",
                "Alice",
                1_000,
                "text",
                "Launch brief https://docs.example.com/brief",
            ),
        ).lastrowid

        project_knowledge(connection)
        project_graph(connection)
        graph = get_graph(connection)

        assert any(
            node["kind"] == "link_domain"
            and node["label"] == "docs.example.com"
            and node["traceKind"] == "link_domain"
            for node in graph["nodes"]
        )
        assert graph["edges"]
        assert {edge["kind"] for edge in graph["edges"]} >= {
            "participates_in",
            "has_content",
            "links_to",
        }
        assert all(edge["evidenceCount"] >= 1 for edge in graph["edges"])
        assert all(edge["evidence"] for edge in graph["edges"])
        assert all(
            evidence["citation"]["conversationId"] == "team"
            and evidence["citation"]["messageId"] == int(message_id)
            and evidence["citation"]["sentAt"] == 1_000
            and evidence["citation"]["snippet"].startswith("Launch brief")
            for edge in graph["edges"]
            for evidence in edge["evidence"]
        )
        assert all(
            evidence["kind"] in {"message", "message_part"}
            for edge in graph["edges"]
            for evidence in edge["evidence"]
        )

        structural_edge_kinds = {
            row[0]
            for row in connection.execute(
                """
                SELECT graph_edges.kind
                FROM graph_edges
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM graph_edge_evidence
                    WHERE graph_edge_evidence.edge_id = graph_edges.id
                      AND graph_edge_evidence.evidence_kind IN (
                          'message', 'message_part'
                      )
                )
                """
            )
        }
        assert "contains" in structural_edge_kinds
        assert "contains" not in {edge["kind"] for edge in graph["edges"]}


def test_graph_does_not_project_unsupported_content_or_pseudo_assets(
    tmp_path: Path,
) -> None:
    with closing(initialize_database(tmp_path / "library.sqlite")) as connection:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('team', 'team', 'Team', 'group')
            """
        )
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES (
                'team', 'team:unsupported', 'Alice',
                1000, 'unsupported_999', '[不支持的消息类型: 999]'
            )
            """
        )

        project_knowledge(connection)
        connection.execute(
            """
            INSERT INTO graph_nodes(
                id, kind, label, trace_kind, trace_id
            ) VALUES (
                'legacy-unsupported', 'content_kind', 'unsupported',
                'content_kind', 'unsupported'
            )
            """
        )
        project_graph(connection)

        nodes = [
            tuple(row)
            for row in connection.execute(
                "SELECT kind, label, trace_kind FROM graph_nodes"
            )
        ]
        edge_kinds = {
            str(row[0])
            for row in connection.execute("SELECT kind FROM graph_edges")
        }
        assert ("content_kind", "unsupported", "content_kind") not in nodes
        assert all(kind != "asset" for kind, _label, _trace_kind in nodes)
        assert "has_content" not in edge_kinds
        assert "has_asset" not in edge_kinds


def test_graph_evidence_fallback_sanitizes_unsupported_type_numbers_everywhere(
    tmp_path: Path,
) -> None:
    with closing(initialize_database(tmp_path / "library.sqlite")) as connection:
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind
            ) VALUES ('team', 'team', 'Team', 'group')
            """
        )
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('team', 'team:text', 'Alice', 1000, 'text', 'supported')
            """
        )
        unsupported_message_id = int(
            connection.execute(
                """
                INSERT INTO messages(
                    conversation_id, dedupe_key, sender_name,
                    sent_at, type, content
                ) VALUES (
                    'team', 'team:unsupported', 'Alice',
                    2000, 'unsupported_999', '[不支持的消息类型: 999]'
                )
                """
            ).lastrowid
        )

        project_knowledge(connection)
        project_graph(connection)
        unsupported_part_id = int(
            connection.execute(
                "SELECT id FROM message_parts WHERE message_id = ?",
                (unsupported_message_id,),
            ).fetchone()[0]
        )
        participant_edge_id = str(
            connection.execute(
                "SELECT id FROM graph_edges WHERE kind = 'participates_in'"
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO graph_edge_evidence(
                edge_id, evidence_kind, evidence_id
            ) VALUES (?, 'message_part', ?)
            """,
            (participant_edge_id, str(unsupported_part_id)),
        )

        preview = next(
            edge
            for edge in get_graph(connection, evidence_limit=20)["edges"]
            if edge["id"] == participant_edge_id
        )["evidence"]
        detail = get_edge_evidence(
            connection,
            participant_edge_id,
            limit=20,
        )["items"]

        for evidence in (preview, detail):
            unsupported_snippets = {
                item["citation"]["snippet"]
                for item in evidence
                if item["citation"]["messageId"] == unsupported_message_id
            }
            assert unsupported_snippets == {"暂无法预览此消息"}
            assert all("999" not in snippet for snippet in unsupported_snippets)
