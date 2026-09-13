from __future__ import annotations

import json
from collections.abc import Iterator
from urllib.error import URLError
from urllib.request import Request

import pytest


class FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.closed = False
        self.status = 200

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class FailingStreamResponse(FakeResponse):
    def __init__(self, error: BaseException) -> None:
        super().__init__([])
        self._error = error

    def __iter__(self) -> Iterator[bytes]:
        raise self._error
        yield b""  # pragma: no cover - keeps this method an iterator


def test_lm_studio_stream_parses_openai_sse_and_closes_response() -> None:
    from wechat_ai.lm_studio import LmStudioClient
    from wechat_ai.services.answer_service import Evidence

    response = FakeResponse(
        [
            b'data: {"choices":[{"delta":{"content":"First "}}]}\n',
            b'data: {"choices":[{"delta":{"content":"answer"}}]}\n',
            b"data: [DONE]\n",
        ]
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 15
        return response

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        model="local-test-model",
        opener=open_request,
    )
    evidence = [
        Evidence(
            message_id=7,
            conversation_id="synthetic-room",
            sender_name="Synthetic Sender",
            sent_at=1_700_000_000_000,
            excerpt="Synthetic source text",
        )
    ]

    assert list(client.stream_answer("What was decided?", evidence)) == [
        "First ",
        "answer",
    ]
    assert response.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:1234/v1/chat/completions"
    body = json.loads(request.data or b"{}")
    assert body["model"] == "local-test-model"
    assert body["stream"] is True
    assert "Synthetic source text" in body["messages"][1]["content"]
    assert "Retrieved Context" in body["messages"][0]["content"]
    assert "按时间先后顺序" in body["messages"][0]["content"]
    assert "相关度得分" in body["messages"][1]["content"]


def test_rag_user_prompt_formats_gmt8_score_and_fallback_exactly() -> None:
    from wechat_ai.rag_prompt import build_rag_user_prompt
    from wechat_ai.services.answer_service import Evidence

    prompt = build_rag_user_prompt(
        "发生了什么？",
        [
            Evidence(
                message_id=7,
                conversation_id="synthetic-room",
                sender_name="蔡敏",
                sent_at=1_700_000_000_000,
                excerpt="第一条",
                relevance_score=0.956,
            ),
            Evidence(
                message_id=8,
                conversation_id="synthetic-room",
                sender_name="G爸",
                sent_at=1_700_000_060_000,
                excerpt="第二条",
            ),
        ],
    )

    assert "[检索上下文开始]" in prompt
    assert "[检索上下文结束]" in prompt
    assert "[Block 1] | 时间: 2023-11-15 06:13 | 发送者: 蔡敏 | 相关度得分: 0.96" in prompt
    assert "内容: 第一条" in prompt
    assert "[Block 2] | 时间: 2023-11-15 06:14 | 发送者: G爸 | 相关度得分: N/A" in prompt
    assert prompt.endswith("用户问题：发生了什么？")


@pytest.mark.parametrize(
    "network_error",
    [
        TimeoutError("timed out"),
        OSError("connection reset"),
        URLError("connection lost"),
    ],
    ids=["timeout", "os-error", "url-error"],
)
def test_lm_studio_maps_stream_read_network_failures_to_unavailable(
    network_error: BaseException,
) -> None:
    from wechat_ai.lm_studio import LmStudioClient, LmStudioUnavailable

    response = FailingStreamResponse(network_error)

    def open_request(_request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        return response

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        model="local-test-model",
        opener=open_request,
    )

    with pytest.raises(LmStudioUnavailable, match="unavailable"):
        list(client.stream_answer("Question", []))

    assert response.closed is True


def test_lm_studio_maps_sse_error_frame_to_unavailable() -> None:
    from wechat_ai.lm_studio import LmStudioClient, LmStudioUnavailable

    response = FakeResponse(
        [
            b'data: {"error":{"code":"server_error",'
            b'"message":"synthetic upstream failure"}}\n'
        ]
    )

    def open_request(_request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        return response

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        model="local-test-model",
        opener=open_request,
    )

    with pytest.raises(LmStudioUnavailable, match="request failed"):
        list(client.stream_answer("Question", []))

    assert response.closed is True


