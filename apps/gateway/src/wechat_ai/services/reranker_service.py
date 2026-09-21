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

_DEFAULT_MODEL = "vl-reranker-2b"  # 2026-09-21: text-reranker-8b 已从 llama-swap 摘除（与常驻 30B 显存冲突），
                                   # 改由 vl-reranker-2b 兼任文本精排；沿用旧 id 会 404


class RerankerUnavailable(RuntimeError):
    pass


class RerankerClient:
    """Rerank against llama-swap's ``/v1/rerank`` (llama.cpp rank pooling).

    The endpoint scores every document against the query in a single call and
    returns ``results`` in descending relevance; the caller maps each
    ``index`` back to its original document position.
    """

    def __init__(
        self,
        base_url: str,
        *,
        model: str = _DEFAULT_MODEL,
        opener: OpenRequest = urlopen,
        # llama-swap spawns retrieval models on demand (cold start ~10s after
        # its TTL unloads them); keep the timeout well above that so the first
        # request after idle does not silently fall back to time ranking.
        timeout: float = 30.0,
    ) -> None:
        self._base_url = _validated_loopback_url(base_url)
        self._model = model
        self._opener = opener
        self._timeout = timeout

    def score_many(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[float | None]:
        """Return a relevance score per document, aligned to ``documents``.

        A document missing from the endpoint response maps to ``None`` (the
        caller may then fall back to embedding-only ranking). A transport or
        protocol failure raises :class:`RerankerUnavailable`.
        """
        if not documents:
            return []
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Reranker query must be non-empty.")
        payload = {
            "model": self._model,
            "query": query,
            "documents": list(documents),
        }
        request = Request(
            f"{self._base_url}/rerank",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self._timeout)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise RerankerUnavailable("Reranker is unavailable.") from exc
        try:
            status = getattr(response, "status", 200)
            if not isinstance(status, int) or not 200 <= status < 300:
                raise RerankerUnavailable("Reranker is unavailable.")
            try:
                payload = json.loads(response.read())
            except (TypeError, UnicodeError, ValueError) as exc:
                raise RerankerUnavailable("Reranker is unavailable.") from exc
            results = payload.get("results")
            if not isinstance(results, list):
                raise RerankerUnavailable("Reranker is unavailable.")
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise RerankerUnavailable("Reranker is unavailable.") from exc
        finally:
            response.close()

        scores: list[float | None] = [None] * len(documents)
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(documents)
            ):
                continue
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                continue
            scores[index] = float(score)
        return scores


def _validated_loopback_url(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise ValueError("Reranker URL must be an HTTP loopback endpoint.")
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
        raise ValueError("Reranker URL must be an HTTP loopback endpoint.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(
            "Reranker URL must be an HTTP loopback endpoint."
        ) from exc
    hostname = parsed.hostname.casefold()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("Reranker URL must be an HTTP loopback endpoint.")
    return normalized
