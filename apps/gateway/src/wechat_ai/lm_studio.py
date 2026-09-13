from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .rag_prompt import LOCAL_WECHAT_RAG_SYSTEM_PROMPT, build_rag_user_prompt

if TYPE_CHECKING:
    from .services.answer_service import Evidence


OpenRequest = Callable[..., Any]


class LmStudioError(RuntimeError):
    pass


class LmStudioConfigurationError(LmStudioError):
    pass


class LmStudioUnavailable(LmStudioError):
    pass


class _LmStudioModelNotFound(LmStudioUnavailable):
    pass


class LmStudioClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str | None = None,
        opener: OpenRequest = urlopen,
        timeout: float = 15,
    ) -> None:
        self._base_url = _validated_loopback_url(base_url)
        self._model = model.strip() if model else None
        self._model_is_configured = self._model is not None
        self._opener = opener
        self._timeout = timeout

    def stream_answer(
        self,
        question: str,
        contexts: Sequence[Evidence],
    ) -> Iterator[str]:
        retried_model_discovery = False
        while True:
            model = self._model or self._discover_model()
            self._model = model
            try:
                yield from self._stream_answer_once(
                    question,
                    contexts,
                    model,
                )
                return
            except _LmStudioModelNotFound as exc:
                if self._model_is_configured:
                    raise LmStudioUnavailable(
                        "LM Studio model is unavailable."
                    ) from exc
                self._model = None
                if retried_model_discovery:
                    raise LmStudioUnavailable(
                        "LM Studio model is unavailable."
                    ) from exc
                retried_model_discovery = True

    def _stream_answer_once(
        self,
        question: str,
        contexts: Sequence[Evidence],
        model: str,
    ) -> Iterator[str]:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": LOCAL_WECHAT_RAG_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": build_rag_user_prompt(question, contexts),
                },
            ],
            "stream": True,
        }
        request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = self._open(request)
        try:
            _require_success(response)
            try:
                for raw_line in response:
                    try:
                        line = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError as exc:
                        raise LmStudioUnavailable(
                            "LM Studio returned invalid UTF-8."
                        ) from exc
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise LmStudioUnavailable(
                            "LM Studio returned invalid stream data."
                        ) from exc
                    _raise_stream_error(event)
                    content = _delta_content(event)
                    if content:
                        yield content
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                raise LmStudioUnavailable(
                    "LM Studio is unavailable."
                ) from exc
        finally:
            response.close()

    def _discover_model(self) -> str:
        request = Request(
            f"{self._base_url}/models",
            headers={"Accept": "application/json"},
            method="GET",
        )
        response = self._open(request)
        try:
            _require_success(response)
            payload = b"".join(response)
            try:
                value = json.loads(payload)
                models = value["data"]
                model = models[0]["id"]
            except (
                json.JSONDecodeError,
                KeyError,
                IndexError,
                TypeError,
            ) as exc:
                raise LmStudioUnavailable(
                    "LM Studio did not report an available model."
                ) from exc
            if not isinstance(model, str) or not model.strip():
                raise LmStudioUnavailable(
                    "LM Studio did not report an available model."
                )
            return model.strip()
        finally:
            response.close()

    def _open(self, request: Request) -> Any:
        try:
            return self._opener(request, timeout=self._timeout)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise LmStudioUnavailable("LM Studio is unavailable.") from exc


def _validated_loopback_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or not parsed.hostname:
        raise LmStudioConfigurationError(
            "LM Studio URL must be an HTTP loopback endpoint."
        )
    hostname = parsed.hostname.casefold()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback or parsed.username or parsed.password:
        raise LmStudioConfigurationError(
            "LM Studio URL must be an HTTP loopback endpoint."
        )
    return normalized


def _require_success(response: Any) -> None:
    status = getattr(response, "status", 200)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise LmStudioUnavailable("LM Studio request failed.")


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


def _raise_stream_error(event: object) -> None:
    if not isinstance(event, dict) or "error" not in event:
        return
    error = event["error"]
    if _is_model_not_found(error):
        raise _LmStudioModelNotFound(
            "LM Studio model is unavailable."
        )
    raise LmStudioUnavailable("LM Studio request failed.")


def _is_model_not_found(error: object) -> bool:
    if isinstance(error, dict):
        details = (
            error.get("code"),
            error.get("type"),
            error.get("message"),
        )
    else:
        details = (error,)
    normalized = " ".join(
        str(detail).casefold()
        for detail in details
        if detail is not None
    )
    return any(
        marker in normalized
        for marker in (
            "model_not_found",
            "model not found",
            "model does not exist",
            "unknown model",
        )
    )


def _user_prompt(question: str, contexts: Sequence[Evidence]) -> str:
    return build_rag_user_prompt(question, contexts)
