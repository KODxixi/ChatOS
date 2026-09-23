"""Disposable migration checks; run with the local-model Python dependencies."""
import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/migrate_vectors_1024.py"
spec = importlib.util.spec_from_file_location("vector_migration", SCRIPT)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.parametrize("outcome", ["complete", "source_changed", "file_failed", "rollback"])
def test_shadow_migration_is_bound_and_preserves_source(tmp_path, monkeypatch, outcome):
    lancedb = pytest.importorskip("lancedb", reason="migration needs local-model dependencies")
    import pyarrow as pa
    import yaml
    from wechat_ai.services import embedding_service as es

    gateway = tmp_path / "gateway"
    settings = gateway / "config/settings.json"
    settings.parent.mkdir(parents=True)
    db = gateway / "data/library.sqlite"
    db.parent.mkdir()
    settings.write_text(json.dumps({"libraryDb": "data/library.sqlite"}), encoding="utf-8")
    original_settings = settings.read_bytes()
    knowledge = db.parent / "knowledge"
    (knowledge / "index").mkdir(parents=True)
    (knowledge / "index/sample.md").write_text("synthetic architecture text", encoding="utf-8")
    (knowledge / "local-model-registry.yaml").write_text("old-registry", encoding="utf-8")
    (knowledge / "lancedb").mkdir()
    (knowledge / "lancedb/old-marker").touch()
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("""
            CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT);
            INSERT INTO messages VALUES (1,'synthetic architecture message');
            CREATE TABLE message_embeddings(message_id TEXT PRIMARY KEY, conversation_id TEXT,
              embedding BLOB, model_version TEXT, status TEXT, created_at INTEGER, updated_at INTEGER);
            INSERT INTO message_embeddings VALUES ('1','team',x'00000000','old','indexed',0,0);
            INSERT INTO message_embeddings VALUES ('2','team',x'','old','pending',0,0);
        """)
        conn.commit()
    monkeypatch.setattr(es, "MODEL", migration.MODEL)
    monkeypatch.setattr(es, "_DIM", migration.DIM)
    monkeypatch.setattr(es, "BASE", "http://127.0.0.1:9123/v1")
    monkeypatch.setattr(es, "_embed_request", lambda texts: np.array([[1.0] + [0.0] * 1023 for _ in texts], dtype=np.float32))
    work = tmp_path / "work"
    migration.prepare(work, settings)
    migration.prepare(work, settings)  # Resumes without adding duplicate rows.
    with closing(sqlite3.connect(work / "vectors.sqlite")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM message_embeddings").fetchone()[0] == 1
    registry = {"knowledge_bases": {"chatos": {
        "root": str(knowledge / "index"), "dimensions": 1024, "embed_model": migration.MODEL,
    }}}
    (work / "registry-1024.yaml").write_text(yaml.safe_dump(registry), encoding="utf-8")
    schema = pa.schema([pa.field("vector", pa.list_(pa.float32(), 1024)), pa.field("model", pa.string())])
    table = lancedb.connect(work / "lancedb").create_table("kb_chatos", schema=schema)
    table.add([{"vector": [1.0] + [0.0] * 1023, "model": migration.MODEL}])
    del table
    report = {"files_failed": int(outcome == "file_failed"), "total_chunks_in_store": 1, "errors": []}
    (work / "file-index-report.json").write_text(json.dumps(report), encoding="utf-8")

    if outcome == "source_changed":
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE messages SET content='new source'")
            conn.commit()
    if outcome == "rollback":
        real_copy = migration.shutil.copy2
        def fail_registry_copy(source, target):
            if Path(target) == knowledge / "local-model-registry.yaml":
                raise OSError("injected cutover failure")
            return real_copy(source, target)
        monkeypatch.setattr(migration.shutil, "copy2", fail_registry_copy)

    if outcome == "complete":
        migration.cutover(work, settings)
        assert (work / "previous-lancedb/old-marker").exists()
        assert json.loads(settings.read_text())["indexing"]["dimensions"] == 1024
    else:
        with pytest.raises((RuntimeError, OSError)):
            migration.cutover(work, settings)
        assert settings.read_bytes() == original_settings
        assert (knowledge / "local-model-registry.yaml").read_text() == "old-registry"
        assert (knowledge / "lancedb/old-marker").exists()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT status FROM message_embeddings WHERE message_id='2'").fetchone()[0] == "pending"
        model, width = conn.execute("SELECT model_version,length(embedding) FROM message_embeddings WHERE message_id='1'").fetchone()
        assert (model, width) == ((migration.MODEL, 4096) if outcome == "complete" else ("old", 4))
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == ("new source" if outcome == "source_changed" else "synthetic architecture message")


def test_prepare_refuses_unverified_snapshot(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"libraryDb": "missing.sqlite"}), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / "source.sqlite").touch()
    with pytest.raises(RuntimeError, match="Unverified snapshot"):
        migration.prepare(work, settings)
