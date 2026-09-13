from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from wechat_ai.database import initialize_database
from wechat_ai.services.graph_service import project_graph
from wechat_ai.services.knowledge_projection_service import project_knowledge
from wechat_ai.services.model_profile_service import ModelProfileService


class TrackingCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.has_calls: list[str] = []

    def has(self, profile_id: str) -> bool:
        self.has_calls.append(profile_id)
        return profile_id in self.values

    def get(self, profile_id: str) -> str | None:
        self.get_calls.append(profile_id)
        return self.values.get(profile_id)

    def set(self, profile_id: str, value: str) -> None:
        self.values[profile_id] = value

    def delete(self, profile_id: str) -> None:
        self.values.pop(profile_id, None)


class RecordingProvider:
    def __init__(
        self,
        responses: Sequence[str | BaseException | Sequence[object]],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def stream_answer(
        self,
        question: str,
        contexts: Sequence[object],
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> Iterator[str]:
        self.calls.append(
            {
                "question": question,
                "contexts": list(contexts),
                "history": list(history),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if not isinstance(response, str):
            yield from response
            return
        midpoint = len(response) // 2
        yield response[:midpoint]
        yield response[midpoint:]


def _connection_factory(path: Path):
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    return connect


def _model_service(
    path: Path,
    credentials: TrackingCredentialStore,
) -> ModelProfileService:
    return ModelProfileService(
        _connection_factory(path),
        credentials,
        clock=lambda: 1_700_000_000_000,
    )


def _insert_profile(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    base_url: str,
    auth_kind: str,
    is_default: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO model_profiles(
            id, name, provider, base_url, model, auth_kind, is_default,
            last_status, created_at, updated_at
        ) VALUES (?, ?, 'openai-compatible', ?, 'test-model', ?, ?,
                  'unknown', 1, 1)
        """,
        (
            profile_id,
            profile_id,
            base_url,
            auth_kind,
            int(is_default),
        ),
    )


def _seed_fact(
    connection: sqlite3.Connection,
    *,
    content: str = "Project Atlas will launch in October.",
) -> int:
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
            conversation_id, dedupe_key, sender_id, sender_name,
            sent_at, type, content
        ) VALUES ('team', 'team:1', 'alice', 'Alice', 1000, 'text', ?)
        """,
        (content,),
    )
    project_knowledge(connection)
    project_graph(connection)
    connection.commit()
    return int(
        connection.execute(
            """
            SELECT message_parts.id
            FROM message_parts
            JOIN messages ON messages.id = message_parts.message_id
            WHERE messages.conversation_id = 'team'
            """
        ).fetchone()[0]
    )


def _semantic_service(
    path: Path,
    credentials: TrackingCredentialStore,
    provider: RecordingProvider,
):
    try:
        from wechat_ai.services.semantic_graph_service import (
            SemanticGraphService,
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"semantic graph service is missing: {exc}")

    provider_calls: list[tuple[str, str | None]] = []

    def provider_factory(profile, credential):
        provider_calls.append((profile.id, credential))
        return provider

    return (
        SemanticGraphService(
            model_profile_service=_model_service(path, credentials),
            provider_factory=provider_factory,
            clock=lambda: 1_700_000_000_123,
            batch_size=10,
        ),
        provider_calls,
    )


def test_migration_adds_incremental_semantic_state_without_touching_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    with closing(initialize_database(path)) as connection:
        chunk_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(retrieval_chunks)"
            )
        }
        account_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_accounts)"
            )
        }

    assert {"semantic_content_hash", "semantic_version"} <= chunk_columns
    assert {
        "semantic_graph_status",
        "semantic_graph_error_code",
        "semantic_graph_updated_at",
    } <= account_columns


