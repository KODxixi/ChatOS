from __future__ import annotations

import sqlite3

from fixtures.synthetic_sqlite import create_synthetic_sources
from wechat_ai import wechat_db
from wechat_ai.services.knowledge_projection_service import project_knowledge


def test_repeat_import_repairs_previously_exposed_binary_text(tmp_path) -> None:
    fixtures = create_synthetic_sources(tmp_path / "sources")
    table_name = wechat_db._message_table_name(fixtures.group_user_name)
    with sqlite3.connect(fixtures.message_shard) as source:
        source.execute(
            f'UPDATE "{table_name}" SET message_content = ? WHERE local_id = 1',
            (sqlite3.Binary(b"\xff\xfe\x00\x01binary"),),
        )
        source.commit()

    library = tmp_path / "library.sqlite"
    wechat_db.import_sources(fixtures.root, library)
    with sqlite3.connect(library) as connection:
        connection.execute(
            """
            UPDATE messages
            SET type = 'text', content = ?
            WHERE source_message_id = '9001'
            """,
            ("\ufffd\ufffd\u0000\u0001binary",),
        )
        project_knowledge(connection)
        connection.execute(
            """
            UPDATE message_parts
            SET kind = 'unsupported'
            WHERE message_id = (
                SELECT id FROM messages WHERE source_message_id = '9001'
            )
            """
        )
        connection.commit()

    repeated = wechat_db.import_sources(fixtures.root, library)
    with sqlite3.connect(library) as connection:
        repaired = connection.execute(
            """
            SELECT type, content
            FROM messages
            WHERE source_message_id = '9001'
            """
        ).fetchone()
        repaired_part = connection.execute(
            """
            SELECT message_parts.kind, message_parts.status, message_parts.text_content,
                   COUNT(retrieval_chunks.id)
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            LEFT JOIN retrieval_chunks
                ON retrieval_chunks.message_part_id = message_parts.id
            WHERE messages.source_message_id = '9001'
            GROUP BY message_parts.id
            """
        ).fetchone()

    assert repeated.inserted_messages == 0
    assert repaired == ("unsupported_1", "[\u6587\u672c\u5185\u5bb9\u4e0d\u53ef\u8bfb]")
    assert repaired_part == ("unsupported", "unresolved", "", 0)
