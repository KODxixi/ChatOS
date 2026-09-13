from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from .rag_prompt import (
    LOCAL_WECHAT_RAG_SYSTEM_PROMPT,
    build_rag_user_prompt,
)


OpenRequest = Callable[..., Any]
PromptBuilder = Callable[[str, Sequence[object]], str]
REMOTE_HISTORY_MAX_MESSAGES = 16
REMOTE_HISTORY_MAX_CONTENT_CHARS = 4_000
REMOTE_HISTORY_MAX_TOTAL_CHARS = 16_000


class ModelEndpointConfigurationError(ValueError):
    pass


class OpenAICompatibleUnavailable(RuntimeError):
    pass


class EvidenceLike(Protocol):
    message_id: int
    conversation_id: str
    sender_name: str
    sent_at: int
    excerpt: str
    score: float | None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    base_url: str
    is_remote: bool

    def privacy_fingerprint(self, policy_version: str) -> str:
        normalized_version = policy_version.strip()
        if not normalized_version:
            raise ValueError("policy version must not be blank")
        return hashlib.sha256(
            f"{self.base_url}\0{normalized_version}".encode("utf-8")
        ).hexdigest()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_without_redirects(request: Request, *, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def validate_model_endpoint(base_url: str) -> ModelEndpoint:
    if not isinstance(base_url, str):
        raise ModelEndpointConfigurationError("model endpoint is invalid")
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ModelEndpointConfigurationError("model endpoint is invalid")
    try:
        parsed.port
    except ValueError as exc:
        raise ModelEndpointConfigurationError(
            "model endpoint is invalid"
        ) from exc

    hostname = parsed.hostname.casefold()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback and parsed.scheme != "https":
        raise ModelEndpointConfigurationError(
            "remote model endpoint must use HTTPS"
        )
    return ModelEndpoint(
        base_url=normalized,
        is_remote=not is_loopback,
    )


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str | None,
        credential: str | None = None,
        opener: OpenRequest = _open_without_redirects,
        timeout: float = 15,
        system_prompt: str | None = None,
        user_prompt_builder: PromptBuilder | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.endpoint = validate_model_endpoint(base_url)
        self._model = model.strip() if model and model.strip() else None
        self._credential = credential if credential else None
        self._opener = opener
        self._timeout = timeout
        self._system_prompt = system_prompt
        self._user_prompt_builder = user_prompt_builder
        self._temperature = temperature
        self._max_tokens = max_tokens

    def stream_answer(
        self,
        question: str,
        contexts: Sequence[EvidenceLike],
        *,
        history: Sequence[Mapping[str, str]] = (),
        thinking: bool | None = None,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        usage_sink: Callable[[ModelUsage], None] | None = None,
    ) -> Iterator[str]:
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be a boolean")
        if reasoning_effort is not None and reasoning_effort not in {
            "low",
            "medium",
            "high",
        }:
            raise ValueError("reasoning effort is invalid")
        if reasoning_effort is not None and thinking is not True:
            raise ValueError("reasoning effort requires thinking")
        model = self._model or self.list_models()[0]
        system_content = (
            self._system_prompt
            if self._system_prompt is not None
            else LOCAL_WECHAT_RAG_SYSTEM_PROMPT
        )
        user_content = (
            self._user_prompt_builder(question, contexts)
            if self._user_prompt_builder is not None
            else build_rag_user_prompt(question, contexts)
        )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content,
                },
                *_safe_history(
                    trim_remote_history(history)
                    if self.endpoint.is_remote
                    else history
                ),
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "stream": True,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        if thinking is not None:
            payload["thinking"] = {
                "type": "enabled" if thinking else "disabled"
            }
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        request = self._request(
            "/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            accept="text/event-stream",
        )
        response = self._open(request)
        try:
            _require_success(response)
            try:
                for raw_line in response:
                    try:
                        line = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError as exc:
                        raise OpenAICompatibleUnavailable(
                            "model endpoint returned invalid stream data"
                        ) from exc
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise OpenAICompatibleUnavailable(
                            "model endpoint returned invalid stream data"
                        ) from exc
                    _raise_stream_error(event)
                    usage = _model_usage(event)
                    if usage is not None and usage_sink is not None:
                        usage_sink(usage)
                    content = _delta_content(event)
                    if content:
                        yield content
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                raise OpenAICompatibleUnavailable(
                    "model endpoint is unavailable"
                ) from exc
        finally:
            response.close()

    def list_models(self) -> list[str]:
        request = self._request(
            "/models",
            method="GET",
            accept="application/json",
        )
        response = self._open(request)
        try:
            _require_success(response)
            try:
                payload = json.loads(b"".join(response))
                items = payload["data"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise OpenAICompatibleUnavailable(
                    "model endpoint returned invalid model data"
                ) from exc
            models = [
                str(item["id"]).strip()
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].strip()
            ]
            if not models:
                raise OpenAICompatibleUnavailable(
                    "model endpoint reported no models"
                )
            return models
        finally:
            response.close()

    def _request(
        self,
        path: str,
        *,
        method: str,
        accept: str,
        data: bytes | None = None,
    ) -> Request:
        headers = {"Accept": accept}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self._credential is not None:
            headers["Authorization"] = f"Bearer {self._credential}"
        return Request(
            f"{self.endpoint.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )

    def _open(self, request: Request) -> Any:
        try:
            return self._opener(request, timeout=self._timeout)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise OpenAICompatibleUnavailable(
                "model endpoint is unavailable"
            ) from exc


def _safe_history(
    history: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            safe.append({"role": role, "content": content})
    return safe


def trim_remote_history(
    history: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    safe = _safe_history(history)[-REMOTE_HISTORY_MAX_MESSAGES:]
    remaining = REMOTE_HISTORY_MAX_TOTAL_CHARS
    retained_reversed: list[dict[str, str]] = []
    for item in reversed(safe):
        if remaining <= 0:
            break
        content = item["content"]
        allowed = min(
            len(content),
            REMOTE_HISTORY_MAX_CONTENT_CHARS,
            remaining,
        )
        if allowed <= 0:
            continue
        retained_reversed.append(
            {"role": item["role"], "content": content[:allowed]}
        )
        remaining -= allowed
    retained_reversed.reverse()
    return retained_reversed


def _require_success(response: Any) -> None:
    status = getattr(response, "status", 200)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise OpenAICompatibleUnavailable("model endpoint is unavailable")


def _delta_content(event: object) -> str | None:
    if not isinstance(event, dict):
        return None
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    return content if isinstance(content, str) else None


def _model_usage(event: object) -> ModelUsage | None:
    if not isinstance(event, dict) or not isinstance(event.get("usage"), dict):
        return None
    usage = event["usage"]
    values = (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        return None
    return ModelUsage(
        input_tokens=values[0],
        output_tokens=values[1],
        total_tokens=values[2],
    )


def _raise_stream_error(event: object) -> None:
    if isinstance(event, dict) and "error" in event:
        detail = event["error"]
        message = detail.get("message") if isinstance(detail, dict) else detail
        if isinstance(message, str) and message.strip():
            raise OpenAICompatibleUnavailable(message)
        raise OpenAICompatibleUnavailable("model endpoint request failed")


def _user_prompt(
    question: str,
    contexts: Sequence[EvidenceLike],
) -> str:
    return build_rag_user_prompt(question, contexts)
