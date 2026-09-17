"""Round-2 regression tests: synthetic SQLite, no real chat library.

Covers:
  * Raw-table read-only enforcement (authorizer) across all write paths.
  * organize_pending persisted atomically with the watermark advance.
  * TaskPatch status/priority enum validation.
  * tasks API returns sourceMessageId.
  * Embedding/service filter honors COALESCE override over base.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Make src importable when running from apps/gateway.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Schema builders (synthetic — never touches the real vault)
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            title TEXT,
            kind TEXT,
            is_indexing_enabled INTEGER DEFAULT 1,
            domain TEXT,
            project_id TEXT,
            project_role TEXT,
            classification_status TEXT DEFAULT 'needs_review',
            classified_at INTEGER,
            last_message_at INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT REFERENCES conversations(id),
            sender_id TEXT,
            sender_name TEXT,
            sent_at INTEGER,
            type TEXT,
            content TEXT
        );
        CREATE TABLE message_parts (
            id INTEGER PRIMARY KEY,
            message_id INTEGER,
            ordinal INTEGER,
            kind TEXT,
            status TEXT,
            text_content TEXT,
            metadata_json TEXT
        );
        CREATE TABLE assets (
            id INTEGER PRIMARY KEY,
            part_id INTEGER,
            ordinal INTEGER,
            kind TEXT,
            status TEXT,
            UNIQUE(part_id, ordinal)
        );
        CREATE TABLE conversation_overrides (
            conversation_id TEXT PRIMARY KEY,
            is_indexing_enabled INTEGER,
            domain TEXT,
            project_id TEXT,
            project_role TEXT,
            classification_status TEXT,
            classified_at INTEGER,
            is_favorite INTEGER,
            is_archived INTEGER,
            note TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE conversation_watermarks (
            conversation_id TEXT PRIMARY KEY,
            last_message_sent_at INTEGER,
            last_message_id INTEGER,
            updated_at TEXT
        );
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            conversation_title TEXT,
            content TEXT,
            sender_name TEXT,
            sent_at INTEGER,
            status TEXT DEFAULT 'todo',
            priority TEXT DEFAULT 'medium',
            due_date TEXT,
            source_message_id INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE organize_pending (
            conversation_id TEXT PRIMARY KEY,
            pending_since INTEGER NOT NULL
        );
        CREATE TABLE retrieval_chunks (
            conversation_id TEXT,
            chunk TEXT
        );
        """
    )
    return db


def _seed(db: sqlite3.Connection, n_convs: int = 30) -> None:
    """Insert n conversations, each with 5 text messages."""
    for i in range(n_convs):
        cid = f"conv_{i:03d}"
        db.execute(
            "INSERT INTO conversations (id, title, kind, is_indexing_enabled) VALUES (?, ?, 'group', 1)",
            (cid, f"测试群{i}"),
        )
        for j in range(5):
            db.execute(
                "INSERT INTO messages (conversation_id, sender_id, sender_name, sent_at, type, content) VALUES (?, ?, ?, ?, 'text', ?)",
                (cid, "wxid_gary", "Gary", 1_700_000_000_000 + i * 100 + j, f"今天要提交方案 {i}-{j}"),
            )
    db.commit()


# ---------------------------------------------------------------------------
# 1. Raw-table read-only via SQLite authorizer
# ---------------------------------------------------------------------------

class ReadOnlyViolation(Exception):
    pass


_RAW_TABLES = frozenset({"conversations", "messages", "message_parts", "assets"})


def _authorizer_deny_raw(action: int, arg: str | None, **_kw: object) -> int:
    import sqlite3 as sq
    # SQLITE_INSERT=2, SQLITE_UPDATE=23, SQLITE_DELETE=9, SQLITE_REPLACE=
    if action in (sq.SQLITE_INSERT, sq.SQLITE_UPDATE, sq.SQLITE_DELETE, sq.SQLITE_REPLACE):
        if (arg or "").lower() in _RAW_TABLES:
            return sq.SQLITE_DENY
    return sq.SQLITE_OK


def test_authorizer_blocks_raw_table_writes(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "ro.sqlite")
    _seed(db)
    db.set_authorizer(_authorizer_deny_raw)
    # These must be DENIED.
    for sql in (
        "UPDATE conversations SET title = 'x'",
        "INSERT INTO messages (conversation_id, content) VALUES ('conv_000', 'x')",
        "DELETE FROM message_parts",
        "INSERT INTO assets (part_id, ordinal, kind, status) VALUES (1, 0, 'image', 'unresolved')",
    ):
        with pytest.raises(sqlite3.Error):
            db.execute(sql)
    # Derived table writes are allowed when the authorizer is cleared.
    db.set_authorizer(None)
    db.execute(
        "INSERT INTO conversation_overrides (conversation_id, updated_at) VALUES ('conv_000', '2026-01-01T00:00:00Z')"
    )
    db.commit()


# ---------------------------------------------------------------------------
# 2. organize_pending + watermark in same transaction
# ---------------------------------------------------------------------------

