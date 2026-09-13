from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fixtures.synthetic_sqlite import create_synthetic_sources, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_wechat_db_module():
    try:
        return importlib.import_module("wechat_ai.wechat_db")
    except ModuleNotFoundError as exc:
        raise AssertionError("wechat_ai.wechat_db has not been implemented") from exc


class WechatDatabaseTests(unittest.TestCase):
    def test_discover_sources_uses_table_signatures_not_filenames(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            fixtures = create_synthetic_sources(Path(temp_dir) / "sources")

            discovered = wechat_db.discover_sources(fixtures.root)
            kinds_by_name = {
                source.path.name: str(source.kind)
                for source in discovered
            }

            self.assertEqual(
                kinds_by_name,
                {
                    fixtures.session.name: "session",
                    fixtures.contact.name: "contact",
                    fixtures.message_shard.name: "message",
                },
            )
            self.assertNotIn(fixtures.unrelated.name, kinds_by_name)

    def test_source_connection_rejects_writes(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            fixtures = create_synthetic_sources(Path(temp_dir) / "sources")

            with closing(
                wechat_db.connect_read_only(fixtures.session)
            ) as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE forbidden_write (id INTEGER)")

    def test_dedupe_key_prefers_server_id_then_local_id_and_sort_seq(self) -> None:
        wechat_db = load_wechat_db_module()

        self.assertEqual(
            wechat_db.make_dedupe_key(
                "shard-a",
                "Msg_abc",
                server_id=42,
                local_id=7,
                sort_seq=9,
            ),
            "shard-a:Msg_abc:server:42",
        )
        self.assertEqual(
            wechat_db.make_dedupe_key(
                "shard-a",
                "Msg_abc",
                server_id=0,
                local_id=7,
                sort_seq=9,
            ),
            "shard-a:Msg_abc:local:7:9",
        )

    def test_import_is_repeatable_and_does_not_change_source_hashes(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "library" / "wechat-ai.sqlite"
            hashes_before = {
                path.name: sha256_file(path)
                for path in fixtures.database_paths
            }

            first = wechat_db.import_sources(fixtures.root, app_database)
            with closing(sqlite3.connect(app_database)) as connection:
                connection.execute(
                    """
                    UPDATE conversations
                    SET is_favorite = 1, note = 'local-only'
                    WHERE id = ?
                    """,
                    (fixtures.group_user_name,),
                )
                connection.commit()

            second = wechat_db.import_sources(fixtures.root, app_database)
            hashes_after = {
                path.name: sha256_file(path)
                for path in fixtures.database_paths
            }

            self.assertEqual(first.inserted_messages, 5)
            self.assertEqual(second.inserted_messages, 0)
            self.assertEqual(hashes_after, hashes_before)

            with closing(sqlite3.connect(app_database)) as connection:
                total, distinct_keys = connection.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT dedupe_key) FROM messages"
                ).fetchone()
                self.assertEqual((total, distinct_keys), (5, 5))

                local_fields = connection.execute(
                    """
                    SELECT is_favorite, note
                    FROM conversations
                    WHERE id = ?
                    """,
                    (fixtures.group_user_name,),
                ).fetchone()
                self.assertEqual(local_fields, (1, "local-only"))

    def test_import_upserts_each_conversation_only_once(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "library" / "wechat-ai.sqlite"
            calls: list[str] = []
            original = wechat_db._upsert_conversation

            def tracked_upsert(connection, **kwargs) -> None:
                calls.append(str(kwargs["user_name"]))
                original(connection, **kwargs)

            with patch.object(
                wechat_db,
                "_upsert_conversation",
                tracked_upsert,
            ):
                result = wechat_db.import_sources(
                    fixtures.root,
                    app_database,
                )

            self.assertEqual(len(calls), result.normalized_conversations)
            self.assertEqual(len(calls), len(set(calls)))

    def test_repeat_import_avoids_noop_conversation_title_updates(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "library" / "wechat-ai.sqlite"

            wechat_db.import_sources(fixtures.root, app_database)
            with closing(sqlite3.connect(app_database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE title_update_audit(conversation_id TEXT);
                    CREATE TRIGGER audit_conversation_title_update
                    AFTER UPDATE OF title ON conversations
                    BEGIN
                        INSERT INTO title_update_audit(conversation_id)
                        VALUES (NEW.id);
                    END;
                    """
                )
                connection.commit()

            wechat_db.import_sources(fixtures.root, app_database)
            with closing(sqlite3.connect(app_database)) as connection:
                updates = connection.execute(
                    "SELECT COUNT(*) FROM title_update_audit"
                ).fetchone()[0]

            self.assertEqual(updates, 0)

    def test_import_uses_md5_table_mapping_and_stable_dedupe_keys(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "wechat-ai.sqlite"

            wechat_db.import_sources(fixtures.root, app_database)

            with closing(sqlite3.connect(app_database)) as connection:
                rows = connection.execute(
                    """
                    SELECT dedupe_key, content
                    FROM messages
                    ORDER BY sent_at
                    """
                ).fetchall()

            self.assertIn(
                (
                    f"opaque-message:{fixtures.group_table}:server:9001",
                    "launch checklist",
                ),
                rows,
            )
            self.assertIn(
                (
                    f"opaque-message:{fixtures.group_table}:local:2:200",
                    "second project note",
                ),
                rows,
            )
            self.assertNotIn("rogue table content", {row[1] for row in rows})

    def test_import_normalizes_metadata_timestamps_media_and_fts(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "wechat-ai.sqlite"

            wechat_db.import_sources(fixtures.root, app_database)

            with closing(sqlite3.connect(app_database)) as connection:
                group = connection.execute(
                    """
                    SELECT title, kind, last_message_at, message_count
                    FROM conversations
                    WHERE id = ?
                    """,
                    (fixtures.group_user_name,),
                ).fetchone()
                self.assertEqual(group, ("Core Team", "group", 1_700_000_003_000, 4))

                image = connection.execute(
                    """
                    SELECT messages.type, messages.content, message_fts.content
                    FROM messages
                    JOIN message_fts ON message_fts.rowid = messages.id
                    WHERE messages.source_message_id = '9003'
                    """
                ).fetchone()
                self.assertEqual(image, ("image", "[图片]", ""))

                indexed = connection.execute(
                    """
                    SELECT messages.content, message_fts.conversation_title
                    FROM message_fts
                    JOIN messages ON messages.id = message_fts.rowid
                    WHERE message_fts MATCH 'launch'
                    """
                ).fetchone()
                self.assertEqual(indexed, ("launch checklist", "Core Team"))

                unresolved = connection.execute(
                    """
                    SELECT sender_id, sender_name
                    FROM messages
                    WHERE content = 'unresolved sender'
                    """
                ).fetchone()
                self.assertEqual(
                    unresolved,
                    (
                        "opaque-message:sender:999",
                        "opaque-message:sender:999",
                    ),
                )

    def test_name_mapping_without_explicit_id_uses_read_only_sqlite_rowid(
        self,
    ) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "sources"
            source_dir.mkdir()
            source_database = source_dir / "rowid-message.sqlite"
            group_user_name = "rowid-group@chatroom"
            table_name = (
                "Msg_"
                + hashlib.md5(group_user_name.encode("utf-8")).hexdigest()
            )
            with closing(sqlite3.connect(source_database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE Name2Id (
                        user_name TEXT NOT NULL,
                        is_session INTEGER NOT NULL
                    );
                    INSERT INTO Name2Id(user_name, is_session) VALUES
                        ('rowid-group@chatroom', 1),
                        ('alice', 0);
                    """
                )
                connection.execute(
                    f"""
                    CREATE TABLE "{table_name}" (
                        local_id INTEGER NOT NULL,
                        server_id INTEGER NOT NULL,
                        local_type INTEGER NOT NULL,
                        sort_seq INTEGER NOT NULL,
                        real_sender_id INTEGER,
                        create_time INTEGER NOT NULL,
                        status INTEGER,
                        source INTEGER,
                        message_content TEXT,
                        compress_content BLOB,
                        packed_info_data BLOB
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO "{table_name}" VALUES (
                        1, 7001, 1, 10, 2, 1700000100,
                        0, 0, 'rowid sender mapping', NULL, NULL
                    )
                    """
                )
                connection.commit()
            hash_before = sha256_file(source_database)
            app_database = temp_path / "wechat-ai.sqlite"

            try:
                result = wechat_db.import_sources(source_dir, app_database)
            except wechat_db.SourceSchemaError as exc:
                self.fail(f"implicit SQLite rowid mapping is unsupported: {exc}")

            self.assertEqual(result.inserted_messages, 1)
            self.assertEqual(sha256_file(source_database), hash_before)
            with closing(sqlite3.connect(app_database)) as connection:
                sender = connection.execute(
                    """
                    SELECT sender_id, sender_name
                    FROM messages
                    WHERE content = 'rowid sender mapping'
                    """
                ).fetchone()
            self.assertEqual(sender, ("alice", "alice"))


    def test_import_projects_knowledge_and_graph_idempotently(self) -> None:
        wechat_db = load_wechat_db_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixtures = create_synthetic_sources(temp_path / "sources")
            app_database = temp_path / "wechat-ai.sqlite"

            first = wechat_db.import_sources(fixtures.root, app_database)
            with closing(sqlite3.connect(app_database)) as connection:
                counts_after_first = tuple(
                    connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM conversation_origins),
                            (SELECT COUNT(*) FROM message_parts),
                            (SELECT COUNT(*) FROM assets),
                            (SELECT COUNT(*) FROM retrieval_chunks),
                            (SELECT COUNT(*) FROM graph_nodes),
                            (SELECT COUNT(*) FROM graph_edges),
                            (SELECT COUNT(*) FROM graph_edge_evidence)
                        """
                    ).fetchone()
                )
                image_asset = tuple(
                    connection.execute(
                        """
                        SELECT
                            message_parts.kind,
                            message_parts.status,
                            assets.kind,
                            assets.status,
                            assets.file_name,
                            assets.external_url,
                            assets.local_path
                        FROM messages
                        JOIN message_parts
                          ON message_parts.message_id = messages.id
                        JOIN assets ON assets.part_id = message_parts.id
                        WHERE messages.source_message_id = '9003'
                        """
                    ).fetchone()
                )
                launch_chunk = tuple(
                    connection.execute(
                        """
                        SELECT
                            retrieval_chunks.conversation_id,
                            retrieval_chunks.message_id,
                            retrieval_chunks.text_content
                        FROM retrieval_chunk_fts
                        JOIN retrieval_chunks
                          ON retrieval_chunks.id = retrieval_chunk_fts.rowid
                        WHERE retrieval_chunk_fts MATCH 'launch'
                        """
                    ).fetchone()
                )
                conversation_node = tuple(
                    connection.execute(
                        """
                        SELECT kind, label, trace_kind, trace_id
                        FROM graph_nodes
                        WHERE trace_kind = 'conversation'
                          AND trace_id = ?
                        """,
                        (fixtures.group_user_name,),
                    ).fetchone()
                )
                traced_message_edges = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM graph_edge_evidence
                    WHERE evidence_kind = 'message'
                    """
                ).fetchone()[0]

            second = wechat_db.import_sources(fixtures.root, app_database)
            with closing(sqlite3.connect(app_database)) as connection:
                counts_after_second = tuple(
                    connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM conversation_origins),
                            (SELECT COUNT(*) FROM message_parts),
                            (SELECT COUNT(*) FROM assets),
                            (SELECT COUNT(*) FROM retrieval_chunks),
                            (SELECT COUNT(*) FROM graph_nodes),
                            (SELECT COUNT(*) FROM graph_edges),
                            (SELECT COUNT(*) FROM graph_edge_evidence)
                        """
                    ).fetchone()
                )

            self.assertEqual(first.inserted_messages, 5)
            self.assertEqual(second.inserted_messages, 0)
            self.assertEqual(counts_after_first[:4], (2, 5, 1, 4))
            self.assertEqual(counts_after_second, counts_after_first)
            self.assertEqual(
                image_asset,
                ("image", "unresolved", "image", "unresolved", None, None, None),
            )
            self.assertEqual(
                launch_chunk,
                (fixtures.group_user_name, 1, "launch checklist"),
            )
            self.assertEqual(
                conversation_node,
                (
                    "conversation",
                    "Core Team",
                    "conversation",
                    fixtures.group_user_name,
                ),
            )
            self.assertGreater(traced_message_edges, 0)



    def test_import_materializes_safe_local_media_as_content_addressed_asset(self) -> None:
        wechat_db = load_wechat_db_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = create_synthetic_sources(root / "sources")
            media_path = fixtures.root / "media" / "board.png"
            media_path.parent.mkdir()
            media_bytes = b"synthetic-image-content"
            media_path.write_bytes(media_bytes)
            with closing(sqlite3.connect(fixtures.message_shard)) as connection:
                connection.execute(
                    f'UPDATE "{fixtures.group_table}" SET packed_info_data = ? '
                    "WHERE server_id = 9003",
                    (
                        '{"local_path":"media/board.png",'
                        '"file_name":"board.png",'
                        '"mime_type":"image/png\\r\\nx-injected: yes"}',
                    ),
                )
                connection.commit()
            source_hash = sha256_file(fixtures.message_shard)
            application_database = root / "data" / "library" / "wechat-ai.sqlite"

            first = wechat_db.import_sources(fixtures.root, application_database)
            second = wechat_db.import_sources(fixtures.root, application_database)

            digest = hashlib.sha256(media_bytes).hexdigest()
            copied_path = application_database.parent / "assets" / digest
            self.assertEqual(first.inserted_messages, 5)
            self.assertEqual(second.inserted_messages, 0)
            self.assertTrue(copied_path.is_file())
            self.assertEqual(copied_path.read_bytes(), media_bytes)
            self.assertEqual(sha256_file(fixtures.message_shard), source_hash)
            self.assertEqual(
                list(copied_path.parent.glob(f".{digest}.*.tmp")), []
            )
            with closing(sqlite3.connect(application_database)) as connection:
                asset = tuple(
                    connection.execute(
                        """
                        SELECT assets.status, assets.file_name, assets.mime_type,
                               assets.byte_size, assets.local_path, assets.sha256,
                               message_parts.status
                        FROM assets
                        JOIN message_parts ON message_parts.id = assets.part_id
                        JOIN messages ON messages.id = message_parts.message_id
                        WHERE messages.source_message_id = '9003'
                        """
                    ).fetchone()
                )
                asset_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM assets
                    JOIN message_parts ON message_parts.id = assets.part_id
                    JOIN messages ON messages.id = message_parts.message_id
                    WHERE messages.source_message_id = '9003'
                    """
                ).fetchone()[0]
            self.assertEqual(
                asset,
                (
                    "resolved", "board.png", "image/png", len(media_bytes),
                    digest, digest, "resolved",
                ),
            )
            self.assertEqual(asset_count, 1)

    def test_import_resolves_wechat_media_without_claiming_encrypted_v2(self) -> None:
        wechat_db = load_wechat_db_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = create_synthetic_sources(root / "sources")
            application_database = root / "data" / "library" / "wechat-ai.sqlite"
            attach_root = root / "attach"
            media_database = fixtures.root / "media_0.db"
            hardlink_database = fixtures.root / "hardlink.db"

            voice_server_id = 9_200
            encrypted_server_id = 9_300
            voice_bytes = b"\x02#!SILK_V3" + b"\x00\x01synthetic-voice"
            png_bytes = bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001"
                "0804000000b51c0c020000000b4944415478da6364f80f00"
                "010501012718e3660000000049454e44ae426082"
            )
            encrypted_v2_bytes = (
                b"\x07\x08V2\x08\x07"
                + bytes(range(32))
                + b"\x00" * 64
                + b"encrypted-image-payload-without-key"
            )
            plain_key = "0123456789abcdef0123456789abcdef"
            encrypted_key = "fedcba9876543210fedcba9876543210"
            plain_packed_info = (
                b"\x1a\x22\x22\x20" + plain_key.encode("ascii")
            )
            encrypted_packed_info = (
                b"\x1a\x22\x22\x20" + encrypted_key.encode("ascii")
            )

            with closing(sqlite3.connect(fixtures.message_shard)) as connection:
                connection.execute(
                    f'UPDATE "{fixtures.group_table}" SET packed_info_data = ? '
                    "WHERE server_id = 9003",
                    (plain_packed_info,),
                )
                connection.executemany(
                    f'INSERT INTO "{fixtures.group_table}" '
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            5,
                            voice_server_id,
                            34,
                            500,
                            2,
                            1_700_000_005,
                            0,
                            0,
                            "voice payload must come from VoiceInfo",
                            None,
                            None,
                        ),
                        (
                            6,
                            encrypted_server_id,
                            3,
                            600,
                            2,
                            1_700_000_006,
                            0,
                            0,
                            "encrypted image must remain unresolved",
                            None,
                            encrypted_packed_info,
                        ),
                    ),
                )
                connection.commit()

            with closing(sqlite3.connect(media_database)) as connection:
                connection.execute(
                    """
                    CREATE TABLE VoiceInfo (
                        svr_id INTEGER NOT NULL,
                        voice_data BLOB NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO VoiceInfo(svr_id, voice_data) VALUES (?, ?)",
                    (voice_server_id, voice_bytes),
                )
                connection.commit()

            with closing(sqlite3.connect(hardlink_database)) as connection:
                connection.execute("CREATE TABLE dir2id (username TEXT NOT NULL)")
                connection.execute(
                    """
                    CREATE TABLE image_hardlink_info_v4 (
                        md5_hash INTEGER,
                        md5 TEXT,
                        type INTEGER,
                        file_name TEXT,
                        file_size INTEGER,
                        modify_time INTEGER,
                        dir1 INTEGER,
                        dir2 INTEGER,
                        _rowid_ INTEGER PRIMARY KEY,
                        extra_buffer BLOB
                    )
                    """
                )
                first_dir = connection.execute(
                    "INSERT INTO dir2id(username) VALUES ('account-hash')"
                ).lastrowid
                second_dir = connection.execute(
                    "INSERT INTO dir2id(username) VALUES ('2026-07')"
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO image_hardlink_info_v4(
                        md5_hash, md5, type, file_name, file_size,
                        modify_time, dir1, dir2, extra_buffer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            1,
                            plain_key,
                            2,
                            "plain-image.dat",
                            len(png_bytes),
                            100,
                            first_dir,
                            second_dir,
                            None,
                        ),
                        (
                            2,
                            encrypted_key,
                            2,
                            "encrypted-v2.dat",
                            len(encrypted_v2_bytes),
                            200,
                            first_dir,
                            second_dir,
                            None,
                        ),
                    ),
                )
                connection.commit()

            media_dir = attach_root / "account-hash" / "2026-07"
            media_dir.mkdir(parents=True)
            (media_dir / "plain-image.dat").write_bytes(png_bytes)
            (media_dir / "encrypted-v2.dat").write_bytes(encrypted_v2_bytes)

            first = wechat_db.import_sources(
                fixtures.root,
                application_database,
                media_root=attach_root,
            )
            second = wechat_db.import_sources(
                fixtures.root,
                application_database,
                media_root=attach_root,
            )

            voice_digest = hashlib.sha256(voice_bytes).hexdigest()
            image_digest = hashlib.sha256(png_bytes).hexdigest()
            encrypted_digest = hashlib.sha256(encrypted_v2_bytes).hexdigest()
            asset_root = application_database.parent / "assets"
            with closing(sqlite3.connect(application_database)) as connection:
                media_rows = {
                    str(row[0]): tuple(row[1:])
                    for row in connection.execute(
                        """
                        SELECT
                            messages.source_message_id,
                            messages.type,
                            message_parts.kind,
                            message_parts.status,
                            assets.kind,
                            assets.status,
                            assets.mime_type,
                            assets.byte_size,
                            assets.local_path,
                            assets.sha256,
                            assets.metadata_json
                        FROM messages
                        JOIN message_parts
                          ON message_parts.message_id = messages.id
                        JOIN assets ON assets.part_id = message_parts.id
                        WHERE messages.source_message_id IN ('9003', '9200', '9300')
                        """
                    ).fetchall()
                }
                asset_count = connection.execute(
                    "SELECT COUNT(*) FROM assets"
                ).fetchone()[0]

            voice = media_rows[str(voice_server_id)]
            self.assertEqual(
                voice[:9],
                (
                    "voice",
                    "audio",
                    "resolved",
                    "audio",
                    "resolved",
                    "audio/silk",
                    len(voice_bytes),
                    voice_digest,
                    voice_digest,
                ),
            )
            voice_metadata = json.loads(voice[9])
            self.assertEqual(voice_metadata["codec"], "silk")
            self.assertIs(voice_metadata["playable"], False)
            self.assertEqual(
                media_rows["9003"][:9],
                (
                    "image",
                    "image",
                    "resolved",
                    "image",
                    "resolved",
                    "image/png",
                    len(png_bytes),
                    image_digest,
                    image_digest,
                ),
            )
            self.assertEqual(
                media_rows[str(encrypted_server_id)][2:9],
                (
                    "unresolved",
                    "image",
                    "unresolved",
                    "application/x-wechat-image-v2",
                    len(encrypted_v2_bytes),
                    encrypted_digest,
                    encrypted_digest,
                ),
            )
            encrypted_metadata = json.loads(
                media_rows[str(encrypted_server_id)][9]
            )
            self.assertEqual(encrypted_metadata["encryption"], "wechat-v2")
            self.assertIs(encrypted_metadata["previewable"], False)
            self.assertEqual(first.inserted_messages, 7)
            self.assertEqual(second.inserted_messages, 0)
            self.assertEqual(asset_count, 3)
            self.assertEqual(
                {path.name for path in asset_root.iterdir()},
                {voice_digest, image_digest, encrypted_digest},
            )
            self.assertEqual((asset_root / voice_digest).read_bytes(), voice_bytes)
            self.assertEqual((asset_root / image_digest).read_bytes(), png_bytes)
            self.assertEqual(
                (asset_root / encrypted_digest).read_bytes(),
                encrypted_v2_bytes,
            )

    def test_projection_failure_commits_facts_and_next_sync_recovers(self) -> None:
        wechat_db = load_wechat_db_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = create_synthetic_sources(root / "sources")
            application_database = root / "data" / "library" / "wechat-ai.sqlite"

            def fail_projection(*_args, **_kwargs):
                raise RuntimeError("synthetic projector failure")

            first = wechat_db.import_sources(
                fixtures.root,
                application_database,
                knowledge_projector=fail_projection,
            )
            with closing(sqlite3.connect(application_database)) as connection:
                facts_after_failure = connection.execute(
                    "SELECT COUNT(*) FROM messages"
                ).fetchone()[0]
                parts_after_failure = connection.execute(
                    "SELECT COUNT(*) FROM message_parts"
                ).fetchone()[0]
                assets_after_failure = connection.execute(
                    "SELECT COUNT(*) FROM assets"
                ).fetchone()[0]
                chunks_after_failure = connection.execute(
                    "SELECT COUNT(*) FROM retrieval_chunks"
                ).fetchone()[0]
                degraded = connection.execute(
                    """
                    SELECT projection_status
                    FROM source_accounts
                    WHERE id = 'wechat:default'
                    """
                ).fetchone()[0]

            second = wechat_db.import_sources(fixtures.root, application_database)
            with closing(sqlite3.connect(application_database)) as connection:
                chunks_after_recovery = connection.execute(
                    "SELECT COUNT(*) FROM retrieval_chunks"
                ).fetchone()[0]
                ready = connection.execute(
                    """
                    SELECT projection_status
                    FROM source_accounts
                    WHERE id = 'wechat:default'
                    """
                ).fetchone()[0]

            self.assertEqual(first.inserted_messages, 5)
            self.assertEqual(facts_after_failure, 5)
            self.assertEqual(parts_after_failure, 5)
            self.assertEqual(assets_after_failure, 1)
            self.assertEqual(chunks_after_failure, 0)
            self.assertEqual(degraded, "degraded")
            self.assertEqual(second.inserted_messages, 0)
            self.assertEqual(chunks_after_recovery, 4)
            self.assertEqual(ready, "ready")
if __name__ == "__main__":
    unittest.main()
