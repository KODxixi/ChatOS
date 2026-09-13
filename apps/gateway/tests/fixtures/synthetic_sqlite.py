from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SyntheticSources:
    root: Path
    session: Path
    contact: Path
    message_shard: Path
    unrelated: Path
    group_user_name: str
    direct_user_name: str
    group_table: str
    direct_table: str
    rogue_table: str

    @property
    def database_paths(self) -> tuple[Path, ...]:
        return (
            self.session,
            self.contact,
            self.message_shard,
            self.unrelated,
        )


def create_synthetic_sources(root: Path) -> SyntheticSources:
    root.mkdir(parents=True, exist_ok=True)
    group_user_name = "project@chatroom"
    direct_user_name = "alice"
    group_table = _message_table(group_user_name)
    direct_table = _message_table(direct_user_name)
    rogue_table = "Msg_" + ("0" * 32)

    session = root / "opaque-session-name.sqlite"
    _write_database(
        session,
        """
        CREATE TABLE SessionTable (
            user_name TEXT PRIMARY KEY,
            nick_name TEXT NOT NULL,
            last_timestamp INTEGER
        );
        INSERT INTO SessionTable VALUES
            ('project@chatroom', 'Session Project Room', 1700000003),
            ('alice', 'Alice Session', 1700000004);
        """,
    )

    contact = root / "looks-like-a-message-shard.db"
    _write_database(
        contact,
        """
        CREATE TABLE contact (
            user_name TEXT PRIMARY KEY,
            nick_name TEXT NOT NULL,
            remark TEXT NOT NULL DEFAULT '',
            local_type INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE chat_room (
            user_name TEXT PRIMARY KEY,
            member_list TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO contact VALUES
            ('project@chatroom', 'Project Room', 'Core Team', 2),
            ('alice', 'Alice', 'Alicia', 1),
            ('bob', 'Bob', '', 1);
        INSERT INTO chat_room VALUES
            ('project@chatroom', 'alice,bob');
        """,
    )

    message_shard = root / "opaque-message.sqlite"
    connection = sqlite3.connect(message_shard)
    try:
        connection.executescript(
            """
            CREATE TABLE Name2Id (
                id INTEGER PRIMARY KEY,
                user_name TEXT NOT NULL UNIQUE
            );
            INSERT INTO Name2Id(id, user_name) VALUES
                (1, 'project@chatroom'),
                (2, 'alice'),
                (3, 'bob');
            """
        )
        for table_name in (group_table, direct_table, rogue_table):
            _create_message_table(connection, table_name)

        connection.executemany(
            f'INSERT INTO "{group_table}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                (
                    1,
                    9001,
                    1,
                    100,
                    2,
                    1_700_000_000,
                    0,
                    0,
                    "launch checklist",
                    None,
                    None,
                ),
                (
                    2,
                    0,
                    1,
                    200,
                    3,
                    1_700_000_001,
                    0,
                    0,
                    "second project note",
                    None,
                    None,
                ),
                (
                    3,
                    9003,
                    3,
                    300,
                    2,
                    1_700_000_002,
                    0,
                    0,
                    "raw image payload must not be indexed",
                    b"compressed",
                    None,
                ),
                (
                    4,
                    0,
                    1,
                    400,
                    999,
                    1_700_000_003,
                    0,
                    0,
                    "unresolved sender",
                    None,
                    None,
                ),
            ),
        )
        connection.execute(
            f'INSERT INTO "{direct_table}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                1,
                9100,
                1,
                10,
                2,
                1_700_000_004_000,
                0,
                0,
                "direct hello",
                None,
                None,
            ),
        )
        connection.execute(
            f'INSERT INTO "{rogue_table}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                99,
                9999,
                1,
                999,
                2,
                1_700_000_099,
                0,
                0,
                "rogue table content",
                None,
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    unrelated = root / "not-a-wechat-database.sqlite"
    _write_database(
        unrelated,
        """
        CREATE TABLE unrelated (
            id INTEGER PRIMARY KEY,
            value TEXT
        );
        INSERT INTO unrelated(value) VALUES ('synthetic');
        """,
    )

    return SyntheticSources(
        root=root,
        session=session,
        contact=contact,
        message_shard=message_shard,
        unrelated=unrelated,
        group_user_name=group_user_name,
        direct_user_name=direct_user_name,
        group_table=group_table,
        direct_table=direct_table,
        rogue_table=rogue_table,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _message_table(user_name: str) -> str:
    suffix = hashlib.md5(user_name.encode("utf-8")).hexdigest()
    return f"Msg_{suffix}"


def _create_message_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
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


def _write_database(path: Path, script: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
