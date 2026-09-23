from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_migration_009_adds_operations_tables_and_agent_grounding_columns(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(tmp_path / "library.sqlite")
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "background_jobs",
            "wechat_source_key_refs",
            "project_assignment_candidates",
            "project_assignment_candidate_evidence",
        } <= tables
        assert connection.execute(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0] == "22"

        agent_turn_columns = {
            str(row[1]): str(row[4])
            for row in connection.execute("PRAGMA table_info(agent_turns)")
        }
        assert agent_turn_columns["model_options_json"] == "'{}'"
        assert agent_turn_columns["grounding_status"] == "'unknown'"
        assert agent_turn_columns["retrieval_summary_json"] == "'{}'"

        key_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(wechat_source_key_refs)")
        }
        assert key_columns == {
            "key_ref",
            "database_id",
            "salt_hash",
            "verified_at",
            "credential_target",
        }
        assert not {"key", "enc_key", "secret", "path"} & key_columns
    finally:
        connection.close()


def test_operations_schema_enforces_locked_enums_candidates_and_evidence_cascade(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database

    connection = initialize_database(tmp_path / "library.sqlite")
    try:
        base_job = (
            "job-1",
            "import",
            "queued",
            "queued",
            0,
            None,
            "manual",
            None,
            0,
            "{}",
            "2026-08-02T00:00:00Z",
            None,
            None,
            "2026-08-02T00:00:00Z",
            None,
        )
        connection.execute(
            """
            INSERT INTO background_jobs(
                id, type, status, stage, progress_current, progress_total,
                trigger_source, error_code, retry_count, payload_json,
                created_at, started_at, finished_at, updated_at,
                cancel_requested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            base_job,
        )
        for invalid_job in (
            ("job-type-invalid", "unknown") + base_job[2:],
            ("job-2", base_job[1], "unknown") + base_job[3:],
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO background_jobs(
                        id, type, status, stage, progress_current, progress_total,
                        trigger_source, error_code, retry_count, payload_json,
                        created_at, started_at, finished_at, updated_at,
                        cancel_requested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    invalid_job,
                )

        connection.execute(
            """
            INSERT INTO project_assignment_candidates(
                id, conversation_id, suggested_project_id,
                suggested_project_name, source_turn_id, confidence,
                model_profile_id, source_hash, status, created_at, reviewed_at
            ) VALUES (
                'candidate-1', 'conversation-1', NULL, 'Launch', NULL, 0.8,
                NULL, 'source-hash-1', 'suggested', '2026-08-02T00:00:00Z', NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO project_assignment_candidate_evidence(
                id, candidate_id, message_id, part_id, created_at
            ) VALUES (
                'evidence-1', 'candidate-1', 'message-1', NULL,
                '2026-08-02T00:00:00Z'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO project_assignment_candidates(
                    id, conversation_id, suggested_project_id,
                    suggested_project_name, confidence, source_hash,
                    status, created_at
                ) VALUES (
                    'candidate-invalid', 'conversation-1', NULL, NULL,
                    0.8, 'source-hash-invalid', 'suggested',
                    '2026-08-02T00:00:00Z'
                )
                """
            )
        connection.execute(
            "DELETE FROM project_assignment_candidates WHERE id = 'candidate-1'"
        )
        assert connection.execute(
            "SELECT 1 FROM project_assignment_candidate_evidence"
        ).fetchone() is None
    finally:
        connection.close()