def test_lm_studio_stream_closes_upstream_when_consumer_cancels() -> None:
    from wechat_ai.lm_studio import LmStudioClient

    response = FakeResponse(
        [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"late"}}]}\n',
        ]
    )

    def open_request(_request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        return response

    stream = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        model="local-test-model",
        opener=open_request,
    ).stream_answer("Question", [])

    assert next(stream) == "partial"
    stream.close()

    assert response.closed is True


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com/v1",
        "http://192.168.1.20:1234/v1",
        "file:///tmp/model",
    ],
)
def test_lm_studio_rejects_non_loopback_endpoints(base_url: str) -> None:
    from wechat_ai.lm_studio import LmStudioConfigurationError, LmStudioClient

    with pytest.raises(LmStudioConfigurationError):
        LmStudioClient(base_url, model="local-test-model")


def test_lm_studio_discovers_first_local_model_when_not_configured() -> None:
    from wechat_ai.lm_studio import LmStudioClient

    model_response = FakeResponse(
        [json.dumps({"data": [{"id": "first-local-model"}]}).encode("utf-8")]
    )
    stream_response = FakeResponse([b"data: [DONE]\n"])
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 15
        return model_response if request.data is None else stream_response

    client = LmStudioClient(
        "http://localhost:1234/v1",
        opener=open_request,
    )

    assert list(client.stream_answer("Question", [])) == []
    assert [request.full_url for request in requests] == [
        "http://localhost:1234/v1/models",
        "http://localhost:1234/v1/chat/completions",
    ]
    request_body = json.loads(requests[1].data or b"{}")
    assert request_body["model"] == "first-local-model"


def test_lm_studio_rediscovers_stale_automatic_model_and_retries_once() -> None:
    from wechat_ai.lm_studio import LmStudioClient

    responses = iter(
        [
            FakeResponse(
                [json.dumps({"data": [{"id": "stale-model"}]}).encode()]
            ),
            FakeResponse(
                [
                    b'data: {"error":{"code":"model_not_found",'
                    b'"message":"model does not exist"}}\n'
                ]
            ),
            FakeResponse(
                [json.dumps({"data": [{"id": "fresh-model"}]}).encode()]
            ),
            FakeResponse(
                [
                    b'data: {"choices":[{"delta":{"content":"fresh"}}]}\n',
                    b"data: [DONE]\n",
                ]
            ),
        ]
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        requests.append(request)
        return next(responses)

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        opener=open_request,
    )

    assert list(client.stream_answer("Question", [])) == ["fresh"]
    assert [request.full_url for request in requests] == [
        "http://127.0.0.1:1234/v1/models",
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://127.0.0.1:1234/v1/models",
        "http://127.0.0.1:1234/v1/chat/completions",
    ]
    chat_models = [
        json.loads(request.data or b"{}")["model"]
        for request in requests
        if request.data is not None
    ]
    assert chat_models == ["stale-model", "fresh-model"]


def test_lm_studio_retries_stale_automatic_model_at_most_once() -> None:
    from wechat_ai.lm_studio import LmStudioClient, LmStudioUnavailable

    model_error = (
        b'data: {"error":{"code":"model_not_found",'
        b'"message":"model does not exist"}}\n'
    )
    responses = iter(
        [
            FakeResponse(
                [json.dumps({"data": [{"id": "stale-model"}]}).encode()]
            ),
            FakeResponse([model_error]),
            FakeResponse(
                [json.dumps({"data": [{"id": "also-stale"}]}).encode()]
            ),
            FakeResponse([model_error]),
        ]
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        requests.append(request)
        return next(responses)

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        opener=open_request,
    )

    with pytest.raises(LmStudioUnavailable, match="model"):
        list(client.stream_answer("Question", []))

    assert len(requests) == 4


def test_lm_studio_does_not_retry_explicit_missing_model() -> None:
    from wechat_ai.lm_studio import LmStudioClient, LmStudioUnavailable

    response = FakeResponse(
        [
            b'data: {"error":{"code":"model_not_found",'
            b'"message":"model does not exist"}}\n'
        ]
    )
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        requests.append(request)
        return response

    client = LmStudioClient(
        "http://127.0.0.1:1234/v1",
        model="configured-model",
        opener=open_request,
    )

    with pytest.raises(LmStudioUnavailable, match="model"):
        list(client.stream_answer("Question", []))

    assert len(requests) == 1
