from __future__ import annotations

import sqlite3
from pathlib import Path


def test_agent_schema_upgrade_is_additive_and_contains_no_credentials(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import MIGRATIONS_DIR, initialize_database

    database_path = tmp_path / "library.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            (MIGRATIONS_DIR / "001_initial.sql").read_text("utf-8")
        )
        connection.execute(
            """
            INSERT INTO conversations(
                id, source_conversation_id, title, kind, note
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy", "legacy-source", "Legacy", "direct", "keep-note"),
        )
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy", "legacy-message", 1_000, "text", "keep-message"),
        )
        connection.executescript(
            (MIGRATIONS_DIR / "002_knowledge_projection.sql").read_text(
                "utf-8"
            )
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value) VALUES ('schema_version', '2')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        connection.commit()
    finally:
        connection.close()

    upgraded = initialize_database(database_path)
    try:
        tables = {
            row[0]
            for row in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "model_profiles",
            "remote_model_send_consents",
            "agent_threads",
            "agent_turns",
            "agent_citations",
        } <= tables
        latest_version = max(
            int(path.name.split("_", 1)[0])
            for path in MIGRATIONS_DIR.glob("*.sql")
        )
        assert upgraded.execute(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == str(latest_version)
        assert tuple(
            upgraded.execute(
                "SELECT title, note FROM conversations WHERE id = 'legacy'"
            ).fetchone()
        ) == ("Legacy", "keep-note")
        assert upgraded.execute(
            "SELECT content FROM messages WHERE dedupe_key = 'legacy-message'"
        ).fetchone()[0] == "keep-message"

        for table in ("model_profiles", "agent_threads", "agent_turns"):
            column_names = {
                str(row[1]).casefold()
                for row in upgraded.execute(f"PRAGMA table_info({table})")
            }
            assert not {
                "api_key",
                "apikey",
                "password",
                "secret",
                "token",
            } & column_names
        assert "project_id" in {
            str(row[1]).casefold()
            for row in upgraded.execute("PRAGMA table_info(agent_threads)")
        }
    finally:
        upgraded.close()