def test_remote_only_profiles_skip_without_credentials_or_provider_calls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    provider = RecordingProvider([])
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="remote-default",
            base_url="https://models.example/v1",
            auth_kind="bearer",
            is_default=True,
        )
        connection.commit()
        service, provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code,
                   projection_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()
        chunk = connection.execute(
            """
            SELECT semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert tuple(account) == ("skipped", None, "ready")
    assert tuple(chunk) == (None, None)
    assert credentials.get_calls == []
    assert credentials.has_calls == []
    assert provider_calls == []
    assert provider.calls == []


def test_valid_local_output_creates_only_evidence_backed_mentions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://127.0.0.1:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["Launch plan"],
                                "entities": ["Project Atlas"],
                            }
                        ]
                    }
                )
            ]
        )
        service, provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        rows = connection.execute(
            """
            SELECT target.kind, target.label, edges.kind, edges.weight,
                   evidence.evidence_kind, evidence.evidence_id
            FROM graph_edges AS edges
            JOIN graph_nodes AS target ON target.id = edges.target_node_id
            JOIN graph_edge_evidence AS evidence
              ON evidence.edge_id = edges.id
            WHERE edges.id LIKE 'semantic_mentions:%'
            ORDER BY target.kind
            """
        ).fetchall()
        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code,
                   projection_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()
        chunk = connection.execute(
            """
            SELECT content_hash, semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert [tuple(row) for row in rows] == [
        ("entity", "Project Atlas", "mentions", 1, "message_part", str(part_id)),
        ("topic", "Launch plan", "mentions", 1, "message_part", str(part_id)),
    ]
    assert tuple(account) == ("ready", None, "ready")
    assert chunk[1] == chunk[0]
    assert chunk[2] == 1
    assert provider_calls == [("local-default", None)]
    assert len(provider.calls) == 1
    assert [
        context.part_id for context in provider.calls[0]["contexts"]
    ] == [part_id]
    assert provider.calls[0]["history"] == []


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        json.dumps({"no_items": True}),
        json.dumps({"items": "not-a-list"}),
    ],
)
def test_top_level_damage_is_skipped_without_graph_damage(
    tmp_path: Path,
    response: str,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        if '"messagePartId": 1' in response:
            response = response.replace(
                '"messagePartId": 1',
                f'"messagePartId": {part_id}',
            )
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        deterministic_counts = tuple(
            connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM messages),
                    (SELECT COUNT(*) FROM graph_edges),
                    (SELECT COUNT(*) FROM graph_edge_evidence)
                """
            ).fetchone()
        )
        provider = RecordingProvider([response])
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        current_counts = tuple(
            connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM messages),
                    (SELECT COUNT(*) FROM graph_edges),
                    (SELECT COUNT(*) FROM graph_edge_evidence)
                """
            ).fetchone()
        )
        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code,
                   projection_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()
        chunk = connection.execute(
            """
            SELECT semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert current_counts == deterministic_counts
    assert tuple(account) == ("ready", None, "ready")
    assert tuple(chunk) is not None
    assert chunk[0] is not None and chunk[1] == 1


def test_malformed_items_are_dropped_but_valid_neighbours_survive(
    tmp_path: Path,
) -> None:
    """A local model may emit hallucinated/duplicate part ids, extra items or a
    non-list labels payload. Such malformed items are dropped per-item (they
    never write graph facts) while valid items in the same response survive;
    the account stays ready instead of degrading on one noisy batch."""
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["Good topic"],
                                "entities": ["Alice"],
                            },
                            {
                                "messagePartId": 999_999,
                                "topics": ["Forged"],
                                "entities": [],
                            },
                            {"messagePartId": part_id, "topics": ["Dup"]},
                            {"topics": ["Missing part id"]},
                            {"messagePartId": 1, "topics": "not-a-list"},
                        ]
                    }
                )
            ]
        )
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        rows = connection.execute(
            """
            SELECT kind, label
            FROM graph_nodes
            WHERE trace_kind IN ('semantic_topic', 'semantic_entity')
            ORDER BY kind, label
            """
        ).fetchall()
        account = connection.execute(
            """
            SELECT semantic_graph_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()[0]
        chunk = connection.execute(
            """
            SELECT semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert [tuple(row) for row in rows] == [
        ("entity", "Alice"),
        ("topic", "Good topic"),
    ]
    assert account == "ready"
    assert chunk[0] is not None and chunk[1] == 1


def test_extra_labels_are_truncated_not_rejected(
    tmp_path: Path,
) -> None:
    """Local models may emit more than the cap when a message names many
    entities; excess labels are truncated to the cap instead of failing the
    whole account, while per-label validation still applies."""
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": [
                                    f"topic-{index}" for index in range(12)
                                ],
                                "entities": ["One", "Two"],
                            }
                        ]
                    }
                )
            ]
        )
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        rows = connection.execute(
            """
            SELECT COUNT(*)
            FROM graph_nodes
            WHERE kind = 'topic' AND trace_kind = 'semantic_topic'
            """
        ).fetchone()[0]
        account = connection.execute(
            """
            SELECT semantic_graph_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()[0]

    assert rows == 8
    assert account == "ready"


def test_malformed_labels_are_dropped_without_failing_account(
    tmp_path: Path,
) -> None:
    """A single oversize/control-character label from a noisy local model is
    dropped instead of failing the whole account; valid labels survive."""
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": [
                                    "Good topic",
                                    "x" * 65,
                                    "bad\u0007control",
                                    "",
                                    "  ",
                                    123,
                                ],
                                "entities": ["Alice"],
                            }
                        ]
                    }
                )
            ]
        )
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        rows = connection.execute(
            """
            SELECT label
            FROM graph_nodes
            WHERE kind = 'topic' AND trace_kind = 'semantic_topic'
            """
        ).fetchall()
        entity = connection.execute(
            """
            SELECT label
            FROM graph_nodes
            WHERE kind = 'entity' AND trace_kind = 'semantic_entity'
            """
        ).fetchone()
        account = connection.execute(
            """
            SELECT semantic_graph_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()[0]

    assert [row[0] for row in rows] == ["Good topic"]
    assert entity is not None and entity[0] == "Alice"
    assert account == "ready"


def test_missing_topics_or_entities_field_is_treated_as_empty(
    tmp_path: Path,
) -> None:
    """A local model may omit an empty topics/entities array for a message with
    nothing to extract (e.g. a sticker-only reply). A missing field must not
    fail the whole account; an explicitly non-list value still does."""
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["Greeting"],
                            }
                        ]
                    }
                )
            ]
        )
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        account = connection.execute(
            """
            SELECT semantic_graph_status
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()[0]
        nodes = connection.execute(
            """
            SELECT COUNT(*) FROM graph_nodes
            WHERE trace_kind IN ('semantic_topic', 'semantic_entity')
            """
        ).fetchone()[0]

    assert account == "ready"
    assert nodes == 1


def test_invalid_stream_delta_is_skipped_without_failing_account(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider([["{\"items\":", object()]])
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)

        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()

    assert tuple(account) == ("ready", None)


def test_repeated_run_is_idempotent_and_zero_hits_are_marked_processed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider([json.dumps({"items": []})])
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)
        first_state = tuple(
            connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM graph_nodes
                     WHERE trace_kind IN ('semantic_topic', 'semantic_entity')),
                    (SELECT COUNT(*) FROM graph_edges
                     WHERE id LIKE 'semantic_mentions:%'),
                    (SELECT semantic_version FROM retrieval_chunks
                     WHERE message_part_id = ?)
                """,
                (part_id,),
            ).fetchone()
        )
        service.enrich(connection)
        second_state = tuple(
            connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM graph_nodes
                     WHERE trace_kind IN ('semantic_topic', 'semantic_entity')),
                    (SELECT COUNT(*) FROM graph_edges
                     WHERE id LIKE 'semantic_mentions:%'),
                    (SELECT semantic_version FROM retrieval_chunks
                     WHERE message_part_id = ?)
                """,
                (part_id,),
            ).fetchone()
        )

    assert first_state == (0, 0, 1)
    assert second_state == first_state
    assert len(provider.calls) == 1


