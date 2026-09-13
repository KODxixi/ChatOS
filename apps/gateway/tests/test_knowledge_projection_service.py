from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from wechat_ai.database import initialize_database
from wechat_ai.services.knowledge_projection_service import project_knowledge


def _insert_conversation_message(
    connection: sqlite3.Connection,
    *,
    conversation_id: str,
    message_type: str = "text",
    content: str,
    sent_at: int,
) -> int:
    connection.execute(
        """
        INSERT INTO conversations(
            id,
            source_conversation_id,
            title,
            kind
        ) VALUES (?, ?, ?, ?)
        """,
        (
            conversation_id,
            conversation_id,
            conversation_id.title(),
            "direct",
        ),
    )
    return int(
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id,
                dedupe_key,
                sender_name,
                sent_at,
                type,
                content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                f"{conversation_id}:1",
                conversation_id.title(),
                sent_at,
                message_type,
                content,
            ),
        ).lastrowid
    )


def _expected_hash(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\x1f{text}".encode("utf-8")).hexdigest()


def test_global_projection_has_incremental_hash_and_never_binds_current_chat(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    with closing(initialize_database(database_path)) as connection:
        alpha_message_id = _insert_conversation_message(
            connection,
            conversation_id="alpha",
            content="alpha knowledge",
            sent_at=1_000,
        )
        _insert_conversation_message(
            connection,
            conversation_id="beta",
            content="beta knowledge",
            sent_at=2_000,
        )

        global_result = project_knowledge(connection)
        chunks = connection.execute(
            """
            SELECT conversation_id, text_content, content_hash, index_version
            FROM retrieval_chunks
            ORDER BY conversation_id
            """
        ).fetchall()

        assert global_result.retrieval_chunks == 2
        assert [tuple(row) for row in chunks] == [
            (
                "alpha",
                "alpha knowledge",
                _expected_hash("text", "alpha knowledge"),
                1,
            ),
            (
                "beta",
                "beta knowledge",
                _expected_hash("text", "beta knowledge"),
                1,
            ),
        ]

        beta_hash_before = chunks[1][2]
        connection.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("alpha revised", alpha_message_id),
        )
        project_knowledge(connection, conversation_ids=("alpha",))
        project_knowledge(connection, conversation_ids=())

        updated = connection.execute(
            """
            SELECT conversation_id, text_content, content_hash
            FROM retrieval_chunks
            ORDER BY conversation_id
            """
        ).fetchall()
        assert [tuple(row) for row in updated] == [
            (
                "alpha",
                "alpha revised",
                _expected_hash("text", "alpha revised"),
            ),
            ("beta", "beta knowledge", beta_hash_before),
        ]


def test_projection_classifies_links_files_and_preserves_resolved_media(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    with closing(initialize_database(database_path)) as connection:
        _insert_conversation_message(
            connection,
            conversation_id="text-link",
            message_type="text",
            content="Launch notes https://docs.example.com/launch?id=7",
            sent_at=1_000,
        )
        _insert_conversation_message(
            connection,
            conversation_id="explicit-link",
            message_type="link",
            content="Design spec\nhttps://example.org/spec",
            sent_at=2_000,
        )
        _insert_conversation_message(
            connection,
            conversation_id="image",
            message_type="image",
            content="[??]",
            sent_at=3_000,
        )
        _insert_conversation_message(
            connection,
            conversation_id="file",
            message_type="file",
            content="roadmap.pdf",
            sent_at=4_000,
        )

        project_knowledge(connection)

        parts = connection.execute(
            """
            SELECT messages.conversation_id,
                   message_parts.kind,
                   message_parts.status,
                   message_parts.text_content,
                   message_parts.metadata_json
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            ORDER BY messages.conversation_id
            """
        ).fetchall()
        assert [tuple(row[:4]) for row in parts] == [
            ("explicit-link", "link", "resolved", "Design spec\nhttps://example.org/spec"),
            ("file", "file", "resolved", "roadmap.pdf"),
            ("image", "image", "unresolved", ""),
            (
                "text-link",
                "link",
                "resolved",
                "Launch notes https://docs.example.com/launch?id=7",
            ),
        ]
        assert '"url":"https://docs.example.com/launch?id=7"' in parts[3][4]
        assert '"domain":"docs.example.com"' in parts[3][4]
        asset_conversations = connection.execute(
            """
            SELECT messages.conversation_id
            FROM assets
            JOIN message_parts ON message_parts.id = assets.part_id
            JOIN messages ON messages.id = message_parts.message_id
            ORDER BY messages.conversation_id
            """
        ).fetchall()
        assert [row[0] for row in asset_conversations] == ["file", "image"]


        indexed = connection.execute(
            """
            SELECT conversations.id, retrieval_chunks.text_content
            FROM retrieval_chunks
            JOIN conversations
              ON conversations.id = retrieval_chunks.conversation_id
            ORDER BY conversations.id
            """
        ).fetchall()
        assert [tuple(row) for row in indexed] == [
            ("explicit-link", "Design spec\nhttps://example.org/spec"),
            ("file", "roadmap.pdf"),
            (
                "text-link",
                "Launch notes https://docs.example.com/launch?id=7",
            ),
        ]

        image_part = connection.execute(
            """
            SELECT message_parts.id
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            WHERE messages.conversation_id = 'image'
            """
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE message_parts
            SET status = 'resolved',
                text_content = 'whiteboard OCR',
                metadata_json = '{"ocr":"complete"}'
            WHERE id = ?
            """,
            (image_part,),
        )
        connection.execute(
            """
            UPDATE assets
            SET status = 'resolved',
                file_name = 'whiteboard.png',
                local_path = 'images/whiteboard.png',
                metadata_json = '{"width":1200}'
            WHERE part_id = ?
            """,
            (image_part,),
        )

        project_knowledge(connection)

        preserved = connection.execute(
            """
            SELECT message_parts.status,
                   message_parts.text_content,
                   message_parts.metadata_json,
                   assets.status,
                   assets.file_name,
                   assets.local_path,
                   assets.metadata_json
            FROM message_parts
            JOIN assets ON assets.part_id = message_parts.id
            WHERE message_parts.id = ?
            """,
            (image_part,),
        ).fetchone()
        assert tuple(preserved) == (
            "resolved",
            "whiteboard OCR",
            '{"ocr":"complete"}',
            "resolved",
            "whiteboard.png",
            "images/whiteboard.png",
            '{"width":1200}',
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE message_part_id = ?",
            (image_part,),
        ).fetchone()[0] == 1


def test_unsupported_messages_remain_unresolved_without_derived_knowledge(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    with closing(initialize_database(database_path)) as connection:
        message_id = _insert_conversation_message(
            connection,
            conversation_id="unsupported",
            message_type="unsupported_999",
            content="[不支持的消息类型: 999]",
            sent_at=1_000,
        )

        result = project_knowledge(connection)

        part = connection.execute(
            """
            SELECT id, kind, status, text_content
            FROM message_parts
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        assert part is not None
        assert tuple(part[1:]) == ("unsupported", "unresolved", "")
        assert connection.execute(
            "SELECT COUNT(*) FROM assets WHERE part_id = ?",
            (int(part[0]),),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_chunks WHERE message_part_id = ?",
            (int(part[0]),),
        ).fetchone()[0] == 0
        assert result.message_parts == 1
        assert result.assets == 0
        assert result.retrieval_chunks == 0


def test_projection_repairs_legacy_unsupported_asset_kind(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "library.sqlite"
    with closing(initialize_database(database_path)) as connection:
        message_id = _insert_conversation_message(
            connection,
            conversation_id="attachment",
            message_type="attachment",
            content="[附件]",
            sent_at=1_000,
        )
        project_knowledge(connection)
        connection.execute(
            """
            UPDATE assets
            SET kind = 'unsupported'
            WHERE part_id = (
                SELECT id FROM message_parts WHERE message_id = ?
            )
            """,
            (message_id,),
        )

        project_knowledge(connection)

        assert connection.execute(
            """
            SELECT assets.kind
            FROM assets
            JOIN message_parts ON message_parts.id = assets.part_id
            WHERE message_parts.message_id = ?
            """,
            (message_id,),
        ).fetchone()[0] == "attachment"
