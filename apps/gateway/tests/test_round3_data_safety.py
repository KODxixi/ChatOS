"""Exercise production paths against a disposable library, never the live vault."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import numpy as np
import pytest

from wechat_ai.database import initialize_database, recover_knowledge_indexes
from wechat_ai.services.conversation_service import update_conversation
from wechat_ai.services.knowledge_projection_service import project_knowledge
from wechat_ai.services import embedding_service as embedding


RAW = ("conversations", "messages", "message_parts", "assets")


def protect_raw(connection):
    attempts = []

    def authorize(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table in RAW:
            attempts.append((action, table))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return attempts


def snapshot(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in RAW}


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "library.sqlite"
    with closing(initialize_database(path)) as connection:
        for cid in ("alpha", "beta"):
            connection.execute(
                "INSERT INTO conversations(id,source_conversation_id,title,kind,message_count) VALUES (?,?,?,'direct',12)",
                (cid, cid, cid),
            )
            connection.execute(
                "INSERT INTO messages(conversation_id,dedupe_key,sender_name,sent_at,type,content) VALUES (?,?,'Tester',2000,'text','synthetic knowledge text')",
                (cid, cid),
            )
        # Explicit import fixture setup precedes the read-only runtime boundary.
        project_knowledge(connection)
        connection.commit()
    return path


def test_round2_authorizer_allows_read_and_derived_write():
    from test_round2_regression import _authorizer_deny_raw

    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript("CREATE TABLE conversations(id); CREATE TABLE conversation_overrides(id);")
        connection.set_authorizer(_authorizer_deny_raw)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        connection.execute("INSERT INTO conversation_overrides VALUES (1)")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("INSERT INTO conversations VALUES (1)")


def test_index_toggle_preserves_raw_and_rebuilds_only_derived(library):
    with closing(sqlite3.connect(library)) as connection:
        connection.row_factory = sqlite3.Row
        before = snapshot(connection)
        attempts = protect_raw(connection)
        for enabled in (False, True):
            result = update_conversation(connection, "alpha", {"isIndexingEnabled": enabled})
            assert result["isIndexingEnabled"] is enabled
            count = connection.execute("SELECT COUNT(*) FROM retrieval_chunks WHERE conversation_id='alpha'").fetchone()[0]
            assert count == int(enabled)
            assert snapshot(connection) == before
        assert attempts == []


def test_recovery_and_background_projection_never_reimport_raw(library):
    from wechat_ai.plugins.projection import apply

    with closing(sqlite3.connect(library)) as connection:
        connection.row_factory = sqlite3.Row
        before = snapshot(connection)
        attempts = protect_raw(connection)
        assert recover_knowledge_indexes(connection, force=True)
        assert attempts == []
        assert snapshot(connection) == before

    attempts = []

    def connect():
        connection = sqlite3.connect(library)
        connection.row_factory = sqlite3.Row
        attempts.append(protect_raw(connection))
        return connection

    registry = {}
    services = {"jobs.registry": registry, "db.connection_factory": connect}
    apply(SimpleNamespace(get=services.__getitem__, on=lambda *_: None))
    registry["retrieval"](None, None)
    assert attempts == [[]]


def test_recovery_does_not_backfill_missing_import_parts(library):
    with closing(sqlite3.connect(library)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DELETE FROM retrieval_chunks")
        connection.execute("DELETE FROM message_parts")
        connection.commit()
        before = snapshot(connection)
        attempts = protect_raw(connection)
        assert recover_knowledge_indexes(connection, force=True) is False
        assert snapshot(connection) == before
        assert attempts == []
        assert connection.execute("SELECT projection_status FROM source_accounts LIMIT 1").fetchone()[0] == "degraded"


@pytest.fixture
def vectors(library, monkeypatch):
    calls = []

    def embed(texts):
        calls.append(texts)
        return np.array([[1.0] + [0.0] * 1023 for _ in texts], dtype=np.float32)

    monkeypatch.setattr(embedding, "MODEL", "text-embedding-qwen3-embedding-0.6b")
    monkeypatch.setattr(embedding, "_DIM", 1024)
    monkeypatch.setattr(embedding, "_embed_request", embed)
    with closing(sqlite3.connect(library)) as connection:
        connection.execute("INSERT INTO conversation_overrides(conversation_id,is_indexing_enabled,updated_at) VALUES ('alpha',0,'test')")
        for mid, cid in connection.execute("SELECT id,conversation_id FROM messages"):
            connection.execute(
                "INSERT INTO message_embeddings(message_id,conversation_id,embedding,model_version,status,created_at,updated_at) VALUES (?,?,?,'text-embedding-qwen3-embedding-0.6b','indexed',0,0)",
                (str(mid), cid, np.array([1.0] + [0.0] * 1023, dtype=np.float32).tobytes()),
            )
        connection.commit()
    return library, calls


@pytest.mark.parametrize("scope,expected", [(None, ["beta"]), ("alpha", []), ("beta", ["beta"])])
def test_semantic_search_applies_effective_state(vectors, scope, expected):
    path, _ = vectors
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        hits = embedding.search(connection, "synthetic query", conversation_id=scope)
        assert [hit["conversationId"] for hit in hits] == expected


def test_disabled_conversation_cannot_be_indexed_directly(vectors):
    path, calls = vectors
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DELETE FROM message_embeddings WHERE conversation_id='alpha'")
        connection.commit()
    result = embedding.index_conversation(str(path), "alpha")
    assert result["messages"] == 0
    assert calls == []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        mid = connection.execute("SELECT id FROM messages WHERE conversation_id='alpha'").fetchone()[0]
        assert embedding.embed_and_store(connection, mid, "alpha", "synthetic knowledge") is False
        assert calls == []
        assert connection.execute("SELECT COUNT(*) FROM message_embeddings WHERE conversation_id='alpha'").fetchone()[0] == 0


def test_override_can_enable_raw_disabled_conversation(vectors):
    path, _ = vectors
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE conversations SET is_indexing_enabled=0 WHERE id='alpha'")
        connection.execute("UPDATE conversation_overrides SET is_indexing_enabled=1 WHERE conversation_id='alpha'")
        connection.commit()
    assert "alpha" in embedding._get_indexable_conversations(str(path))


@pytest.mark.parametrize("entry", ["single", "batch"])
def test_disabling_during_embedding_does_not_store_new_vectors(library, monkeypatch, entry):
    def disable_and_embed(texts):
        with closing(sqlite3.connect(library)) as other:
            other.execute("INSERT INTO conversation_overrides(conversation_id,is_indexing_enabled,updated_at) VALUES ('alpha',0,'test')")
            other.commit()
        return np.array([[1.0] + [0.0] * 1023 for _ in texts], dtype=np.float32)

    monkeypatch.setattr(embedding, "_embed_request", disable_and_embed)
    if entry == "batch":
        embedding.index_conversation(str(library), "alpha")
    else:
        with closing(sqlite3.connect(library)) as connection:
            mid = connection.execute("SELECT id FROM messages WHERE conversation_id='alpha'").fetchone()[0]
            assert embedding.embed_and_store(connection, mid, "alpha", "synthetic knowledge") is False
            connection.commit()
    with closing(sqlite3.connect(library)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM message_embeddings WHERE conversation_id='alpha'").fetchone()[0] == 0