def test_no_pending_chunks_stay_ready_when_model_credential_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="bearer",
            is_default=True,
        )
        credentials.values["local-default"] = "synthetic-secret"
        connection.commit()
        provider = RecordingProvider([json.dumps({"items": []})])
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )

        service.enrich(connection)
        credentials.values.clear()
        service.enrich(connection)

        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()
        chunk = connection.execute(
            """
            SELECT semantic_content_hash, content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert tuple(account) == ("ready", None)
    assert tuple(chunk) == (chunk[1], chunk[1], 1)


def test_model_failure_degrades_only_accounts_with_pending_semantic_work(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        _seed_fact(connection)
        connection.execute(
            """
            INSERT INTO source_accounts(
                id, source_app_id, external_account_id, display_name, status
            ) VALUES (
                'wechat:already-ready', 'wechat', 'already-ready',
                'Already ready', 'ready'
            )
            """
        )
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="bearer",
            is_default=True,
        )
        connection.commit()
        service, provider_calls = _semantic_service(
            path,
            credentials,
            RecordingProvider([]),
        )

        result = service.enrich(connection)

        statuses = {
            str(row[0]): (str(row[1]), row[2])
            for row in connection.execute(
                """
                SELECT id, semantic_graph_status, semantic_graph_error_code
                FROM source_accounts ORDER BY id
                """
            )
        }

    assert result.ready_accounts == 1
    assert result.degraded_accounts == 1
    assert statuses == {
        "wechat:already-ready": ("ready", None),
        "wechat:default": ("degraded", "MODEL_UNAVAILABLE"),
    }
    assert provider_calls == []


def test_failed_refresh_preserves_old_semantics_and_can_recover(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://localhost:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["Old topic"],
                                "entities": [],
                            }
                        ]
                    }
                ),
                RuntimeError("raw provider failure must not be stored"),
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["New topic"],
                                "entities": [],
                            }
                        ]
                    }
                ),
            ]
        )
        service, _provider_calls = _semantic_service(
            path,
            credentials,
            provider,
        )
        service.enrich(connection)
        old_semantic_hash = str(
            connection.execute(
                """
                SELECT semantic_content_hash FROM retrieval_chunks
                WHERE message_part_id = ?
                """,
                (part_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE retrieval_chunks
            SET text_content = 'Changed source text',
                content_hash = 'changed-content-hash'
            WHERE message_part_id = ?
            """,
            (part_id,),
        )
        connection.commit()

        service.enrich(connection)

        failed_labels = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT label FROM graph_nodes
                WHERE trace_kind IN ('semantic_topic', 'semantic_entity')
                ORDER BY label
                """
            )
        ]
        failed_state = connection.execute(
            """
            SELECT
                source_accounts.semantic_graph_status,
                source_accounts.semantic_graph_error_code,
                source_accounts.projection_status,
                retrieval_chunks.semantic_content_hash
            FROM source_accounts
            JOIN conversation_origins
              ON conversation_origins.source_account_id = source_accounts.id
            JOIN retrieval_chunks
              ON retrieval_chunks.conversation_id =
                 conversation_origins.conversation_id
            WHERE retrieval_chunks.message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

        service.enrich(connection)

        recovered_labels = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT label FROM graph_nodes
                WHERE trace_kind IN ('semantic_topic', 'semantic_entity')
                ORDER BY label
                """
            )
        ]
        recovered_state = connection.execute(
            """
            SELECT
                source_accounts.semantic_graph_status,
                source_accounts.semantic_graph_error_code,
                source_accounts.projection_status,
                retrieval_chunks.semantic_content_hash,
                retrieval_chunks.semantic_version
            FROM source_accounts
            JOIN conversation_origins
              ON conversation_origins.source_account_id = source_accounts.id
            JOIN retrieval_chunks
              ON retrieval_chunks.conversation_id =
                 conversation_origins.conversation_id
            WHERE retrieval_chunks.message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert failed_labels == ["Old topic"]
    assert tuple(failed_state) == (
        "degraded",
        "MODEL_UNAVAILABLE",
        "ready",
        old_semantic_hash,
    )
    assert "raw provider failure" not in str(tuple(failed_state))
    assert recovered_labels == ["New topic"]
    assert tuple(recovered_state) == (
        "ready",
        None,
        "ready",
        "changed-content-hash",
        1,
    )


