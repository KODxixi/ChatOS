from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request

import pytest


class FakeResponse:
    def __init__(self, lines: list[bytes], *, status: int = 200) -> None:
        self._lines = lines
        self.status = status
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class Evidence:
    message_id: int = 7
    part_id: int | None = 9
    conversation_id: str = "conversation-alpha"
    sender_name: str = "Alice"
    sent_at: int = 1_700_000_000_000
    excerpt: str = "Synthetic local evidence"
    score: float | None = 0.87


@pytest.mark.parametrize(
    "base_url",
    [
        "http://models.example/v1",
        "https://user:pass@models.example/v1",
        "https://models.example/v1?key=secret",
        "https://models.example/v1#fragment",
    ],
)
def test_endpoint_policy_rejects_unsafe_remote_urls(base_url: str) -> None:
    from wechat_ai.openai_compatible import (
        ModelEndpointConfigurationError,
        validate_model_endpoint,
    )

    with pytest.raises(ModelEndpointConfigurationError):
        validate_model_endpoint(base_url)


def test_endpoint_policy_classifies_remote_and_binds_privacy_fingerprint() -> None:
    from wechat_ai.openai_compatible import validate_model_endpoint

    endpoint = validate_model_endpoint("https://models.example/v1/")

    assert endpoint.base_url == "https://models.example/v1"
    assert endpoint.is_remote is True
    assert endpoint.privacy_fingerprint("privacy-v1") == hashlib.sha256(
        b"https://models.example/v1\x00privacy-v1"
    ).hexdigest()
    assert (
        validate_model_endpoint("http://127.0.0.1:1234/v1").is_remote
        is False
    )


def test_remote_client_sends_profile_credential_and_streams_answer() -> None:
    from wechat_ai.openai_compatible import OpenAICompatibleClient

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

    client = OpenAICompatibleClient(
        "https://models.example/v1",
        model="remote-model",
        credential="synthetic-secret",
        opener=open_request,
    )

    assert list(
        client.stream_answer(
            "What changed?",
            [Evidence()],
            history=[{"role": "user", "content": "Earlier question"}],
        )
    ) == ["First ", "answer"]
    assert response.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://models.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer synthetic-secret"
    payload = json.loads(request.data or b"{}")
    assert payload["model"] == "remote-model"
    assert payload["messages"][1] == {
        "role": "user",
        "content": "Earlier question",
    }
    prompt = payload["messages"][-1]["content"]
    assert "Synthetic local evidence" in prompt
    assert "part=9" in prompt
    system_prompt = payload["messages"][0]["content"]
    assert "Retrieved Context" in system_prompt
    assert "相关度得分" in system_prompt
    assert "以时间最晚的对话为准" in system_prompt
    assert "根据本地微信记录，未查找到相关讨论。" in system_prompt
    assert "时间: 2023-11-15 06:13" in prompt
    assert "发送者: Alice" in prompt
    assert "相关度得分: 0.87" in prompt


def test_stream_reports_usage_only_when_provider_returns_it() -> None:
    from wechat_ai.openai_compatible import ModelUsage, OpenAICompatibleClient

    response = FakeResponse(
        [
            b'data: {"choices":[{"delta":{"content":"answer"}}]}\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":21,"completion_tokens":8,"total_tokens":29}}\n',
            b"data: [DONE]\n",
        ]
    )
    usages: list[ModelUsage] = []
    client = OpenAICompatibleClient(
        "http://127.0.0.1:1234/v1",
        model="local-model",
        opener=lambda _request, *, timeout: response,
    )

    assert list(
        client.stream_answer("Question", [Evidence()], usage_sink=usages.append)
    ) == ["answer"]
    assert usages == [ModelUsage(input_tokens=21, output_tokens=8, total_tokens=29)]


