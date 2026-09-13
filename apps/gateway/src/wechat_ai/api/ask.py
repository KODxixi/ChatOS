from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from typing import cast

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from ..services.answer_service import AnswerQuery


_END_OF_STREAM = object()


AnswerStreamer = Callable[
    [AnswerQuery],
    Iterable[Mapping[str, object]],
]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)
    conversationIds: list[str] = Field(default_factory=list, max_length=50)
    senderName: str | None = Field(default=None, max_length=500)
    sentAfter: int | None = None
    sentBefore: int | None = None

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized

    @field_validator("conversationIds")
    @classmethod
    def normalize_conversation_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("conversation id must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("conversation ids must be unique")
        return normalized

    @field_validator("senderName")
    @classmethod
    def normalize_sender_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_time_range(self) -> AskRequest:
        if (
            self.sentAfter is not None
            and self.sentBefore is not None
            and self.sentAfter > self.sentBefore
        ):
            raise ValueError("sentAfter must not exceed sentBefore")
        return self


def create_ask_router(*, answer_streamer: AnswerStreamer) -> APIRouter:
    router = APIRouter()

    @router.post("/ask/stream")
    def ask(request: AskRequest) -> StreamingResponse:
        query = AnswerQuery(
            question=request.question,
            conversation_ids=tuple(request.conversationIds),
            sender_name=request.senderName,
            sent_after=request.sentAfter,
            sent_before=request.sentBefore,
        )
        return StreamingResponse(
            _ndjson(answer_streamer(query)),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    return router


async def _ndjson(
    events: Iterable[Mapping[str, object]],
) -> AsyncIterator[bytes]:
    iterator = iter(events)
    try:
        while True:
            item = await run_in_threadpool(_next_or_end, iterator)
            if item is _END_OF_STREAM:
                break
            event = cast(Mapping[str, object], item)
            yield (
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


def _next_or_end(
    iterator: Iterator[Mapping[str, object]],
) -> Mapping[str, object] | object:
    try:
        return next(iterator)
    except StopIteration:
        return _END_OF_STREAM
