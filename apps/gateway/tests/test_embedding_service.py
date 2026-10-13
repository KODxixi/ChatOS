from __future__ import annotations

import json
from urllib.request import Request

import pytest


@pytest.fixture(autouse=True)
def embedding_defaults(monkeypatch):
    from wechat_ai.services import embedding_service as es
    monkeypatch.setattr(es, "MODEL", "text-embedding-qwen3-embedding-0.6b")
    monkeypatch.setattr(es, "_DIM", 1024)


class FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


def test_embedding_client_batches_query_and_documents_for_cosine_scores() -> None:
    from wechat_ai.services.embedding_service import EmbeddingClient

    response = FakeResponse(
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0] + [0.0] * 1022},
                {"index": 1, "embedding": [1.0, 0.0] + [0.0] * 1022},
                {"index": 2, "embedding": [0.0, 1.0] + [0.0] * 1022},
            ]
        }
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 3.0
        return response

    client = EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
        timeout=3.0,
    )

    scores = client.similarities("query", ["same", "different"])

    assert scores == pytest.approx([1.0, 0.0])
    assert response.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:9123/v1/embeddings"
    body = json.loads(request.data or b"{}")
    assert body == {
        "model": "text-embedding-qwen3-embedding-0.6b",
        "input": ["query", "same", "different"],
    }


def test_embedding_client_defaults_to_the_llama_swap_embedding_model() -> None:
    from wechat_ai.services.embedding_service import EmbeddingClient

    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        return FakeResponse(
            {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0] + [0.0] * 1022},
                    {"index": 1, "embedding": [1.0, 0.0] + [0.0] * 1022},
                ]
            }
        )

    EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=open_request,
    ).similarities("query", ["document"])

    body = json.loads(requests[0].data or b"{}")
    assert body["model"] == "text-embedding-qwen3-embedding-0.6b"


def test_embedding_client_rejects_mismatched_dimensions() -> None:
    from wechat_ai.services.embedding_service import (
        EmbeddingClient,
        EmbeddingUnavailable,
    )

    response = FakeResponse(
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [1.0]},
            ]
        }
    )
    client = EmbeddingClient(
        "http://127.0.0.1:9123/v1",
        opener=lambda _request, *, timeout: response,
    )

    with pytest.raises(EmbeddingUnavailable):
        client.similarities("query", ["document"])


def test_existing_vector_space_blocks_query_and_write(monkeypatch):
    import sqlite3
    from wechat_ai.services import embedding_service as es
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE message_embeddings (status TEXT, model_version TEXT, embedding BLOB)")
        for model, dimension in [("old-model", 1024), (es.MODEL, 4096)]:
            conn.execute("DELETE FROM message_embeddings")
            conn.execute("INSERT INTO message_embeddings VALUES ('indexed',?,?)", (model, bytes(dimension * 4)))
            with pytest.raises(es.EmbeddingUnavailable, match="shadow"):
                es.search(conn, "query")
            with pytest.raises(es.EmbeddingUnavailable, match="shadow"):
                es.embed_and_store(conn, 1, "conversation", "message text")
            assert conn.execute("SELECT length(embedding) FROM message_embeddings").fetchone()[0] == dimension * 4


def test_registry_refresh_preserves_active_configuration(tmp_path):
    from wechat_ai.services.knowledge_archiver import _write_registry
    registry = tmp_path / "local-model-registry.yaml"
    registry.write_text("active-old-model-and-schema", encoding="utf-8")
    _write_registry(tmp_path)
    assert registry.read_text(encoding="utf-8") == "active-old-model-and-schema"


def test_new_registry_uses_explicit_endpoint_without_reading_host_settings(tmp_path, monkeypatch):
    from wechat_ai import config
    from wechat_ai.services.knowledge_archiver import _write_registry
    def forbidden(*args, **kwargs):
        raise AssertionError("host settings must not be loaded during archival")
    monkeypatch.setattr(config, "load_settings", forbidden)
    _write_registry(tmp_path, retrieval_base_url="http://127.0.0.1:9999/v1")
    value = (tmp_path / "local-model-registry.yaml").read_text(encoding="utf-8")
    assert "http://127.0.0.1:9999" in value
    assert "dimensions: 1024" in value


# ---------------------------------------------------------------------------
# P2-1 / P4-3: status denominator = eligible (filtered) set, not all text.
# ---------------------------------------------------------------------------