@pytest.mark.parametrize(
    ("thinking", "reasoning_effort", "expected"),
    [
        (False, None, {"thinking": {"type": "disabled"}}),
        (
            True,
            "high",
            {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            },
        ),
    ],
)
def test_deepseek_thinking_contract_is_added_to_chat_payload(
    thinking: bool,
    reasoning_effort: str | None,
    expected: dict[str, object],
) -> None:
    from wechat_ai.openai_compatible import OpenAICompatibleClient

    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 15
        return FakeResponse([b"data: [DONE]\n"])

    client = OpenAICompatibleClient(
        "https://api.deepseek.com",
        model="deepseek-v4-flash",
        credential="synthetic-secret",
        opener=open_request,
    )

    assert list(
        client.stream_answer(
            "Analyze this",
            [Evidence()],
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
    ) == []

    payload = json.loads(requests[0].data or b"{}")
    for key, value in expected.items():
        assert payload[key] == value
    if reasoning_effort is None:
        assert "reasoning_effort" not in payload


def test_remote_history_keeps_latest_eight_rounds_with_bounded_content() -> None:
    from wechat_ai.openai_compatible import trim_remote_history

    short_history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index}",
        }
        for index in range(20)
    ]
    latest_rounds = trim_remote_history(short_history)
    assert len(latest_rounds) == 16
    assert [item["content"] for item in latest_rounds] == [
        f"turn-{index}" for index in range(4, 20)
    ]

    long_history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{index}:" + ("x" * 5_000),
        }
        for index in range(20)
    ]

    trimmed = trim_remote_history(long_history)

    assert len(trimmed) <= 16
    assert all(len(item["content"]) <= 4_000 for item in trimmed)
    assert sum(len(item["content"]) for item in trimmed) <= 16_000
    assert trimmed[-1]["content"].startswith("19:")


def test_stream_ignores_reasoning_content_and_yields_only_final_content() -> None:
    from wechat_ai.openai_compatible import OpenAICompatibleClient

    response = FakeResponse(
        [
            b'data: {"choices":[{"delta":{"reasoning_content":"private chain"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"final answer"}}]}\n',
            b"data: [DONE]\n",
        ]
    )

    client = OpenAICompatibleClient(
        "https://api.deepseek.com",
        model="deepseek-v4-flash",
        credential="synthetic-secret",
        opener=lambda _request, timeout: response,
    )

    assert list(
        client.stream_answer(
            "Analyze this",
            [Evidence()],
            thinking=True,
            reasoning_effort="high",
        )
    ) == ["final answer"]


def test_remote_client_rejects_redirect_without_retrying_or_leaking_secret() -> None:
    from wechat_ai.openai_compatible import (
        OpenAICompatibleClient,
        OpenAICompatibleUnavailable,
    )

    requests: list[Request] = []

    def redirect(
        request: Request,
        *,
        timeout: float,
    ) -> FakeResponse:
        requests.append(request)
        raise HTTPError(
            request.full_url,
            302,
            "redirect to https://evil.example synthetic-secret",
            {},
            None,
        )

    client = OpenAICompatibleClient(
        "https://models.example/v1",
        model="remote-model",
        credential="synthetic-secret",
        opener=redirect,
    )

    with pytest.raises(
        OpenAICompatibleUnavailable,
        match="model endpoint is unavailable",
    ) as captured:
        list(client.stream_answer("Question", [Evidence()]))

    assert len(requests) == 1
    assert requests[0].full_url.startswith("https://models.example/")
    assert "evil.example" not in str(captured.value)
    assert "synthetic-secret" not in str(captured.value)


def test_remote_client_closes_response_when_consumer_cancels() -> None:
    from wechat_ai.openai_compatible import OpenAICompatibleClient

    response = FakeResponse(
        [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"late"}}]}\n',
        ]
    )

    def open_request(_request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 15
        return response

    stream = OpenAICompatibleClient(
        "https://models.example/v1",
        model="remote-model",
        credential="synthetic-secret",
        opener=open_request,
    ).stream_answer("Question", [Evidence()])

    assert next(stream) == "partial"
    stream.close()

    assert response.closed is True


def test_stream_error_surfaces_server_message_for_overflow_detection() -> None:
    from wechat_ai.openai_compatible import (
        OpenAICompatibleClient,
        OpenAICompatibleUnavailable,
    )

    response = FakeResponse(
        [
            b'data: {"error": {"message": "request (9000 tokens) exceeds the '
            b"available context size (8192 tokens)\"}}\n",
        ]
    )
    client = OpenAICompatibleClient(
        "https://models.example/v1",
        model="remote-model",
        credential="synthetic-secret",
        opener=lambda _request, timeout: response,
    )

    with pytest.raises(OpenAICompatibleUnavailable) as captured:
        list(client.stream_answer("Question", [Evidence()]))

    assert "exceeds the available context size" in str(captured.value)