def _add_group_speakers(
    connection,
    *,
    count: int,
    conversation_id: str = "team",
) -> None:
    for index in range(count):
        connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_id, sender_name,
                sent_at, type, content
            ) VALUES (?, ?, ?, ?, ?, 'text', 'noise')
            """,
            (
                conversation_id,
                f"{conversation_id}:fill:{index}",
                f"speaker-{index}",
                f"Speaker {index}",
                2000 + index,
            ),
        )


def test_chunks_in_very_large_group_are_marked_without_model_calls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _add_group_speakers(connection, count=30)  # Alice + 30 => 31 speakers
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://127.0.0.1:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider([])
        service, provider_calls = _semantic_service(path, credentials, provider)

        result = service.enrich(connection)

        chunk = connection.execute(
            """
            SELECT content_hash, semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()
        account = connection.execute(
            """
            SELECT semantic_graph_status, semantic_graph_error_code
            FROM source_accounts WHERE id = 'wechat:default'
            """
        ).fetchone()

    assert provider.calls == []
    assert provider_calls == []
    assert result.processed_chunks == 0
    assert tuple(chunk) == (chunk[0], chunk[0], 1)
    assert tuple(account) == ("ready", None)


def test_group_with_exactly_thirty_speakers_is_still_enriched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        part_id = _seed_fact(connection)
        _add_group_speakers(connection, count=29)  # Alice + 29 => exactly 30
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://127.0.0.1:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
        provider = RecordingProvider(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "messagePartId": part_id,
                                "topics": ["Standup"],
                                "entities": ["Project Atlas"],
                            }
                        ]
                    }
                )
            ]
        )
        service, provider_calls = _semantic_service(path, credentials, provider)

        service.enrich(connection)

        chunk = connection.execute(
            """
            SELECT semantic_content_hash, semantic_version
            FROM retrieval_chunks WHERE message_part_id = ?
            """,
            (part_id,),
        ).fetchone()

    assert provider_calls == [("local-default", None)]
    assert len(provider.calls) == 1
    assert tuple(chunk) == (chunk[0], 1)