def _seed_filter_lib(tmp_path):
    from wechat_ai.database import initialize_database
    path = tmp_path / "lib.sqlite"
    conn = initialize_database(path)
    convs = [
        # id, title, kind, message_count, is_indexing_enabled, domain
        ("g_noise", "业主交流群", "group", 50, 1, "personal"),
        ("g_noise_force", "业主交流群", "group", 50, 1, "personal"),  # override=1 → kept
        ("g_large", "项目大群", "group", 50, 1, "project"),           # 50 senders → too_many
        ("g_tiny", "小群", "group", 3, 1, "personal"),                # message_count<=10
        ("d1", "张三", "direct", 20, 1, "personal"),                   # eligible
    ]
    for cid, title, kind, mc, en, dom in convs:
        conn.execute(
            "INSERT INTO conversations(id, source_conversation_id, title, kind, message_count, is_indexing_enabled, domain)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, cid, title, kind, mc, en, dom),
        )
    mid = 1
    for cid, ns in {"g_noise": 5, "g_noise_force": 5, "g_large": 50}.items():
        for i in range(ns):
            conn.execute(
                "INSERT INTO messages(id, conversation_id, sender_id, sender_name, sent_at, type, content, dedupe_key)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (mid, cid, f"wx_{cid}_{i}", f"用户{i}", 1_790_000_000_000 + i,
                 "text", f"足够长的内容{i}", f"dk-{mid}"),
            )
            mid += 1
    for i in range(3):
        conn.execute(
            "INSERT INTO messages(id, conversation_id, sender_id, sender_name, sent_at, type, content, dedupe_key)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mid, "d1", "wx_me", "我", 1_790_000_001_000 + i, "text", f"私聊内容足够长{i}", f"dk-{mid}"),
        )
        mid += 1
    # human explicitly turned the noise group back on
    conn.execute(
        "INSERT INTO conversation_overrides(conversation_id, is_indexing_enabled, domain, updated_at)"
        " VALUES (?,1,'personal','2026-10-13')",
        ("g_noise_force",),
    )
    conn.commit()
    return path, conn


def test_index_status_denominator_is_eligible_not_all_text(tmp_path) -> None:
    from wechat_ai.services import embedding_service as es
    _path, conn = _seed_filter_lib(tmp_path)
    status = es.get_index_status(conn)

    # eligible conversations = g_noise_force (explicit on) + d1 (direct)
    assert status["eligibleConversations"] == 2
    # eligible messages = 5 (forced noise group) + 3 (direct) = 8
    assert status["eligibleMessages"] == 8
    # 63 total text messages; only 8 are eligible → no longer a flat 29% story
    assert status["totalMessages"] == 63
    assert status["skippedByRule"] == 63 - 8
    assert status["indexed"] == 0
    assert status["completionPct"] == 0.0  # 0/8, not 0/63

    rule = status["filterRule"]
    assert len(rule["version"]) == 12
    assert rule["groupSizeThreshold"] == es.GROUP_SIZE_THRESHOLD
    bd = status["filterBreakdown"]
    assert bd["eligible"] == 1            # d1
    assert bd["explicit_enabled"] == 1    # g_noise_force
    assert bd["too_many_senders"] == 1    # g_large
    assert bd["noise_keyword"] == 1       # g_noise
    assert bd["too_few_messages"] == 1    # g_tiny
    assert bd["disabled_by_switch"] == 0


def test_explicit_override_beats_noise_and_size_rules(tmp_path) -> None:
    from wechat_ai.services import embedding_service as es
    _path, conn = _seed_filter_lib(tmp_path)
    ids, reasons, skip = es._indexable_ids_and_reasons(conn)
    # the noise group a human turned ON must survive, despite matching noise keyword
    assert "g_noise_force" in ids
    # the plain noise group / oversized group / tiny group are excluded
    assert "g_noise" not in ids
    assert "g_large" not in ids
    assert "g_tiny" not in ids
    assert "d1" in ids
    assert reasons["explicit_enabled"] == 1
    assert "g_noise_force" not in skip
    assert "g_noise" in skip


def test_periodic_sync_stall_resets_running_flag(monkeypatch) -> None:
    from wechat_ai.services import embedding_service as es
    with es._job_lock:
        es._JOB["running"] = True
        es._JOB["done"] = 5
        es._JOB["total"] = 100
    try:
        # no progress across polls → stall fires quickly with tiny timeout
        finished = es._wait_for_job_to_finish(timeout_sec=0.05, poll_sec=0.01)
        assert finished is False
        with es._job_lock:
            assert es._JOB["running"] is False
    finally:
        with es._job_lock:
            es._JOB["running"] = False
            es._JOB["done"] = 0
            es._JOB["total"] = 0


def test_periodic_sync_returns_true_when_job_finishes(monkeypatch) -> None:
    from wechat_ai.services import embedding_service as es
    with es._job_lock:
        es._JOB["running"] = False
    assert es._wait_for_job_to_finish(timeout_sec=1.0, poll_sec=0.01) is True
