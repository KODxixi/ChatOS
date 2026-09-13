from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from urllib.request import Request


_CLIENT_PATH = Path(
    r"C:\Users\shiguanyu\.openclaw\agents\gchat\workspace\scripts\gchat_client.py"
)
_WORKSPACE = _CLIENT_PATH.parents[1]
_OPENCLAW_CONFIG = Path(r"C:\Users\shiguanyu\.openclaw\openclaw.json")


def _load_client() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gchat_client_under_test", _CLIENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, body: bytes, *, lines: list[bytes] | None = None) -> None:
        self.status = 200
        self._body = body
        self._lines = lines
        self.closed = False

    def getheaders(self):
        return []

    def read(self) -> bytes:
        return self._body

    def __iter__(self):
        return iter(self._lines or [])

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()


class _Opener:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float):
        self.requests.append(request)
        assert timeout == 10.0
        return self.responses.pop(0)


def test_ask_reads_ndjson_citations_after_health_and_preserves_score() -> None:
    client = _load_client()
    client._log = lambda _record: None
    opener = _Opener(
        [
            _Response(json.dumps({"status": "ok"}).encode()),
            _Response(
                b"",
                lines=[
                    b'{"type":"citations","items":[{"messageId":7,"score":0.91}]}\n',
                    b'{"type":"done"}\n',
                ],
            ),
        ]
    )
    client._OPENER = opener

    result = client.ask("蔡敏昨天找我干嘛")

    assert result == {
        "http": 200,
        "ok": True,
        "answer": "",
        "citations": [{"messageId": 7, "score": 0.91}],
        "done": True,
        "fallback": None,
    }
    assert [request.method for request in opener.requests] == ["GET", "POST"]
    assert opener.requests[1].full_url.endswith("/api/ask/stream")


def test_ask_returns_openclaw_fallback_on_error_event() -> None:
    client = _load_client()
    client._log = lambda _record: None
    opener = _Opener(
        [
            _Response(json.dumps({"status": "ok"}).encode()),
            _Response(
                b"",
                lines=[b'{"type":"error","error":{"code":"LM_OFFLINE"}}\n'],
            ),
        ]
    )
    client._OPENER = opener

    result = client.ask("无关问题")

    assert result["ok"] is False
    assert result["fallback"] == "openclaw"
    assert result["error"] == "chatbox_error"


def test_ask_rejects_chatbox_generation_delta_and_falls_back_to_openclaw() -> None:
    client = _load_client()
    client._log = lambda _record: None
    client._OPENER = _Opener(
        [
            _Response(json.dumps({"status": "ok"}).encode()),
            _Response(
                b"",
                lines=[b'{"type":"delta","text":"must-not-be-consumed"}\n'],
            ),
        ]
    )

    result = client.ask("项目")

    assert result["ok"] is False
    assert result["answer"] == ""
    assert result["fallback"] == "openclaw"
    assert result["error"] == "unexpected_generation_event"


def test_gchat_workspace_documents_one_non_conflicting_rag_contract() -> None:
    documents = {
        name: (_WORKSPACE / name).read_text(encoding="utf-8")
        for name in (
            "AGENTS.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "MEMORY.md",
            "SOUL.md",
            "SPEC.md",
            "TOOLS.md",
            "USER.md",
            "prompts/RAG任务书-v1.md",
            "prompts/wechat-rag-prompt.md",
        )
    }
    joined = "\n".join(documents.values())

    assert r"D:\Vault-assets\AI_Projects\.wechat-cli" in joined
    assert "http://127.0.0.1:8912" in joined
    assert "text-embedding-qwen3-embedding-8b" in joined
    assert "text-reranker-8b" in joined
    assert "bge-m3-q8_0" in joined
    assert "citations" in documents["prompts/RAG任务书-v1.md"]
    assert "不生成答案" in documents["prompts/RAG任务书-v1.md"]
    assert "Gchat/OpenClaw" in documents["prompts/wechat-rag-prompt.md"]

    assert "可调 Gchat /api/import/jobs" not in joined
    assert "按分类结构化入库" not in joined
    assert "可直读/写 chatbox SQLite" not in joined
    assert "待 G爸 分发" not in joined
    assert "Gchat 集成状态(待更新)" not in joined
    assert "Gchat 写 chatbox(完整)" not in joined


def test_gchat_runtime_model_scopes_match_workspace_contract() -> None:
    config = json.loads(_OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    agents = config["agents"]
    gchat = next(item for item in agents["list"] if item["id"] == "gchat")
    binding = next(
        item for item in config["bindings"] if item.get("agentId") == "gchat"
    )
    spec = (_WORKSPACE / "SPEC.md").read_text(encoding="utf-8")

    assert gchat.get("model") is None
    assert agents["defaults"]["model"]["primary"] == "deepseek/deepseek-v4-flash"
    assert agents["defaults"]["model"]["fallbacks"] == [
        "volcano/doubao-seed-evolving"
    ]
    assert gchat["memorySearch"]["model"] == "bge-m3-q8_0"
    assert binding["match"]["channel"] == "openclaw-weixin"
    assert "deepseek/deepseek-v4-flash" in spec
    assert "volcano/doubao-seed-evolving" in spec
    assert "bge-m3-q8_0" in spec
