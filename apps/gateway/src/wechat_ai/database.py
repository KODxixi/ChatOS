from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .runtime_contract import API_CONTRACT_VERSION, APP_VERSION


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BackupClock = Callable[[], datetime]


def initialize_database(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    clock: BackupClock | None = None,
) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        # 与共享工厂一致：拉长 busy_timeout 到 30s，避免初始化/迁移期间被并发
        # 写请求 5s 内抢锁失败。
        connection.execute("PRAGMA busy_timeout = 30000")
        migration_paths = _migration_paths()
        current_version = _current_schema_version(connection)
        target_version = _target_schema_version(migration_paths)
        if 0 < current_version < target_version:
            _backup_before_migration(
                connection,
                database_path=database_path,
                schema_version=current_version,
                backup_dir=Path(backup_dir) if backup_dir is not None else None,
                clock=clock,
            )
        _apply_migrations(connection, migration_paths=migration_paths)
        _record_runtime_contract(connection)
        recover_knowledge_indexes(connection, force=False)
    except BaseException:
        connection.close()
        raise
    return connection




def recover_knowledge_indexes(
    connection: sqlite3.Connection,
    *,
    force: bool = False,
) -> bool:
    required_tables = (
        "messages",
        "message_parts",
        "retrieval_chunks",
        "graph_edge_evidence",
        "source_accounts",
    )
    if not all(_table_exists(connection, name) for name in required_tables):
        return False
    if connection.execute("SELECT 1 FROM messages LIMIT 1").fetchone() is None:
        return False

    from .services.graph_service import project_graph
    from .services.knowledge_projection_service import (
        RETRIEVAL_INDEX_VERSION,
        project_knowledge,
    )

    is_degraded = connection.execute(
        """
        SELECT 1 FROM source_accounts
        WHERE projection_status <> 'ready'
        LIMIT 1
        """
    ).fetchone() is not None
    if not force and not is_degraded and not _knowledge_indexes_need_recovery(
        connection, index_version=RETRIEVAL_INDEX_VERSION
    ):
        return False

    _set_projection_status(connection, "indexing")
    connection.commit()
    try:
        project_knowledge(connection)
        project_graph(connection)
    except Exception:
        connection.rollback()
        _set_projection_status(connection, "degraded")
        connection.commit()
        return False
    _set_projection_status(connection, "ready")
    connection.commit()
    return True


def _knowledge_indexes_need_recovery(
    connection: sqlite3.Connection,
    *,
    index_version: int,
) -> bool:
    missing_part = connection.execute(
        """
        SELECT 1
        FROM messages
        LEFT JOIN message_parts ON message_parts.message_id = messages.id
        WHERE message_parts.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    invalid_chunk = connection.execute(
        """
        SELECT 1
        FROM retrieval_chunks
        WHERE content_hash = '' OR index_version <> ?
        LIMIT 1
        """,
        (index_version,),
    ).fetchone()
    missing_chunk = connection.execute(
        """
        SELECT 1
        FROM message_parts
        LEFT JOIN retrieval_chunks
          ON retrieval_chunks.message_part_id = message_parts.id
        WHERE message_parts.status = 'resolved'
          AND message_parts.text_content <> ''
          AND retrieval_chunks.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    missing_graph_evidence = connection.execute(
        """
        SELECT 1
        WHERE NOT EXISTS (
            SELECT 1
            FROM graph_edge_evidence
            WHERE evidence_kind IN ('message', 'message_part')
        )
        """
    ).fetchone()
    return any(
        item is not None
        for item in (
            missing_part,
            invalid_chunk,
            missing_chunk,
            missing_graph_evidence,
        )
    )




def _set_projection_status(
    connection: sqlite3.Connection,
    status: str,
) -> None:
    connection.execute(
        "UPDATE source_accounts SET projection_status = ?",
        (status,),
    )
def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (name,),
    ).fetchone() is not None
def _apply_migrations(
    connection: sqlite3.Connection,
    *,
    migration_paths: Sequence[Path] | None = None,
) -> None:
    current_version = _current_schema_version(connection)
    migration_paths = tuple(migration_paths or _migration_paths())

    for migration_path in migration_paths:
        version = int(migration_path.name.split("_", 1)[0])
        if version <= current_version:
            continue

        script = migration_path.read_text(encoding="utf-8")
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{script}\n"
                "INSERT INTO app_meta(key, value) "
                f"VALUES ('schema_version', '{version}') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value;\n"
                "COMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        current_version = version


def _migration_paths() -> tuple[Path, ...]:
    return tuple(sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")))


def _target_schema_version(migration_paths: Sequence[Path]) -> int:
    return max(
        (int(path.name.split("_", 1)[0]) for path in migration_paths),
        default=0,
    )


def required_schema_version() -> int:
    return _target_schema_version(_migration_paths())


def current_schema_version(connection: sqlite3.Connection) -> int:
    return _current_schema_version(connection)


def _record_runtime_contract(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "app_meta"):
        return
    connection.executemany(
        """
        INSERT INTO app_meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (
            ("app_version", APP_VERSION),
            ("api_contract_version", API_CONTRACT_VERSION),
        ),
    )
    connection.commit()


def _backup_before_migration(
    connection: sqlite3.Connection,
    *,
    database_path: Path,
    schema_version: int,
    backup_dir: Path | None,
    clock: BackupClock | None,
) -> Path | None:
    destination_dir = backup_dir or _default_backup_dir(database_path)
    destination_dir.mkdir(parents=True, exist_ok=True)

    existing_backups = sorted(
        destination_dir.glob(f"{database_path.stem}.v{schema_version}.*.sqlite")
    )
    if existing_backups:
        return None

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = destination_dir / (
        f"{database_path.stem}.v{schema_version}.{timestamp}.sqlite"
    )
    with closing(sqlite3.connect(backup_path)) as backup_connection:
        connection.backup(backup_connection)
    return backup_path


def _default_backup_dir(database_path: Path) -> Path:
    if database_path.parent.name.casefold() == "library":
        return database_path.parent.parent / "backups"
    return database_path.parent / "backups"


def _current_schema_version(connection: sqlite3.Connection) -> int:
    has_meta = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'app_meta'
        """
    ).fetchone()
    if has_meta is None:
        return 0

    row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row is not None else 0