def _context_overflow_error() -> BaseException:
    import io
    from urllib.error import HTTPError

    return HTTPError(
        "http://127.0.0.1:1234/v1/chat/completions",
        400,
        "Bad Request",
        {},
        io.BytesIO(b"request exceeds the available context size (8192 tokens)"),
    )


def _chunk(part_id: int) -> object:
    from wechat_ai.services.semantic_graph_service import _Chunk

    return _Chunk(
        id=part_id,
        message_part_id=part_id,
        message_id=part_id,
        conversation_id="room",
        source_account_id="wechat:default",
        content_hash=f"hash-{part_id}",
        text_content="x" * 400,
        sender_name="Alice",
        sent_at=1000,
    )


def test_context_overflow_batch_is_halved_and_retried_not_degraded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://127.0.0.1:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()
    events = [
        _context_overflow_error(),
        json.dumps(
            {"items": [{"messagePartId": 101, "topics": ["T1"], "entities": ["E1"]}]}
        ),
        json.dumps(
            {"items": [{"messagePartId": 102, "topics": ["T2"], "entities": ["E2"]}]}
        ),
    ]
    call_sizes: list[int] = []

    class OverflowProvider:
        def stream_answer(self, question, contexts, *, history=()):
            call_sizes.append(len(contexts))
            event = events.pop(0)
            if isinstance(event, BaseException):
                raise event
            yield event

    service, _provider_calls = _semantic_service(
        path, credentials, RecordingProvider([])
    )

    items = service._process_batch(OverflowProvider(), [_chunk(101), _chunk(102)])

    assert call_sizes == [2, 1, 1]
    assert sorted(item.message_part_id for item in items) == [101, 102]


def test_single_chunk_context_overflow_is_skipped_not_degraded(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.semantic_graph_service import _SemanticBatchSkip

    path = tmp_path / "library.sqlite"
    credentials = TrackingCredentialStore()
    with closing(initialize_database(path)) as connection:
        _insert_profile(
            connection,
            profile_id="local-default",
            base_url="http://127.0.0.1:1234/v1",
            auth_kind="none",
            is_default=True,
        )
        connection.commit()

    class OverflowProvider:
        def stream_answer(self, question, contexts, *, history=()):
            raise _context_overflow_error()

    service, _provider_calls = _semantic_service(
        path, credentials, RecordingProvider([])
    )

    with pytest.raises(_SemanticBatchSkip):
        service._process_batch(OverflowProvider(), [_chunk(101)])
