"""Rebuild ChatOS message embeddings from source; explicitly prepare then cut over.

The work directory contains an immutable SQLite backup and a new derived table.
No old vector is converted. File-index rebuilding uses the local-model CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/gateway/src"))

MODEL = "text-embedding-qwen3-embedding-0.6b"
DIM = 1024


def connect_ro(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def source_rows(connection):
    return connection.execute(
        "SELECT e.message_id,e.conversation_id,m.content,e.created_at "
        "FROM message_embeddings e JOIN messages m ON m.id=e.message_id "
        "WHERE e.status='indexed' ORDER BY e.message_id"
    ).fetchall()


def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def configured_db(settings_file):
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    db = Path(raw["libraryDb"]).expanduser()
    return raw, (db if db.is_absolute() else settings_file.parent.parent / db).resolve()


def file_fingerprint(root):
    files = [(str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
             for p in sorted(root.rglob("*.md"))]
    return fingerprint(files)


def prepare(work, settings_file):
    from wechat_ai.services import embedding_service as es
    import numpy as np
    import yaml

    raw, db = configured_db(settings_file)
    knowledge = db.parent / "knowledge"
    work.mkdir(parents=True, exist_ok=True)
    snapshot = work / "source.sqlite"
    manifest_path = work / "manifest.json"
    if snapshot.exists() and not manifest_path.exists():
        raise RuntimeError("Unverified snapshot; use a fresh work directory")
    if not snapshot.exists():
        if manifest_path.exists() or (work / "vectors.sqlite").exists():
            raise RuntimeError("Incomplete work directory; use a fresh directory")
        shutil.copy2(settings_file, work / "settings.json")
        shutil.copy2(knowledge / "local-model-registry.yaml", work / "registry.yaml")
        partial = work / "source.partial.sqlite"
        with closing(connect_ro(db)) as source, closing(sqlite3.connect(partial)) as target:
            target.execute("PRAGMA journal_mode=OFF")
            target.execute("PRAGMA synchronous=OFF")
            started = time.monotonic()
            def progress(status, remaining, total):
                if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE):
                    print(json.dumps({"backup_status": status, "remaining": remaining}), flush=True)
                elif remaining % 16384 < 1024:
                    print(json.dumps({"stage": "backup", "remaining_pages": remaining, "total_pages": total}), flush=True)
                if time.monotonic() - started > 120:
                    raise RuntimeError("SQLite backup timed out; source may have an active writer")
            source.backup(target, pages=1024, progress=progress)
        partial.replace(snapshot)
    with closing(connect_ro(snapshot)) as source:
        rows = source_rows(source)
        indexed = source.execute("SELECT COUNT(*) FROM message_embeddings WHERE status='indexed'").fetchone()[0]
    if len(rows) != indexed:
        raise RuntimeError("Source contains orphan embeddings; resolve before migration")
    manifest = {"source": str(db), "settings": str(settings_file.resolve()),
                "source_fingerprint": fingerprint(rows), "rows": len(rows),
                "files_fingerprint": file_fingerprint(knowledge / "index"),
                "model": MODEL, "dimensions": DIM}
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise RuntimeError("Work directory belongs to a different source or corpus; use a fresh directory")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if settings_file.read_bytes() != (work / "settings.json").read_bytes():
        raise RuntimeError("Settings changed since snapshot; use a fresh work directory")
    registry = yaml.safe_load((work / "registry.yaml").read_text(encoding="utf-8"))
    kb = registry["knowledge_bases"]["chatos"]
    if Path(kb["root"]).resolve() != (knowledge / "index").resolve():
        raise RuntimeError("Registry points to a different source corpus")
    registry["text_backend"]["embedding"].update(model=MODEL, dimensions=DIM)
    kb.update(embed_model=MODEL, dimensions=DIM)
    shadow_registry = work / "registry-1024.yaml"
    if shadow_registry.exists():
        if yaml.safe_load(shadow_registry.read_text(encoding="utf-8")) != registry:
            raise RuntimeError("Shadow registry changed since prepare")
    else:
        shadow_registry.write_text(yaml.safe_dump(registry, sort_keys=False, allow_unicode=True), encoding="utf-8")
    es.MODEL, es._DIM = MODEL, DIM
    es.BASE = raw.get("llamaSwap", {}).get("retrievalBaseUrl", "http://127.0.0.1:9123/v1")
    assert es._embed_request(["dimension verification"]).shape == (1, DIM)
    with closing(sqlite3.connect(work / "vectors.sqlite")) as target:
        target.execute("PRAGMA journal_mode=WAL")
        target.execute("PRAGMA synchronous=NORMAL")
        target.execute("CREATE TABLE IF NOT EXISTS message_embeddings (message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, embedding BLOB NOT NULL CHECK(length(embedding)=4096), model_version TEXT NOT NULL CHECK(model_version='" + MODEL + "'), status TEXT NOT NULL CHECK(status='indexed'), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
        done = {r[0] for r in target.execute("SELECT message_id FROM message_embeddings")}
        pending = [row for row in rows if str(row[0]) not in done]
        for start in range(0, len(pending), 128):
            batch = pending[start:start + 128]
            texts = [es.clean_text(row[2] or "") for row in batch]
            if any(not text for text in texts):
                raise RuntimeError("Indexed source cleans to empty text; requires explicit disposition")
            vectors = es._embed_request(texts)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            now = int(time.time() * 1000)
            target.executemany("INSERT INTO message_embeddings VALUES (?,?,?,?,?,?,?)", [
                (r[0], r[1], v.astype(np.float32).tobytes(), MODEL, "indexed", r[3], now)
                for r, v in zip(batch, vectors, strict=True)
            ])
            target.commit()
            if start % 512 == 0:
                print(json.dumps({"stage": "sqlite", "done": len(done) + min(start + 128, len(pending)), "total": len(rows)}), flush=True)
        count = target.execute("SELECT COUNT(*) FROM message_embeddings").fetchone()[0]
        if count != len(rows):
            raise RuntimeError("SQLite shadow count mismatch")
    print(json.dumps({"stage": "sqlite_ready", "rows": count, "dimensions": DIM}), flush=True)


def cutover(work, settings_file):
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    raw, db = configured_db(settings_file)
    if (manifest.get("source"), manifest.get("settings"), manifest.get("model"), manifest.get("dimensions")) != (str(db), str(settings_file.resolve()), MODEL, DIM):
        raise RuntimeError("Manifest does not match the selected database/settings/vector space")
    if not db.is_file() or not (work / "vectors.sqlite").is_file():
        raise RuntimeError("Missing source or shadow database")
    if settings_file.read_bytes() != (work / "settings.json").read_bytes():
        raise RuntimeError("Settings changed since prepare")
    knowledge = db.parent / "knowledge"
    active = (knowledge / "lancedb").resolve()
    backup = (work / "previous-lancedb").resolve()
    shadow_lance = (work / "lancedb").resolve()
    if backup.exists() or active == shadow_lance or work == active or active in work.parents:
        raise RuntimeError("Unsafe or previously attempted cutover paths")
    if shadow_lance.parent != work or backup.parent != work or active.parent != knowledge:
        raise RuntimeError("Cutover path escaped the selected directories")
    if file_fingerprint(knowledge / "index") != manifest.get("files_fingerprint"):
        raise RuntimeError("File corpus changed since prepare; rebuild before cutover")
    report = json.loads((work / "file-index-report.json").read_text(encoding="utf-8-sig"))
    if report.get("files_failed") != 0 or report.get("errors"):
        raise RuntimeError("File index has failures or no completion evidence")
    if not isinstance(report.get("files_discovered"), int) or report["files_discovered"] <= 0 or (
        report.get("files_indexed", 0) + report.get("files_skipped_unchanged", 0) != report["files_discovered"]
    ):
        raise RuntimeError("File index completion count does not cover the source corpus")
    import lancedb
    import yaml
    registry = work / "registry-1024.yaml"
    config = yaml.safe_load(registry.read_text(encoding="utf-8"))
    if config["knowledge_bases"]["chatos"]["dimensions"] != DIM or config["knowledge_bases"]["chatos"]["embed_model"] != MODEL:
        raise RuntimeError("Shadow registry does not match the required vector space")
    table = lancedb.connect(str(shadow_lance)).open_table("kb_chatos")
    lance_count = table.count_rows()
    if table.schema.field("vector").type.list_size != DIM or lance_count == 0:
        raise RuntimeError("Shadow LanceDB failed schema/count check")
    if report.get("total_chunks_in_store") != lance_count or table.count_rows(f"model IS NULL OR model != '{MODEL}'"):
        raise RuntimeError("Shadow LanceDB does not match its model/completion report")
    if Path(config["knowledge_bases"]["chatos"]["root"]).resolve() != (knowledge / "index").resolve():
        raise RuntimeError("Shadow registry points to a different source corpus")
    del table
    import gc
    gc.collect()
    old_settings = settings_file.read_bytes()
    live_registry = knowledge / "local-model-registry.yaml"
    old_registry = live_registry.read_bytes()
    raw = json.loads(old_settings)
    raw.setdefault("indexing", {}).update(model=MODEL, dimensions=DIM)
    moved_old = moved_new = False
    with closing(sqlite3.connect(db, timeout=30, uri=True)) as live:
        live.execute("ATTACH DATABASE ? AS rebuilt", ((work / "vectors.sqlite").as_uri() + "?mode=ro",))
        live.execute("BEGIN IMMEDIATE")
        try:
            if fingerprint(source_rows(live)) != manifest["source_fingerprint"]:
                raise RuntimeError("Production source changed after snapshot; rebuild before cutover")
            count = live.execute("SELECT COUNT(*) FROM rebuilt.message_embeddings").fetchone()[0]
            if count != manifest["rows"]:
                raise RuntimeError("Incomplete SQLite shadow index")
            invalid = live.execute(
                "SELECT 1 FROM rebuilt.message_embeddings WHERE embedding IS NULL OR length(embedding) != ? "
                "OR model_version IS NULL OR model_version != ? OR status IS NULL OR status != 'indexed' LIMIT 1",
                (DIM * 4, MODEL),
            ).fetchone()
            mismatched = live.execute(
                "SELECT message_id,conversation_id FROM rebuilt.message_embeddings EXCEPT "
                "SELECT message_id,conversation_id FROM message_embeddings WHERE status='indexed'"
            ).fetchone()
            if invalid or mismatched:
                raise RuntimeError("Invalid or unrelated SQLite shadow vectors")
            live.execute("DELETE FROM message_embeddings WHERE status='indexed'")
            live.execute("INSERT INTO message_embeddings SELECT * FROM rebuilt.message_embeddings")
            active.rename(backup)
            moved_old = True
            shadow_lance.rename(active)
            moved_new = True
            settings_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            shutil.copy2(registry, live_registry)
            live.commit()
        except BaseException:
            live.rollback()
            settings_file.write_bytes(old_settings)
            live_registry.write_bytes(old_registry)
            if moved_new:
                active.rename(shadow_lance)
            if moved_old:
                backup.rename(active)
            raise
    result = {"status": "completed", "stage": "ready", "stats": {
        "files_discovered": report.get("files_discovered", 0),
        "total_chunks_in_store": lance_count,
    }, "error": None, "updatedAt": int(time.time() * 1000)}
    (knowledge / "index-status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "cutover_complete", "sqlite_rows": count, "lance_rows": lance_count, "dimensions": DIM}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "cutover"])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--settings", type=Path, default=ROOT / "apps/gateway/config/settings.json")
    args = parser.parse_args()
    (prepare if args.stage == "prepare" else cutover)(args.work.resolve(), args.settings.resolve())