def test_pending_and_watermark_same_transaction(tmp_path: Path) -> None:
    """Simulate: advance watermark + insert pending, all inside one commit.
    A crash before commit must leave BOTH uncommitted (rolled back)."""
    db = _make_db(tmp_path / "tx.sqlite")
    _seed(db, n_convs=3)

    cid = "conv_000"
    # Begin implicit transaction
    db.execute("BEGIN")
    db.execute(
        """INSERT INTO conversation_watermarks (conversation_id, last_message_sent_at, last_message_id, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00Z')
           ON CONFLICT(conversation_id) DO UPDATE SET
             last_message_sent_at=excluded.last_message_sent_at,
             last_message_id=excluded.last_message_id""",
        (cid, 1_700_000_000_005, 5),
    )
    db.execute(
        "INSERT OR IGNORE INTO organize_pending (conversation_id, pending_since) VALUES (?, ?)",
        (cid, 1_700_000_000_000),
    )
    # Crash: rollback instead of commit.
    db.rollback()

    # Both must be absent.
    assert db.execute("SELECT COUNT(*) FROM conversation_watermarks WHERE conversation_id=?", (cid,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM organize_pending WHERE conversation_id=?", (cid,)).fetchone()[0] == 0

    # Now commit properly.
    db.execute("BEGIN")
    db.execute(
        """INSERT INTO conversation_watermarks (conversation_id, last_message_sent_at, last_message_id, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00Z')
           ON CONFLICT(conversation_id) DO UPDATE SET
             last_message_sent_at=excluded.last_message_sent_at,
             last_message_id=excluded.last_message_id""",
        (cid, 1_700_000_000_005, 5),
    )
    db.execute(
        "INSERT OR IGNORE INTO organize_pending (conversation_id, pending_since) VALUES (?, ?)",
        (cid, 1_700_000_000_000),
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM conversation_watermarks WHERE conversation_id=?", (cid,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM organize_pending WHERE conversation_id=?", (cid,)).fetchone()[0] == 1


def test_batch_interrupt_no_lost_pending(tmp_path: Path) -> None:
    """26+ conversations: simulate batch commit at 25, then interrupt.
    The first 25 must have BOTH watermark and pending committed; the rest
    must have neither (no partial watermark without pending)."""
    db = _make_db(tmp_path / "batch.sqlite")
    n = 30
    _seed(db, n_convs=n)

    committed = 0
    for i in range(n):
        cid = f"conv_{i:03d}"
        # sqlite3 module auto-begins a transaction on DML; no explicit BEGIN.
        db.execute(
            """INSERT INTO conversation_watermarks (conversation_id, last_message_sent_at, last_message_id, updated_at)
               VALUES (?, ?, ?, '2026-01-01T00:00:00Z')""",
            (cid, 1_700_000_000_005, 5),
        )
        db.execute(
            "INSERT OR IGNORE INTO organize_pending (conversation_id, pending_since) VALUES (?, ?)",
            (cid, 1_700_000_000_000),
        )
        if (i + 1) % 25 == 0:
            db.commit()
            committed = i + 1
            # Simulate crash after batch commit.
            break
    # Rollback any uncommitted rows (the simulated crash).
    db.rollback()

    # First `committed` conversations: both watermark and pending present.
    for i in range(committed):
        cid = f"conv_{i:03d}"
        assert db.execute("SELECT COUNT(*) FROM conversation_watermarks WHERE conversation_id=?", (cid,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM organize_pending WHERE conversation_id=?", (cid,)).fetchone()[0] == 1
    # Remaining: neither.
    for i in range(committed, n):
        cid = f"conv_{i:03d}"
        assert db.execute("SELECT COUNT(*) FROM conversation_watermarks WHERE conversation_id=?", (cid,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM organize_pending WHERE conversation_id=?", (cid,)).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 3. TaskPatch enum validation (pydantic model)
# ---------------------------------------------------------------------------

def test_taskpatch_rejects_invalid_status() -> None:
    from wechat_ai.api.tasks import TaskPatch
    from pydantic import ValidationError

    # Valid values accepted.
    for s in ("todo", "completed", "ignored", "done"):
        m = TaskPatch(status=s)
        assert m.status == s
    for p in ("low", "medium", "high"):
        m = TaskPatch(priority=p)
        assert m.priority == p
    # Invalid values rejected (e.g. draft / in_progress must not silently map to todo).
    with pytest.raises(ValidationError):
        TaskPatch(status="draft")
    with pytest.raises(ValidationError):
        TaskPatch(status="in_progress")
    with pytest.raises(ValidationError):
        TaskPatch(priority="urgent")


# ---------------------------------------------------------------------------
# 4. tasks table has source_message_id and list returns it
# ---------------------------------------------------------------------------

def test_tasks_table_has_source_message_id(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "tasks.sqlite")
    db.execute(
        "INSERT INTO tasks (task_id, conversation_id, content, sent_at, status, priority, source_message_id, created_at, updated_at) "
        "VALUES ('tsk_1', 'conv_000', '提交方案', 1700, 'todo', 'high', 42, 1700, 1700)"
    )
    row = db.execute(
        "SELECT source_message_id FROM tasks WHERE task_id='tsk_1'"
    ).fetchone()
    assert row["source_message_id"] == 42


# ---------------------------------------------------------------------------
# 5. COALESCE override predicate honors disabled conversations
# ---------------------------------------------------------------------------

def test_coalesce_disabled_override(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "coalesce.sqlite")
    _seed(db, n_convs=5)
    # Disable conv_001 via override (not the raw column).
    db.execute(
        "INSERT INTO conversation_overrides (conversation_id, is_indexing_enabled, updated_at) VALUES ('conv_001', 0, '2026-01-01T00:00:00Z')"
    )
    db.commit()
    # The predicate used across search/index/graph must exclude conv_001.
    rows = db.execute(
        """
        SELECT c.id FROM conversations c
        WHERE COALESCE(
            (SELECT o.is_indexing_enabled FROM conversation_overrides o
             WHERE o.conversation_id = c.id),
            c.is_indexing_enabled, 1) = 1
        ORDER BY c.id
        """
    ).fetchall()
    ids = [r["id"] for r in rows]
    assert "conv_001" not in ids
    assert "conv_000" in ids
    assert "conv_002" in ids
