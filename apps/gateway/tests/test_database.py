from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_database_module():
    try:
        return importlib.import_module("wechat_ai.database")
    except ModuleNotFoundError as exc:
        raise AssertionError("wechat_ai.database has not been implemented") from exc


class DatabaseTests(unittest.TestCase):
    def test_initialize_database_creates_minimum_schema_and_pragmas(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "library" / "wechat-ai.sqlite"
            with closing(database.initialize_database(db_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type IN ('table', 'view')"
                    )
                }

                self.assertTrue(
                    {
                        "conversations",
                        "messages",
                        "message_fts",
                        "app_meta",
                        "source_apps",
                        "source_accounts",
                        "conversation_origins",
                        "message_parts",
                        "retrieval_chunks",
                        "retrieval_chunk_fts",
                        "conversation_groups",
                    }
                    <= tables
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM app_meta WHERE key = 'schema_version'"
                    ).fetchone()[0],
                        "22",
                )

    def test_initialize_database_is_idempotent_and_preserves_local_data(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "wechat-ai.sqlite"
            first = database.initialize_database(db_path)
            first.execute(
                """
                INSERT INTO conversations (
                    id,
                    source_conversation_id,
                    title,
                    kind,
                    is_favorite,
                    note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("alice", "alice", "Alice", "direct", 1, "keep this"),
            )
            first.commit()
            first.close()

            with closing(database.initialize_database(db_path)) as second:
                row = second.execute(
                    "SELECT is_favorite, note FROM conversations WHERE id = ?",
                    ("alice",),
                ).fetchone()
                self.assertEqual(tuple(row), (1, "keep this"))

    def test_upgrade_v1_backfills_wechat_origin_without_changing_local_data(
        self,
    ) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "wechat-ai.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    (database.MIGRATIONS_DIR / "001_initial.sql").read_text(
                        encoding="utf-8"
                    )
                )
                connection.execute(
                    """
                    INSERT INTO app_meta(key, value)
                    VALUES ('schema_version', '1')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        source_conversation_id,
                        title,
                        kind,
                        is_favorite,
                        note
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "project@chatroom",
                        "project@chatroom",
                        "Project Room",
                        "group",
                        1,
                        "keep this",
                    ),
                )
                cursor = connection.execute(
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
                        "project@chatroom",
                        "9001",
                        "shard:table:server:9001",
                        "alice",
                        "Alice",
                        1_700_000_000_000,
                        "text",
                        "launch checklist",
                    ),
                )
                message_id = int(cursor.lastrowid)
                connection.commit()
            finally:
                connection.close()

            with closing(database.initialize_database(db_path)) as upgraded:
                self.assertEqual(
                    upgraded.execute(
                        "SELECT value FROM app_meta WHERE key = 'schema_version'"
                    ).fetchone()[0],
                    "22",
                )
                self.assertEqual(
                    tuple(
                        upgraded.execute(
                            """
                            SELECT source_account_id, external_conversation_id
                            FROM conversation_origins
                            WHERE conversation_id = ?
                            """,
                            ("project@chatroom",),
                        ).fetchone()
                    ),
                    ("wechat:default", "project@chatroom"),
                )
                self.assertEqual(
                    tuple(
                        upgraded.execute(
                            """
                            SELECT id, is_favorite, note
                            FROM conversations
                            WHERE id = ?
                            """,
                            ("project@chatroom",),
                        ).fetchone()
                    ),
                    ("project@chatroom", 1, "keep this"),
                )
                self.assertEqual(
                    tuple(
                        upgraded.execute(
                            """
                            SELECT message_id, kind, status, text_content
                            FROM message_parts
                            WHERE message_id = ?
                            """,
                            (message_id,),
                        ).fetchone()
                    ),
                    (message_id, "text", "resolved", "launch checklist"),
                )
                self.assertEqual(
                    upgraded.execute(
                        """
                        SELECT text_content
                        FROM retrieval_chunk_fts
                        WHERE retrieval_chunk_fts MATCH 'launch'
                        """
                    ).fetchone()[0],
                    "launch checklist",
                )
                content_hash, index_version = upgraded.execute(
                    """
                    SELECT content_hash, index_version
                    FROM retrieval_chunks
                    WHERE message_id = ?
                    """,
                    (message_id,),
                ).fetchone()
                self.assertRegex(content_hash, r"^[0-9a-f]{64}$")
                self.assertEqual(index_version, 1)
                self.assertGreater(
                    upgraded.execute(
                        """
                        SELECT COUNT(*)
                        FROM graph_edges
                        JOIN graph_edge_evidence
                          ON graph_edge_evidence.edge_id = graph_edges.id
                        WHERE graph_edge_evidence.evidence_kind IN (
                            'message', 'message_part'
                        )
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    upgraded.execute(
                        """
                        SELECT content
                        FROM message_fts
                        WHERE message_fts MATCH 'launch'
                        """
                    ).fetchone()[0],
                    "launch checklist",
                )

    def test_asset_rows_require_message_parts_and_are_repeat_safe(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with closing(
                database.initialize_database(Path(temp_dir) / "wechat-ai.sqlite")
            ) as connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        source_conversation_id,
                        title,
                        kind
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("alice", "alice", "Alice", "direct"),
                )
                message_id = int(
                    connection.execute(
                        """
                        INSERT INTO messages (
                            conversation_id,
                            dedupe_key,
                            sent_at,
                            type,
                            content
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            "alice",
                            "asset-message",
                            1_700_000_002_000,
                            "image",
                            "[image]",
                        ),
                    ).lastrowid
                )
                part_id = int(
                    connection.execute(
                        """
                        INSERT INTO message_parts (
                            message_id,
                            ordinal,
                            kind,
                            status
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (message_id, 0, "image", "unresolved"),
                    ).lastrowid
                )

                connection.execute(
                    """
                    INSERT INTO assets(part_id, ordinal, kind, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (part_id, 0, "image", "unresolved"),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO assets(part_id, ordinal, kind, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        (part_id, 0, "image", "unresolved"),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO assets(part_id, ordinal, kind, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        (999_999, 0, "image", "unresolved"),
                    )

                row = connection.execute(
                    """
                    SELECT part_id, ordinal, kind, status,
                           file_name, mime_type, byte_size,
                           external_url, local_path, sha256
                    FROM assets
                    WHERE part_id = ?
                    """,
                    (part_id,),
                ).fetchone()
                self.assertEqual(
                    tuple(row),
                    (
                        part_id,
                        0,
                        "image",
                        "unresolved",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                )

    def test_graph_edges_require_nodes_and_keep_traceable_evidence(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with closing(
                database.initialize_database(Path(temp_dir) / "wechat-ai.sqlite")
            ) as connection:
                connection.executemany(
                    """
                    INSERT INTO graph_nodes(
                        id,
                        kind,
                        label,
                        trace_kind,
                        trace_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "source_app|wechat",
                            "source_app",
                            "WeChat",
                            "source_app",
                            "wechat",
                        ),
                        (
                            "source_account|wechat:default",
                            "source_account",
                            "WeChat",
                            "source_account",
                            "wechat:default",
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO graph_edges(
                        id,
                        source_node_id,
                        target_node_id,
                        kind,
                        weight
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "contains|wechat|wechat:default",
                        "source_app|wechat",
                        "source_account|wechat:default",
                        "contains",
                        1,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO graph_edge_evidence(
                        edge_id,
                        evidence_kind,
                        evidence_id
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "contains|wechat|wechat:default",
                        "source_account",
                        "wechat:default",
                    ),
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO graph_edge_evidence(
                            edge_id,
                            evidence_kind,
                            evidence_id
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            "contains|wechat|wechat:default",
                            "source_account",
                            "wechat:default",
                        ),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO graph_edges(
                            id,
                            source_node_id,
                            target_node_id,
                            kind,
                            weight
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("orphan", "missing", "missing", "contains", 1),
                    )

                evidence = connection.execute(
                    """
                    SELECT evidence_kind, evidence_id
                    FROM graph_edge_evidence
                    WHERE edge_id = ?
                    """,
                    ("contains|wechat|wechat:default",),
                ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in evidence],
                    [("source_account", "wechat:default")],
                )

    def test_message_fts_tracks_insert_update_title_change_delete_and_rollback(
        self,
    ) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "wechat-ai.sqlite"
            with closing(database.initialize_database(db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        source_conversation_id,
                        title,
                        kind
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("group@chatroom", "group@chatroom", "Project Room", "group"),
                )
                connection.commit()

                connection.execute("BEGIN")
                cursor = connection.execute(
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
                        "group@chatroom",
                        "9001",
                        "shard:table:server:9001",
                        "alice",
                        "Alice",
                        1_700_000_000_000,
                        "text",
                        "launch checklist",
                    ),
                )
                message_id = cursor.lastrowid

                indexed = connection.execute(
                    """
                    SELECT rowid, content, sender_name, conversation_title
                    FROM message_fts
                    WHERE message_fts MATCH ?
                    """,
                    ("launch",),
                ).fetchone()
                self.assertEqual(
                    tuple(indexed),
                    (message_id, "launch checklist", "Alice", "Project Room"),
                )
                connection.rollback()
                self.assertIsNone(
                    connection.execute(
                        "SELECT rowid FROM message_fts WHERE rowid = ?",
                        (message_id,),
                    ).fetchone()
                )

                cursor = connection.execute(
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
                        "group@chatroom",
                        "9002",
                        "shard:table:server:9002",
                        "bob",
                        "Bob",
                        1_700_000_001_000,
                        "text",
                        "draft agenda",
                    ),
                )
                message_id = cursor.lastrowid
                connection.execute(
                    "UPDATE messages SET content = ?, sender_name = ? WHERE id = ?",
                    ("final agenda", "Robert", message_id),
                )
                connection.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    ("Launch Room", "group@chatroom"),
                )
                updated = connection.execute(
                    """
                    SELECT content, sender_name, conversation_title
                    FROM message_fts
                    WHERE rowid = ?
                    """,
                    (message_id,),
                ).fetchone()
                self.assertEqual(
                    tuple(updated),
                    ("final agenda", "Robert", "Launch Room"),
                )

                connection.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                self.assertIsNone(
                    connection.execute(
                        "SELECT rowid FROM message_fts WHERE rowid = ?",
                        (message_id,),
                    ).fetchone()
                )
                connection.commit()

    def test_non_text_placeholder_is_not_indexed_as_plain_message_content(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with closing(
                database.initialize_database(Path(temp_dir) / "wechat-ai.sqlite")
            ) as connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        source_conversation_id,
                        title,
                        kind
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("alice", "alice", "Alice", "direct"),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id,
                        dedupe_key,
                        sent_at,
                        type,
                        content
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("alice", "media-1", 1_700_000_002_000, "image", "[图片]"),
                )

                indexed_content = connection.execute(
                    "SELECT content FROM message_fts WHERE rowid = ?",
                    (cursor.lastrowid,),
                ).fetchone()[0]
                self.assertEqual(indexed_content, "")

    def test_message_dedupe_key_is_unique(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with closing(
                database.initialize_database(Path(temp_dir) / "wechat-ai.sqlite")
            ) as connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        source_conversation_id,
                        title,
                        kind
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("alice", "alice", "Alice", "direct"),
                )
                values = ("alice", "same-key", 1_700_000_000_000, "text", "hello")
                connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id,
                        dedupe_key,
                        sent_at,
                        type,
                        content
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    values,
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO messages (
                            conversation_id,
                            dedupe_key,
                            sent_at,
                            type,
                            content
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        values,
                    )


    def test_graph_schema_accepts_frozen_knowledge_node_and_relation_kinds(
        self,
    ) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with closing(
                database.initialize_database(Path(temp_dir) / "wechat-ai.sqlite")
            ) as connection:
                connection.executemany(
                    """
                    INSERT INTO graph_nodes(
                        id,
                        kind,
                        label,
                        trace_kind,
                        trace_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        ("group:1", "group", "Project Group", "conversation", "g1"),
                        ("contact:1", "contact", "Alice", "contact", "alice"),
                        ("topic:1", "topic", "Launch", "topic", "launch"),
                        ("entity:1", "entity", "Project X", "entity", "project-x"),
                        (
                            "domain:1",
                            "link_domain",
                            "example.com",
                            "link_domain",
                            "example.com",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO graph_edges(
                        id,
                        source_node_id,
                        target_node_id,
                        kind,
                        weight
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "member:1",
                            "contact:1",
                            "group:1",
                            "member_of",
                            1,
                        ),
                        (
                            "mention:1",
                            "group:1",
                            "topic:1",
                            "mentions",
                            1,
                        ),
                        (
                            "relation:1",
                            "topic:1",
                            "entity:1",
                            "related_to",
                            1,
                        ),
                        (
                            "link:1",
                            "entity:1",
                            "domain:1",
                            "links_to",
                            1,
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO graph_edge_evidence(
                        edge_id,
                        evidence_kind,
                        evidence_id
                    ) VALUES (?, 'message', ?)
                    """,
                    (
                        ("member:1", "1"),
                        ("mention:1", "1"),
                        ("relation:1", "1"),
                        ("link:1", "1"),
                    ),
                )

                assert connection.execute(
                    "SELECT COUNT(*) FROM graph_nodes"
                ).fetchone()[0] == 5
                assert connection.execute(
                    "SELECT COUNT(*) FROM graph_edges"
                ).fetchone()[0] == 4


    def test_v1_upgrade_uses_sqlite_backup_once_before_migration(self) -> None:
        database = load_database_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "data" / "library" / "wechat-ai.sqlite"
            db_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    (database.MIGRATIONS_DIR / "001_initial.sql").read_text(
                        encoding="utf-8"
                    )
                )
                connection.execute(
                    """
                    INSERT INTO app_meta(key, value)
                    VALUES ('schema_version', '1')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO conversations(
                        id,
                        source_conversation_id,
                        title,
                        kind,
                        note
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("alice", "alice", "Alice", "direct", "preserve me"),
                )
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "data" / "backups"
            first_clock = lambda: datetime(
                2026,
                7,
                30,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )
            with closing(
                database.initialize_database(
                    db_path,
                    backup_dir=backup_dir,
                    clock=first_clock,
                )
            ):
                pass

            backup_paths = list(backup_dir.glob("*.sqlite"))
            self.assertEqual(
                [path.name for path in backup_paths],
                ["wechat-ai.v1.20260730T010203Z.sqlite"],
            )
            with closing(sqlite3.connect(backup_paths[0])) as backup:
                self.assertEqual(
                    backup.execute(
                        "SELECT value FROM app_meta WHERE key = 'schema_version'"
                    ).fetchone()[0],
                    "1",
                )
                self.assertEqual(
                    backup.execute(
                        "SELECT note FROM conversations WHERE id = 'alice'"
                    ).fetchone()[0],
                    "preserve me",
                )

            with closing(
                database.initialize_database(
                    db_path,
                    backup_dir=backup_dir,
                    clock=lambda: datetime.now(timezone.utc),
                )
            ):
                pass
            self.assertEqual(len(list(backup_dir.glob("*.sqlite"))), 1)


    def test_failed_startup_recovery_keeps_facts_then_retries(self) -> None:
        database = load_database_module()
        from wechat_ai.services.knowledge_projection_service import project_knowledge

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "wechat-ai.sqlite"
            with closing(database.initialize_database(db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO conversations(id, source_conversation_id, title, kind)
                    VALUES ('team', 'team', 'Team', 'group')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO messages(
                        conversation_id, dedupe_key, sender_name,
                        sent_at, type, content
                    ) VALUES ('team', 'team:1', 'Alice', 1000, 'text', 'launch')
                    """
                )
                # Import owns normalized parts; startup only repairs derived indexes.
                project_knowledge(connection, build_indexes=False)
                connection.execute(
                    "UPDATE source_accounts SET projection_status = 'degraded'"
                )
                connection.commit()

            with patch(
                "wechat_ai.services.graph_service.project_graph",
                side_effect=RuntimeError("synthetic graph failure"),
            ):
                with closing(database.initialize_database(db_path)) as failed:
                    self.assertEqual(
                        failed.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        failed.execute(
                            "SELECT projection_status FROM source_accounts"
                        ).fetchone()[0],
                        "degraded",
                    )
                    self.assertEqual(
                        failed.execute(
                            "SELECT COUNT(*) FROM retrieval_chunks"
                        ).fetchone()[0],
                        0,
                    )

            with closing(database.initialize_database(db_path)) as recovered:
                self.assertEqual(
                    recovered.execute(
                        "SELECT projection_status FROM source_accounts"
                    ).fetchone()[0],
                    "ready",
                )
                self.assertEqual(
                    recovered.execute(
                        "SELECT COUNT(*) FROM retrieval_chunks"
                    ).fetchone()[0],
                    1,
                )
                self.assertGreater(
                    recovered.execute(
                        "SELECT COUNT(*) FROM graph_edge_evidence"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
