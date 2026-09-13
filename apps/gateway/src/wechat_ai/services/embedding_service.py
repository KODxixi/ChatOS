from __future__ import annotations

import ipaddress
import json
import math
from collections.abc import Callable, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


OpenRequest = Callable[..., Any]

_DEFAULT_MODEL = "text-embedding-qwen3-embedding-8b"
_UNAVAILABLE_MESSAGE = "Embedding service is unavailable."


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str = _DEFAULT_MODEL,
        opener: OpenRequest = urlopen,
        # llama-swap spawns retrieval models on demand (cold start ~10s after
        # its TTL unloads them); a 3s timeout would silently degrade retrieval.
        timeout: float = 30.0,
    ) -> None:
        self._base_url = _validated_loopback_url(base_url)
        self._model = model
        self._opener = opener
        self._timeout = timeout

    def similarities(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        if not documents:
            return []

        inputs = [query, *documents]
        request = Request(
            f"{self._base_url}/embeddings",
            data=json.dumps(
                {"model": self._model, "input": inputs},
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            response = self._opener(request, timeout=self._timeout)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE) from None

        try:
            status = getattr(response, "status", 200)
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or not 200 <= status < 300
            ):
                raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
            try:
                payload = json.loads(response.read())
            except (TypeError, UnicodeError, ValueError):
                raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE) from None
            vectors = _ordered_normalized_vectors(payload, len(inputs))
            query_vector = vectors[0]
            return [
                math.fsum(
                    query_value * document_value
                    for query_value, document_value in zip(
                        query_vector,
                        document_vector,
                        strict=True,
                    )
                )
                for document_vector in vectors[1:]
            ]
        except EmbeddingUnavailable:
            raise
        except (HTTPError, URLError, OSError, TimeoutError):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE) from None
        finally:
            response.close()


def _validated_loopback_url(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise ValueError("Embedding URL must be an HTTP loopback endpoint.")
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Embedding URL must be an HTTP loopback endpoint.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(
            "Embedding URL must be an HTTP loopback endpoint."
        ) from exc

    hostname = parsed.hostname.casefold()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("Embedding URL must be an HTTP loopback endpoint.")
    return normalized


def _ordered_normalized_vectors(
    payload: object,
    expected_count: int,
) -> list[tuple[float, ...]]:
    if not isinstance(payload, dict):
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)

    ordered: list[tuple[float, ...] | None] = [None] * expected_count
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
        index = item.get("index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < expected_count
            or ordered[index] is not None
        ):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
        ordered[index] = _normalized_vector(item.get("embedding"))

    if any(vector is None for vector in ordered):
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
    vectors = [vector for vector in ordered if vector is not None]
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors[1:]):
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
    return vectors


def _normalized_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)

    vector: list[float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
        try:
            number = float(component)
        except OverflowError:
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE) from None
        if not math.isfinite(number):
            raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
        vector.append(number)

    scale = max(abs(component) for component in vector)
    if scale == 0.0:
        raise EmbeddingUnavailable(_UNAVAILABLE_MESSAGE)
    scaled = [component / scale for component in vector]
    norm = math.sqrt(math.fsum(component * component for component in scaled))
    return tuple(component / norm for component in scaled)
