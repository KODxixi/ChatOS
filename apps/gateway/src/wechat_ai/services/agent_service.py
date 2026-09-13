from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
import time
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import closing
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from urllib.parse import urlparse

import jieba

from ..openai_compatible import ModelUsage, OpenAICompatibleClient, trim_remote_history
from .agent_thread_service import (
    AgentCitation,
    AgentRun,
    AgentThreadService,
    KnowledgeScope,
    KnowledgeScopeValidationError,
    normalize_knowledge_scope,
)
from .conversation_service import ConnectionFactory
from .call_ledger_service import CallEvidence, CallLedgerService
from .gchat_service import GchatService
from .model_profile_service import (
    ModelProfile,
    ModelProfileService,
    is_deepseek_v4_flash_profile,
)


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_QUESTION_CHARACTERS = frozenset("请问是什么怎么为何哪多少否吗呢的了")
_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_GENERIC_CJK_QUESTION_PHRASES = (
    "有什么变化",
    "有什么",
    "发生了什么",
    "怎么样",
    "如何",
    "请告诉我",
)
_ENGLISH_QUESTION_TERMS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "is",
        "me",
        "of",
        "or",
        "please",
        "tell",
        "that",
        "the",
        "this",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
_DEEP_REASONING_TERMS = (
    "深入",
    "分析",
    "比较",
    "对比",
    "推理",
    "原因",
    "为什么",
    "风险",
    "取舍",
    "权衡",
    "方案",
    "analyze",
    "compare",
    "reason",
    "tradeoff",
    "trade-off",
    "risk",
    "why",
)


def classify_reasoning_mode(question: str) -> Literal["fast", "deep"]:
    normalized = question.strip().casefold()
    if any(term in normalized for term in _DEEP_REASONING_TERMS):
        return "deep"
    if len(normalized) >= 120 or normalized.count("？") + normalized.count("?") >= 2:
        return "deep"
    return "fast"


@dataclass(frozen=True, slots=True)
class ResolvedKnowledgeScope:
    mode: Literal["global", "selection", "project"]
    message_part_ids: tuple[int, ...] | None
    scope_snapshot: KnowledgeScope | None = None


@dataclass(frozen=True, slots=True)
class AgentEvidence:
    message_id: int
    part_id: int
    conversation_id: str
    sender_name: str
    sent_at: int
    excerpt: str
    score: float | None = None

    def to_citation(self) -> AgentCitation:
        return AgentCitation(
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            part_id=self.part_id,
            sender_name=self.sender_name,
            sent_at=self.sent_at,
            excerpt=self.excerpt,
        )


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    question: str
    scope: ResolvedKnowledgeScope
    limit: int


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    evidence: AgentEvidence
    rank: int
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    backend: str
    index_version: int
    hits: tuple[RetrievalHit, ...]
    candidate_count: int


@dataclass(frozen=True, slots=True)
class QueryTimeRange:
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class ParsedRetrievalQuery:
    text: str
    time_range: QueryTimeRange | None


REMOTE_SEND_POLICY_VERSION = "remote-send-v1"
_NO_EVIDENCE_ANSWER = "根据本地微信记录，未查找到相关讨论。"
Clock = Callable[[], int]


class AgentServiceError(RuntimeError):
    code = "AGENT_ERROR"


class AgentServiceValidationError(AgentServiceError):
    code = "AGENT_REQUEST_INVALID"


@dataclass(frozen=True, slots=True)
class RemoteSendPreview:
    destination_host: str
    question: str
    evidence: tuple[AgentCitation, ...]
    fingerprint: str
    history: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "destinationHost": self.destination_host,
            "question": self.question,
            "evidence": [item.to_dict() for item in self.evidence],
            "history": list(self.history),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GroundingResult:
    grounding_status: Literal["grounded", "invalid", "insufficient"]
    evidence: tuple[AgentEvidence, ...]


class RemoteSendConfirmationRequired(AgentServiceError):
    code = "REMOTE_SEND_CONFIRMATION_REQUIRED"

    def __init__(self, preview: RemoteSendPreview) -> None:
        super().__init__("remote model send confirmation is required")
        self.preview = preview


class AgentScopeResolver(Protocol):
    def resolve(self, scope: KnowledgeScope) -> ResolvedKnowledgeScope: ...


class RetrieverBackend(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class AgentProvider(Protocol):
    def stream_answer(
        self,
        question: str,
        contexts: Sequence[AgentEvidence],
        *,
        history: Sequence[Mapping[str, str]] = (),
        thinking: bool | None = None,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        usage_sink: Callable[[ModelUsage], None] | None = None,
    ) -> Iterator[str]: ...


ProviderFactory = Callable[[ModelProfile, str | None], AgentProvider]


class AgentService:
    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        thread_service: AgentThreadService,
        model_profile_service: ModelProfileService,
        scope_resolver: AgentScopeResolver,
        retriever: RetrieverBackend,
        provider_factory: ProviderFactory | None = None,
        gchat_service: GchatService | None = None,
        call_ledger: CallLedgerService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._threads = thread_service
        self._models = model_profile_service
        self._scope_resolver = scope_resolver
        self._retriever = retriever
        self._provider_factory = provider_factory or _default_provider_factory
        self._gchat = gchat_service
        self._call_ledger = call_ledger
        self._clock = clock or (lambda: int(time.time() * 1000))

    def stream(
        self,
        *,
        thread_id: str | None,
        question: str,
        model_profile_id: str | None,
        scope: object,
        remote_send_confirmation: str | None,
        project_id: str | None = None,
        reasoning_mode: Literal["auto", "fast", "deep"] = "auto",
        include_thread_history: bool = True,
        generate_insights: bool = False,
        execution_run_id: str | None = None,
    ) -> Iterator[dict[str, object]]:
        normalized_question = _normalize_question(question)
        if reasoning_mode not in {"auto", "fast", "deep"}:
            raise AgentServiceValidationError("reasoning mode is invalid")
        if not isinstance(include_thread_history, bool):
            raise AgentServiceValidationError(
                "include thread history option is invalid"
            )
        if not isinstance(generate_insights, bool):
            raise AgentServiceValidationError(
                "generate insights option is invalid"
            )
        model_options = {
            "reasoningMode": reasoning_mode,
            "includeThreadHistory": include_thread_history,
            "generateInsights": generate_insights,
        }
        resolved_reasoning_mode = (
            classify_reasoning_mode(normalized_question)
            if reasoning_mode == "auto"
            else reasoning_mode
        )


        normalized_scope = normalize_knowledge_scope(scope)
        effective_project_id = project_id
        if normalized_scope.mode == "project":
            if (
                effective_project_id is not None
                and effective_project_id.strip() != normalized_scope.project_id
            ):
                raise AgentServiceValidationError(
                    "project id does not match knowledge scope"
                )
            effective_project_id = normalized_scope.project_id
        profile = self._models.agent_profile_metadata(model_profile_id)
        resolved_scope = self._scope_resolver.resolve(normalized_scope)
        turn_scope = resolved_scope.scope_snapshot or normalized_scope
        remote_history = (
            self._remote_thread_history(thread_id, turn_scope)
            if profile.endpoint.is_remote and include_thread_history
            else []
        )
        retrieval_request = RetrievalRequest(
            question=normalized_question,
            scope=resolved_scope,
            limit=40,
        )
        backend_name = str(getattr(self._retriever, "backend_name", "retrieval"))
        backend_version = int(getattr(self._retriever, "index_version", 1))
        retrieval_span_id = (
            self._call_ledger.start_span(
                run_id=execution_run_id,
                kind="retrieval",
                operation=backend_name,
                metadata={
                    "backend": backend_name,
                    "indexVersion": backend_version,
                    "scopeMode": resolved_scope.mode,
                },
            )
            if self._call_ledger is not None and execution_run_id is not None
            else None
        )
        try:
            if isinstance(self._retriever, SqlAgentEvidenceRetriever):
                retrieval_result = self._retriever.retrieve_result(retrieval_request)
            else:
                retrieval_result = self._retriever.retrieve(retrieval_request)
        except Exception as exc:
            if self._call_ledger is not None:
                self._call_ledger.finish_span(
                    retrieval_span_id,
                    status="failed",
                    error_code=getattr(exc, "code", type(exc).__name__),
                )
            raise
        selected_hits = retrieval_result.hits[:12]
        evidence = [
            replace(hit.evidence, score=hit.score)
            for hit in selected_hits
        ]
        if self._call_ledger is not None:
            self._call_ledger.finish_span(
                retrieval_span_id,
                candidate_count=retrieval_result.candidate_count,
                citation_count=0,
                evidence=tuple(
                    CallEvidence(
                        message_id=hit.evidence.message_id,
                        part_id=hit.evidence.part_id,
                        role="candidate",
                        rank=hit.rank,
                        score=hit.score,
                    )
                    for hit in selected_hits
                ),
            )
        retrieval_summary = {
            "retrievedCount": len(evidence),
            "citationCount": 0,
        }
        if turn_scope.mode == "project":
            self._validate_project_evidence(turn_scope, evidence)

        if not evidence:
            run = self._threads.start_run(
                thread_id=thread_id,
                question=normalized_question,
                model_profile_id=profile.id,
                scope=turn_scope,
                project_id=effective_project_id,
                model_options=model_options,
                retrieval_summary=retrieval_summary,
            )
            return self._stream_without_evidence(
                run,
                retrieval_summary=retrieval_summary,
            )

        profile = self._models.preflight_for_agent(profile.id)
        pending_send_consent: tuple[str, str] | None = None
        if profile.endpoint.is_remote:
            preview = self._remote_preview(
                profile,
                normalized_question,
                turn_scope,
                evidence,
                remote_history,
            )
            endpoint_fingerprint = profile.endpoint.privacy_fingerprint(
                REMOTE_SEND_POLICY_VERSION
            )
            if not self._has_send_consent(
                profile.id,
                endpoint_fingerprint,
            ):
                if remote_send_confirmation != preview.fingerprint:
                    raise RemoteSendConfirmationRequired(preview)
                pending_send_consent = (
                    profile.id,
                    endpoint_fingerprint,
                )

        resolved_profile = self._models.resolve_for_agent(profile.id)
        provider = self._provider_factory(
            resolved_profile.profile,
            resolved_profile.credential,
        )
        run = self._threads.start_run(
            thread_id=thread_id,
            question=normalized_question,
            model_profile_id=profile.id,
            scope=turn_scope,
            project_id=effective_project_id,
            model_options=model_options,
            retrieval_summary=retrieval_summary,
        )
        history = (
            remote_history if profile.endpoint.is_remote
            else (
                self._threads.history_for_run(run, limit=20)
                if include_thread_history
                else []
            )
        )
        return self._stream_with_provider(
            run,
            normalized_question,
            evidence,
            history,
            resolved_scope,
            provider,
            profile,
            pending_send_consent,
            (
                resolved_reasoning_mode
                if is_deepseek_v4_flash_profile(profile)
                else None
            ),
            generate_insights,
            execution_run_id,
            retrieval_span_id,
        )

    def _validate_project_evidence(
        self,
        scope: KnowledgeScope,
        evidence: Sequence[AgentEvidence],
    ) -> None:
        allowed_conversations = set(scope.conversation_ids)
        if any(
            item.conversation_id not in allowed_conversations
            for item in evidence
        ):
            raise AgentServiceValidationError(
                "retrieved evidence is outside the project scope snapshot"
            )
        with closing(self._connection_factory()) as connection:
            for item in evidence:
                source = connection.execute(
                    """
                    SELECT messages.conversation_id
                    FROM messages
                    JOIN message_parts
                      ON message_parts.message_id = messages.id
                    WHERE messages.id = ? AND message_parts.id = ?
                    """,
                    (item.message_id, item.part_id),
                ).fetchone()
                if (
                    source is None
                    or str(source[0]) != item.conversation_id
                    or str(source[0]) not in allowed_conversations
                ):
                    raise AgentServiceValidationError(
                        "retrieved evidence is outside the project scope snapshot"
                    )

    def revalidate_citations(
        self,
        evidence: Sequence[AgentEvidence],
        scope: ResolvedKnowledgeScope,
    ) -> GroundingResult:
        if not evidence:
            return GroundingResult("insufficient", ())
        allowed_part_ids = (
            set(scope.message_part_ids)
            if scope.message_part_ids is not None
            else None
        )
        allowed_conversations: set[str] | None = None
        if scope.scope_snapshot is not None and (
            scope.scope_snapshot.mode == "project"
            or scope.scope_snapshot.conversation_ids
        ):
            allowed_conversations = set(
                scope.scope_snapshot.conversation_ids
            )
        valid: list[AgentEvidence] = []
        with closing(self._connection_factory()) as connection:
            for item in evidence:
                if (
                    allowed_part_ids is not None
                    and item.part_id not in allowed_part_ids
                ):
                    continue
                if (
                    allowed_conversations is not None
                    and item.conversation_id not in allowed_conversations
                ):
                    continue
                source = connection.execute(
                    """
                    SELECT messages.conversation_id, message_parts.status
                    FROM messages
                    JOIN message_parts
                      ON message_parts.message_id = messages.id
                    WHERE messages.id = ? AND message_parts.id = ?
                    """,
                    (item.message_id, item.part_id),
                ).fetchone()
                if (
                    source is None
                    or str(source[0]) != item.conversation_id
                    or str(source[1]) != "resolved"
                ):
                    continue
                valid.append(item)
        return GroundingResult(
            "grounded" if valid else "invalid",
            tuple(valid),
        )

    def _remote_thread_history(
        self,
        thread_id: str | None,
        scope: KnowledgeScope,
    ) -> list[dict[str, str]]:
        if thread_id is None:
            return []
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT role, content, scope_json, status
                FROM agent_turns
                WHERE thread_id = ?
                ORDER BY rowid ASC
                """,
                (thread_id,),
            ).fetchall()
        target_scope = scope.to_json()
        complete_rounds: list[tuple[dict[str, str], dict[str, str]]] = []
        index = 0
        while index + 1 < len(rows):
            user_row = rows[index]
            assistant_row = rows[index + 1]
            index += 2
            if (
                str(user_row["role"]) != "user"
                or str(assistant_row["role"]) != "assistant"
                or str(user_row["status"]) != "complete"
                or str(assistant_row["status"]) != "complete"
            ):
                continue
            try:
                user_scope = normalize_knowledge_scope(
                    json.loads(str(user_row["scope_json"])),
                    allow_project_snapshot=True,
                ).to_json()
                assistant_scope = normalize_knowledge_scope(
                    json.loads(str(assistant_row["scope_json"])),
                    allow_project_snapshot=True,
                ).to_json()
            except (json.JSONDecodeError, KnowledgeScopeValidationError):
                continue
            if user_scope != target_scope or assistant_scope != target_scope:
                continue
            complete_rounds.append(
                (
                    {"role": "user", "content": str(user_row["content"])},
                    {
                        "role": "assistant",
                        "content": str(assistant_row["content"]),
                    },
                )
            )
        history = [
            item
            for round_items in complete_rounds[-8:]
            for item in round_items
        ]
        return trim_remote_history(history)

    def _remote_preview(
        self,
        profile: ModelProfile,
        question: str,
        scope: KnowledgeScope,
        evidence: Sequence[AgentEvidence],
        history: Sequence[Mapping[str, str]],
    ) -> RemoteSendPreview:
        citations = tuple(item.to_citation() for item in evidence)
        endpoint_fingerprint = profile.endpoint.privacy_fingerprint(
            REMOTE_SEND_POLICY_VERSION
        )
        fingerprint_payload = {
            "version": REMOTE_SEND_POLICY_VERSION,
            "profileId": profile.id,
            "endpointFingerprint": endpoint_fingerprint,
            "question": question,
            "scope": scope.to_dict(),
            "evidence": [item.to_dict() for item in citations],
            "history": list(history),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        destination_host = urlparse(profile.base_url).hostname
        if destination_host is None:
            raise AgentServiceValidationError("model endpoint is invalid")
        return RemoteSendPreview(
            destination_host=destination_host,
            question=question,
            evidence=citations,
            fingerprint=fingerprint,
            history=tuple(dict(item) for item in history),
        )

    def _has_send_consent(
        self,
        profile_id: str,
        endpoint_fingerprint: str,
    ) -> bool:
        with closing(self._connection_factory()) as connection:
            return connection.execute(
                """
                SELECT 1 FROM remote_model_send_consents
                WHERE profile_id = ? AND endpoint_fingerprint = ?
                """,
                (profile_id, endpoint_fingerprint),
            ).fetchone() is not None

    def _record_send_consent(
        self,
        profile_id: str,
        endpoint_fingerprint: str,
    ) -> None:
        with closing(self._connection_factory()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO remote_model_send_consents(
                    profile_id, endpoint_fingerprint, confirmed_at
                ) VALUES (?, ?, ?)
                """,
                (profile_id, endpoint_fingerprint, self._clock()),
            )
            connection.commit()

    def _stream_without_evidence(
        self,
        run: AgentRun,
        *,
        retrieval_summary: Mapping[str, object],
    ) -> Iterator[dict[str, object]]:
        terminal = False
        try:
            yield _status_event(run, "retrieving")
            yield {"type": "delta", "text": _NO_EVIDENCE_ANSWER}
            self._threads.complete_run(
                run,
                answer=_NO_EVIDENCE_ANSWER,
                citations=[],
                grounding_status="insufficient",
                retrieval_summary=retrieval_summary,
            )
            terminal = True
            yield {
                "type": "grounding",
                "status": "insufficient",
                "retrievalSummary": dict(retrieval_summary),
            }
            yield _done_event(run)
        except GeneratorExit:
            if not terminal:
                self._threads.cancel_run(run)
            raise
        finally:
            if self._call_ledger is not None:
                self._call_ledger.prune_if_due()

    def _stream_with_provider(
        self,
        run: AgentRun,
        question: str,
        evidence: Sequence[AgentEvidence],
        history: Sequence[Mapping[str, str]],
        resolved_scope: ResolvedKnowledgeScope,
        provider: AgentProvider,
        profile: ModelProfile,
        pending_send_consent: tuple[str, str] | None,
        reasoning_mode: Literal["fast", "deep"] | None,
        generate_insights: bool,
        execution_run_id: str | None,
        retrieval_span_id: str | None,
    ) -> Iterator[dict[str, object]]:
        terminal = False
        upstream: Iterator[str] | None = None
        usage: ModelUsage | None = None
        model_span_id: str | None = None
        model_span_finished = False
        try:
            yield _status_event(run, "retrieving")
            yield _status_event(run, "generating")
            answer_parts: list[str] = []
            if self._call_ledger is not None and execution_run_id is not None:
                model_span_id = self._call_ledger.start_span(
                    run_id=execution_run_id,
                    kind="model",
                    operation="chat.completions",
                    model_profile_id=profile.id,
                    metadata={
                        "provider": profile.provider,
                        "reasoningMode": reasoning_mode or "fast",
                        "isRemote": profile.endpoint.is_remote,
                    },
                )
            provider_kwargs: dict[str, object] = {"history": history}

            def receive_usage(value: ModelUsage) -> None:
                nonlocal usage
                if isinstance(value, ModelUsage):
                    usage = value

            if _accepts_keyword(provider.stream_answer, "usage_sink"):
                provider_kwargs["usage_sink"] = receive_usage
            if reasoning_mode is None:
                upstream = iter(
                    provider.stream_answer(
                        question,
                        evidence,
                        **provider_kwargs,
                    )
                )
            else:
                provider_kwargs.update(
                    {
                        "thinking": reasoning_mode == "deep",
                        "reasoning_effort": (
                            "high" if reasoning_mode == "deep" else None
                        ),
                    }
                )
                upstream = iter(
                    provider.stream_answer(
                        question,
                        evidence,
                        **provider_kwargs,
                    )
                )
            for delta in upstream:
                if pending_send_consent is not None:
                    self._record_send_consent(*pending_send_consent)
                    pending_send_consent = None
                if isinstance(delta, str) and delta:
                    answer_parts.append(delta)
                    yield {"type": "delta", "text": delta}
            if pending_send_consent is not None:
                self._record_send_consent(*pending_send_consent)
            if self._call_ledger is not None:
                self._call_ledger.finish_span(
                    model_span_id,
                    input_tokens=usage.input_tokens if usage is not None else None,
                    output_tokens=usage.output_tokens if usage is not None else None,
                    total_tokens=usage.total_tokens if usage is not None else None,
                )
                model_span_finished = True
            grounding = self.revalidate_citations(evidence, resolved_scope)
            citations = [
                item.to_citation() for item in grounding.evidence
            ]
            if self._call_ledger is not None:
                self._call_ledger.add_citations(
                    retrieval_span_id,
                    tuple(
                        CallEvidence(
                            message_id=item.message_id,
                            part_id=item.part_id,
                            role="citation",
                            rank=index,
                        )
                        for index, item in enumerate(grounding.evidence, start=1)
                    ),
                )
            retrieval_summary = {
                "retrievedCount": len(evidence),
                "citationCount": len(citations),
            }
            answer = "".join(answer_parts)
            memory_candidate = (
                self._capture_memory(
                    question=question,
                    answer=answer,
                    scope=run.scope.to_dict(),
                    citations=[item.to_dict() for item in citations],
                )
                if generate_insights
                else None
            )
            self._threads.complete_run(
                run,
                answer=answer,
                citations=citations,
                grounding_status=grounding.grounding_status,
                retrieval_summary=retrieval_summary,
            )
            candidates: list[dict[str, object]] = []
            if (
                isinstance(memory_candidate, Mapping)
                and memory_candidate.get("status")
                in {"suggested", "proposed"}
            ):
                candidates.append(dict(memory_candidate))
            candidates.extend(
                self._generate_insights(
                    run=run,
                    question=question,
                    answer=answer,
                    citations=[item.to_dict() for item in citations],
                )
                if generate_insights
                else []
            )
            terminal = True
            yield {
                "type": "grounding",
                "status": grounding.grounding_status,
                "retrievalSummary": retrieval_summary,
            }
            for citation in citations:
                yield {
                    "type": "citation",
                    "item": citation.to_dict(),
                }
            for candidate in candidates:
                yield {"type": "candidate", "item": candidate}
            yield _done_event(run)
        except GeneratorExit:
            if self._call_ledger is not None and not model_span_finished:
                self._call_ledger.finish_span(model_span_id, status="cancelled")
                model_span_finished = True
            if not terminal:
                self._threads.cancel_run(run)
            raise
        except Exception as exc:
            if self._call_ledger is not None and not model_span_finished:
                self._call_ledger.finish_span(
                    model_span_id,
                    status="failed",
                    error_code=getattr(exc, "code", type(exc).__name__),
                )
                model_span_finished = True
            if not terminal:
                self._threads.fail_run(run, "MODEL_UNAVAILABLE")
                terminal = True
            yield {
                "type": "error",
                "error": {
                    "code": "MODEL_UNAVAILABLE",
                    "message": "模型当前不可用。",
                    "retryable": True,
                    "details": {},
                },
            }
        finally:
            close = getattr(upstream, "close", None)
            if callable(close):
                close()
            if self._call_ledger is not None:
                self._call_ledger.prune_if_due()

    def _capture_memory(
        self,
        *,
        question: str,
        answer: str,
        scope: Mapping[str, object],
        citations: Sequence[Mapping[str, object]],
    ) -> dict[str, object] | None:
        if self._gchat is None:
            return None
        try:
            captured = self._gchat.capture_turn(
                question=question,
                answer=answer,
                scope=scope,
                citations=citations,
            )
            return dict(captured) if isinstance(captured, Mapping) else None
        except Exception:
            # Memory suggestions are a rebuildable projection and must never
            # turn a successful chat answer into a failed Agent run.
            return None

    def _generate_insights(
        self,
        *,
        run: AgentRun,
        question: str,
        answer: str,
        citations: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        if self._gchat is None or not citations:
            return []
        try:
            generated = self._gchat.generate_insights(
                question=question,
                answer=answer,
                scope=run.scope.to_dict(),
                citations=citations,
                source_turn_id=run.assistant_turn_id,
                model_profile_id=run.model_profile_id,
            )
        except Exception:
            return []
        if isinstance(generated, (str, bytes)) or not isinstance(
            generated,
            Sequence,
        ):
            return []
        return [
            dict(item)
            for item in generated
            if isinstance(item, Mapping)
            and item.get("status") in {"suggested", "proposed"}
        ]


def _status_event(run: AgentRun, phase: str) -> dict[str, object]:
    return {
        "type": "status",
        "phase": phase,
        "threadId": run.thread_id,
        "assistantTurnId": run.assistant_turn_id,
    }


def _done_event(run: AgentRun) -> dict[str, object]:
    return {
        "type": "done",
        "threadId": run.thread_id,
        "assistantTurnId": run.assistant_turn_id,
    }


def _normalize_question(value: object) -> str:
    if not isinstance(value, str):
        raise AgentServiceValidationError("question is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 20_000:
        raise AgentServiceValidationError("question is invalid")
    return normalized


def _default_provider_factory(
    profile: ModelProfile,
    credential: str | None,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        profile.base_url,
        model=profile.model,
        credential=credential,
    )


def _accepts_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


@dataclass(frozen=True, slots=True)
class _NodeConstraint:
    conversation_ids: tuple[str, ...]
    message_ids: tuple[int, ...]
    message_part_ids: tuple[int, ...]

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.conversation_ids,
                self.message_ids,
                self.message_part_ids,
            )
        )


class SqlKnowledgeScopeResolver:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def resolve(self, scope: KnowledgeScope) -> ResolvedKnowledgeScope:
        if scope.mode == "global":
            return ResolvedKnowledgeScope(
                mode="global",
                message_part_ids=None,
            )

        if scope.mode == "project":
            with closing(self._connection_factory()) as connection:
                project = connection.execute(
                    """
                    SELECT 1 FROM projects
                    WHERE id = ? AND status <> 'archived'
                    """,
                    (scope.project_id,),
                ).fetchone()
                if project is None:
                    raise KnowledgeScopeValidationError(
                        "project knowledge scope is invalid"
                    )
                conversation_ids = tuple(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT conversation_id FROM project_conversations
                        WHERE project_id = ?
                        ORDER BY conversation_id ASC
                        """,
                        (scope.project_id,),
                    ).fetchall()
                )
                snapshot = KnowledgeScope(
                    mode="project",
                    project_id=scope.project_id,
                    conversation_ids=conversation_ids,
                    project_snapshot=True,
                )
                if not conversation_ids:
                    return ResolvedKnowledgeScope(
                        mode="project",
                        message_part_ids=(),
                        scope_snapshot=snapshot,
                    )
                placeholders = ", ".join("?" for _ in conversation_ids)
                rows = connection.execute(
                    f"""
                    SELECT message_parts.id
                    FROM message_parts
                    JOIN messages ON messages.id = message_parts.message_id
                    WHERE messages.conversation_id IN ({placeholders})
                    ORDER BY message_parts.id ASC
                    """,
                    conversation_ids,
                ).fetchall()
            return ResolvedKnowledgeScope(
                mode="project",
                message_part_ids=tuple(int(row[0]) for row in rows),
                scope_snapshot=snapshot,
            )

        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            where: list[str] = []
            parameters: list[object] = []
            _append_in_filter(
                where,
                parameters,
                "source_apps.id",
                scope.source_app_ids,
            )
            _append_in_filter(
                where,
                parameters,
                "source_accounts.id",
                scope.source_account_ids,
            )
            _append_in_filter(
                where,
                parameters,
                "conversations.id",
                scope.conversation_ids,
            )
            _append_in_filter(
                where,
                parameters,
                "messages.id",
                scope.message_ids,
            )
            _append_in_filter(
                where,
                parameters,
                "conversations.kind",
                scope.conversation_kinds,
            )
            _append_in_filter(
                where,
                parameters,
                "message_parts.kind",
                scope.content_kinds,
            )

            if scope.node_ids:
                node_constraint = self._resolve_nodes(
                    connection,
                    scope.node_ids,
                )
                if node_constraint is None or node_constraint.is_empty:
                    return ResolvedKnowledgeScope(
                        mode="selection",
                        message_part_ids=(),
                    )
                node_clauses: list[str] = []
                _append_in_filter(
                    node_clauses,
                    parameters,
                    "conversations.id",
                    node_constraint.conversation_ids,
                )
                _append_in_filter(
                    node_clauses,
                    parameters,
                    "messages.id",
                    node_constraint.message_ids,
                )
                _append_in_filter(
                    node_clauses,
                    parameters,
                    "message_parts.id",
                    node_constraint.message_part_ids,
                )
                where.append("(" + " OR ".join(node_clauses) + ")")

            rows = connection.execute(
                f"""
                SELECT DISTINCT message_parts.id
                FROM message_parts
                JOIN messages ON messages.id = message_parts.message_id
                JOIN conversations
                  ON conversations.id = messages.conversation_id
                JOIN conversation_origins
                  ON conversation_origins.conversation_id = conversations.id
                JOIN source_accounts
                  ON source_accounts.id = conversation_origins.source_account_id
                JOIN source_apps
                  ON source_apps.id = source_accounts.source_app_id
                WHERE {" AND ".join(where) if where else "0"}
                ORDER BY message_parts.id ASC
                """,
                parameters,
            ).fetchall()
        return ResolvedKnowledgeScope(
            mode="selection",
            message_part_ids=tuple(int(row["id"]) for row in rows),
        )

    def _resolve_nodes(
        self,
        connection: sqlite3.Connection,
        node_ids: Sequence[str],
    ) -> _NodeConstraint | None:
        conversations: set[str] = set()
        messages: set[int] = set()
        parts: set[int] = set()

        for node_id in node_ids:
            node = connection.execute(
                "SELECT kind, trace_kind, trace_id FROM graph_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            if node is None:
                return None
            node_conversations: set[str] = set()
            node_messages: set[int] = set()
            node_parts: set[int] = set()
            self._add_trace(
                connection,
                str(node["trace_kind"]),
                str(node["trace_id"]),
                node_conversations,
                node_messages,
                node_parts,
            )
            evidence = connection.execute(
                """
                SELECT evidence.evidence_kind, evidence.evidence_id
                FROM graph_edges AS edges
                JOIN graph_edge_evidence AS evidence
                  ON evidence.edge_id = edges.id
                WHERE edges.source_node_id = ? OR edges.target_node_id = ?
                """,
                (node_id, node_id),
            ).fetchall()
            for row in evidence:
                self._add_trace(
                    connection,
                    str(row["evidence_kind"]),
                    str(row["evidence_id"]),
                    node_conversations,
                    node_messages,
                    node_parts,
                )
            if not any((node_conversations, node_messages, node_parts)):
                return None
            conversations.update(node_conversations)
            messages.update(node_messages)
            parts.update(node_parts)
        return _NodeConstraint(
            conversation_ids=tuple(sorted(conversations)),
            message_ids=tuple(sorted(messages)),
            message_part_ids=tuple(sorted(parts)),
        )

    @staticmethod
    def _add_trace(
        connection: sqlite3.Connection,
        kind: str,
        trace_id: str,
        conversations: set[str],
        messages: set[int],
        parts: set[int],
    ) -> None:
        if kind == "conversation":
            row = connection.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (trace_id,),
            ).fetchone()
            if row is not None:
                conversations.add(str(row["id"]))
            return
        if kind == "message":
            identifier = _positive_int(trace_id)
            if identifier is not None and connection.execute(
                "SELECT 1 FROM messages WHERE id = ?",
                (identifier,),
            ).fetchone() is not None:
                messages.add(identifier)
            return
        if kind == "message_part":
            identifier = _positive_int(trace_id)
            if identifier is not None and connection.execute(
                "SELECT 1 FROM message_parts WHERE id = ?",
                (identifier,),
            ).fetchone() is not None:
                parts.add(identifier)
            return
        if kind == "asset":
            identifier = _positive_int(trace_id)
            if identifier is not None:
                row = connection.execute(
                    "SELECT part_id FROM assets WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if row is not None:
                    parts.add(int(row["part_id"]))
            return
        if kind == "source_account":
            rows = connection.execute(
                """
                SELECT conversation_id FROM conversation_origins
                WHERE source_account_id = ?
                """,
                (trace_id,),
            ).fetchall()
            conversations.update(str(row["conversation_id"]) for row in rows)
            return
        if kind == "source_app":
            rows = connection.execute(
                """
                SELECT origins.conversation_id
                FROM conversation_origins AS origins
                JOIN source_accounts AS accounts
                  ON accounts.id = origins.source_account_id
                WHERE accounts.source_app_id = ?
                """,
                (trace_id,),
            ).fetchall()
            conversations.update(str(row["conversation_id"]) for row in rows)


class SqlAgentEvidenceRetriever:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: int(time.time() * 1000))

    def retrieve(
        self,
        question: str,
        scope: ResolvedKnowledgeScope,
        limit: int,
    ) -> list[AgentEvidence]:
        return [
            hit.evidence
            for hit in self.retrieve_result(
                RetrievalRequest(question=question, scope=scope, limit=limit)
            ).hits
        ]

    def retrieve_result(self, request: RetrievalRequest) -> RetrievalResult:
        question = request.question
        scope = request.scope
        limit = request.limit
        if scope.message_part_ids == () or limit <= 0:
            return RetrievalResult("fts5-v1", 1, (), 0)
        parsed_query = parse_retrieval_query(question, now_ms=self._clock())
        fts_query = _fts_query(parsed_query.text)
        candidates = _retrieval_candidates(parsed_query.text)
        if (
            fts_query is None
            and not candidates
            and parsed_query.time_range is None
        ):
            return RetrievalResult("fts5-v1", 1, (), 0)
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows: list[sqlite3.Row] = []
            seen_part_ids: set[int] = set()
            if fts_query is not None:
                for row in self._query_fts(
                    connection,
                    fts_query,
                    scope.message_part_ids,
                    limit,
                    parsed_query.time_range,
                ):
                    part_id = int(row["message_part_id"])
                    if part_id in seen_part_ids:
                        continue
                    seen_part_ids.add(part_id)
                    rows.append(row)
            if len(rows) < limit and candidates:
                for row in self._query_substrings(
                    connection,
                    candidates,
                    scope.message_part_ids,
                    limit,
                    parsed_query.time_range,
                    minimum_matches=_minimum_candidate_matches(
                        parsed_query.text,
                        candidates,
                    ),
                ):
                    part_id = int(row["message_part_id"])
                    if part_id in seen_part_ids:
                        continue
                    seen_part_ids.add(part_id)
                    rows.append(row)
                    if len(rows) == limit:
                        break
            if (
                len(rows) < limit
                and parsed_query.time_range is not None
                and fts_query is None
                and not candidates
            ):
                for row in self._query_recent(
                    connection,
                    scope.message_part_ids,
                    limit,
                    parsed_query.time_range,
                ):
                    part_id = int(row["message_part_id"])
                    if part_id in seen_part_ids:
                        continue
                    seen_part_ids.add(part_id)
                    rows.append(row)
                    if len(rows) == limit:
                        break
            selected = rows[:limit]
            hits = tuple(
                RetrievalHit(
                    evidence=self._evidence_with_context(
                        connection,
                        row,
                        scope.message_part_ids,
                        parsed_query.time_range,
                    ),
                    rank=index,
                    score=(
                        float(row["relevance"])
                        if "relevance" in row.keys()
                        and row["relevance"] is not None
                        else None
                    ),
                )
                for index, row in enumerate(selected, start=1)
            )
            return RetrievalResult("fts5-v1", 1, hits, len(rows))

    @staticmethod
    def _query_fts(
        connection: sqlite3.Connection,
        query: str,
        part_ids: tuple[int, ...] | None,
        limit: int,
        time_range: QueryTimeRange | None,
    ) -> list[sqlite3.Row]:
        batches: Iterable[tuple[int, ...] | None]
        if part_ids is None:
            batches = (None,)
        else:
            batches = _batches(part_ids, 800)
        rows_by_part: dict[int, sqlite3.Row] = {}
        for batch in batches:
            where = ["retrieval_chunk_fts MATCH ?"]
            parameters: list[object] = [query]
            if batch is not None:
                placeholders = ", ".join("?" for _item in batch)
                where.append(
                    f"chunks.message_part_id IN ({placeholders})"
                )
                parameters.extend(batch)
            _append_time_filter(where, parameters, time_range)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT chunks.id AS chunk_id, chunks.message_part_id,
                       chunks.message_id, chunks.conversation_id,
                       chunks.sender_name, chunks.sent_at, chunks.text_content,
                       bm25(
                           retrieval_chunk_fts,
                           5.0,
                           1.0,
                           0.5
                       ) AS relevance
                FROM retrieval_chunk_fts
                JOIN retrieval_chunks AS chunks
                  ON chunks.id = retrieval_chunk_fts.rowid
                WHERE {" AND ".join(where)}
                ORDER BY relevance ASC, chunks.sent_at DESC, chunks.id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            for row in rows:
                part_id = int(row["message_part_id"])
                current = rows_by_part.get(part_id)
                if current is None or _fts_row_key(row) < _fts_row_key(current):
                    rows_by_part[part_id] = row
        return sorted(
            rows_by_part.values(),
            key=_fts_row_key,
        )[:limit]

    @staticmethod
    def _query_substrings(
        connection: sqlite3.Connection,
        candidates: Sequence[str],
        part_ids: tuple[int, ...] | None,
        limit: int,
        time_range: QueryTimeRange | None,
        *,
        minimum_matches: int,
    ) -> list[sqlite3.Row]:
        if not candidates:
            return []
        patterns = [f"%{_escape_like(candidate)}%" for candidate in candidates]
        score_sql = " + ".join(
            "CASE WHEN chunks.text_content LIKE ? ESCAPE '\\' "
            "THEN ? ELSE 0 END"
            for _candidate in candidates
        )
        match_sql = " OR ".join(
            "chunks.text_content LIKE ? ESCAPE '\\'"
            for _candidate in candidates
        )
        match_count_sql = " + ".join(
            "CASE WHEN chunks.text_content LIKE ? ESCAPE '\\' "
            "THEN 1 ELSE 0 END"
            for _candidate in candidates
        )
        score_parameters: list[object] = []
        for candidate, pattern in zip(candidates, patterns, strict=True):
            score_parameters.extend((pattern, max(1, len(candidate))))

        batches: Iterable[tuple[int, ...] | None]
        if part_ids is None:
            batches = (None,)
        else:
            batches = _batches(part_ids, 800)
        rows_by_part: dict[int, sqlite3.Row] = {}
        for batch in batches:
            where = [
                f"({match_sql})",
                f"({match_count_sql}) >= ?",
            ]
            parameters = [
                *score_parameters,
                *patterns,
                *patterns,
                minimum_matches,
            ]
            if batch is not None:
                placeholders = ", ".join("?" for _item in batch)
                where.append(
                    f"chunks.message_part_id IN ({placeholders})"
                )
                parameters.extend(batch)
            _append_time_filter(where, parameters, time_range)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT chunks.id AS chunk_id, chunks.message_part_id,
                       chunks.message_id, chunks.conversation_id,
                       chunks.sender_name, chunks.sent_at, chunks.text_content,
                       ({score_sql}) AS lexical_score
                FROM retrieval_chunks AS chunks
                WHERE {" AND ".join(where)}
                ORDER BY lexical_score DESC,
                         chunks.sent_at DESC,
                         chunks.id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            for row in rows:
                part_id = int(row["message_part_id"])
                current = rows_by_part.get(part_id)
                if (
                    current is None
                    or _substring_row_key(row) < _substring_row_key(current)
                ):
                    rows_by_part[part_id] = row
        return sorted(rows_by_part.values(), key=_substring_row_key)[:limit]

    @staticmethod
    def _query_recent(
        connection: sqlite3.Connection,
        part_ids: tuple[int, ...] | None,
        limit: int,
        time_range: QueryTimeRange,
    ) -> list[sqlite3.Row]:
        batches: Iterable[tuple[int, ...] | None]
        if part_ids is None:
            batches = (None,)
        else:
            batches = _batches(part_ids, 800)
        rows_by_part: dict[int, sqlite3.Row] = {}
        for batch in batches:
            where: list[str] = []
            parameters: list[object] = []
            if batch is not None:
                placeholders = ", ".join("?" for _item in batch)
                where.append(
                    f"chunks.message_part_id IN ({placeholders})"
                )
                parameters.extend(batch)
            _append_time_filter(where, parameters, time_range)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT chunks.id AS chunk_id, chunks.message_part_id,
                       chunks.message_id, chunks.conversation_id,
                       chunks.sender_name, chunks.sent_at, chunks.text_content
                FROM retrieval_chunks AS chunks
                WHERE {" AND ".join(where)}
                ORDER BY chunks.sent_at DESC, chunks.id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            for row in rows:
                rows_by_part[int(row["message_part_id"])] = row
        return sorted(
            rows_by_part.values(),
            key=lambda row: (-int(row["sent_at"]), -int(row["chunk_id"])),
        )[:limit]

    @classmethod
    def _evidence_with_context(
        cls,
        connection: sqlite3.Connection,
        hit_row: sqlite3.Row,
        part_ids: tuple[int, ...] | None,
        time_range: QueryTimeRange | None,
    ) -> AgentEvidence:
        hit = _evidence_from_row(hit_row)
        before = cls._neighbor_row(
            connection,
            hit_row,
            part_ids,
            time_range,
            before=True,
        )
        after = cls._neighbor_row(
            connection,
            hit_row,
            part_ids,
            time_range,
            before=False,
        )
        if before is None and after is None:
            return hit
        lines: list[str] = []
        if before is not None:
            lines.append(_context_line("前文", before))
        lines.append(_context_line("命中", hit_row))
        if after is not None:
            lines.append(_context_line("后文", after))
        return AgentEvidence(
            message_id=hit.message_id,
            part_id=hit.part_id,
            conversation_id=hit.conversation_id,
            sender_name=hit.sender_name,
            sent_at=hit.sent_at,
            excerpt="\n".join(lines),
        )

    @staticmethod
    def _neighbor_row(
        connection: sqlite3.Connection,
        hit_row: sqlite3.Row,
        part_ids: tuple[int, ...] | None,
        time_range: QueryTimeRange | None,
        *,
        before: bool,
    ) -> sqlite3.Row | None:
        batches: Iterable[tuple[int, ...] | None]
        if part_ids is None:
            batches = (None,)
        else:
            batches = _batches(part_ids, 800)
        selected: sqlite3.Row | None = None
        for batch in batches:
            comparator = "<" if before else ">"
            direction = "DESC" if before else "ASC"
            where = [
                "chunks.conversation_id = ?",
                "chunks.message_id <> ?",
                f"""
                (
                    chunks.sent_at {comparator} ?
                    OR (
                        chunks.sent_at = ?
                        AND chunks.id {comparator} ?
                    )
                )
                """,
            ]
            parameters: list[object] = [
                hit_row["conversation_id"],
                hit_row["message_id"],
                hit_row["sent_at"],
                hit_row["sent_at"],
                hit_row["chunk_id"],
            ]
            if batch is not None:
                placeholders = ", ".join("?" for _item in batch)
                where.append(
                    f"chunks.message_part_id IN ({placeholders})"
                )
                parameters.extend(batch)
            _append_time_filter(where, parameters, time_range)
            row = connection.execute(
                f"""
                SELECT chunks.id AS chunk_id, chunks.message_part_id,
                       chunks.message_id, chunks.conversation_id,
                       chunks.sender_name, chunks.sent_at, chunks.text_content
                FROM retrieval_chunks AS chunks
                WHERE {" AND ".join(where)}
                ORDER BY chunks.sent_at {direction}, chunks.id {direction}
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                continue
            if selected is None:
                selected = row
                continue
            row_key = (int(row["sent_at"]), int(row["chunk_id"]))
            selected_key = (
                int(selected["sent_at"]),
                int(selected["chunk_id"]),
            )
            if (before and row_key > selected_key) or (
                not before and row_key < selected_key
            ):
                selected = row
        return selected


def _context_line(label: str, row: sqlite3.Row) -> str:
    sender = str(row["sender_name"])
    text = str(row["text_content"])[:500]
    return f"[{label}] {sender}: {text}"


def _append_in_filter(
    where: list[str],
    parameters: list[object],
    column: str,
    values: Sequence[object],
) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _item in values)
    where.append(f"{column} IN ({placeholders})")
    parameters.extend(values)


def _positive_int(value: str) -> int | None:
    try:
        identifier = int(value)
    except ValueError:
        return None
    return identifier if identifier > 0 else None


def _batches(values: tuple[int, ...], size: int) -> Iterable[tuple[int, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


class Fts5RetrieverBackend:
    backend_name = "fts5-v1"
    index_version = 1

    def __init__(self, retriever: SqlAgentEvidenceRetriever) -> None:
        self._retriever = retriever

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return self._retriever.retrieve_result(request)


def parse_retrieval_query(
    question: str,
    *,
    now_ms: int,
) -> ParsedRetrievalQuery:
    normalized = question.strip()
    now = datetime.fromtimestamp(now_ms / 1000, tz=_SHANGHAI_TZ)
    time_range: QueryTimeRange | None = None
    expression: str | None = None

    absolute = re.search(
        r"(?P<year>20\d{2})[年\-/](?P<month>\d{1,2})[月\-/]"
        r"(?P<day>\d{1,2})日?",
        normalized,
    )
    if absolute is not None:
        try:
            start = datetime(
                int(absolute.group("year")),
                int(absolute.group("month")),
                int(absolute.group("day")),
                tzinfo=_SHANGHAI_TZ,
            )
        except ValueError:
            start = None
        if start is not None:
            time_range = _query_time_range(start, start + timedelta(days=1))
            expression = absolute.group(0)

    if time_range is None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        relative_days = (
            ("前天", -2),
            ("昨天", -1),
            ("今日", 0),
            ("今天", 0),
        )
        for candidate, offset in relative_days:
            if candidate not in normalized:
                continue
            start = day_start + timedelta(days=offset)
            time_range = _query_time_range(start, start + timedelta(days=1))
            expression = candidate
            break

    if time_range is None and "本周" in normalized:
        start = day_start - timedelta(days=day_start.weekday())
        time_range = _query_time_range(start, start + timedelta(days=7))
        expression = "本周"
    if time_range is None and "上周" in normalized:
        end = day_start - timedelta(days=day_start.weekday())
        time_range = _query_time_range(end - timedelta(days=7), end)
        expression = "上周"
    if time_range is None and "本月" in normalized:
        start = day_start.replace(day=1)
        end = _next_month(start)
        time_range = _query_time_range(start, end)
        expression = "本月"
    if time_range is None and "上月" in normalized:
        end = day_start.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        time_range = _query_time_range(start, end)
        expression = "上月"

    recent = re.search(r"最近(?P<days>[1-9]\d?)天", normalized)
    if time_range is None and recent is not None:
        days = int(recent.group("days"))
        end = now
        time_range = _query_time_range(end - timedelta(days=days), end)
        expression = recent.group(0)

    query_text = normalized
    if expression is not None:
        query_text = query_text.replace(expression, " ", 1)
    for phrase in _GENERIC_CJK_QUESTION_PHRASES:
        query_text = query_text.replace(phrase, " ")
    query_text = re.sub(r"\s+", " ", query_text).strip(" ？?")
    return ParsedRetrievalQuery(query_text, time_range)


def _query_time_range(start: datetime, end: datetime) -> QueryTimeRange:
    return QueryTimeRange(
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
    )


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _append_time_filter(
    where: list[str],
    parameters: list[object],
    time_range: QueryTimeRange | None,
) -> None:
    if time_range is None:
        return
    where.extend(("chunks.sent_at >= ?", "chunks.sent_at < ?"))
    parameters.extend((time_range.start_ms, time_range.end_ms))


def _minimum_candidate_matches(
    question: str,
    candidates: Sequence[str],
) -> int:
    if _CJK_PATTERN.search(question) and len(candidates) >= 3:
        return 2
    return 1


def _retrieval_candidates(question: str) -> list[str]:
    normalized = question.strip()
    if not normalized:
        return []
    candidates = [normalized]
    if _CJK_PATTERN.search(normalized):
        for raw_token in jieba.lcut(normalized, cut_all=False):
            token = "".join(_CJK_PATTERN.findall(raw_token))
            if (
                len(token) >= 2
                and not any(char in _QUESTION_CHARACTERS for char in token)
                and token not in candidates
            ):
                candidates.append(token)
        return candidates[:17]
    for term in re.findall(r"[A-Za-z0-9_]+", normalized):
        if (
            len(term) >= 3
            and term.casefold() not in _ENGLISH_QUESTION_TERMS
            and term not in candidates
        ):
            candidates.append(term)
    return candidates[:17]


def _fts_query(question: str) -> str | None:
    terms: list[str] = []
    for raw_term in re.findall(r"[A-Za-z0-9_]+", question):
        term = raw_term.casefold()
        if (
            len(term) < 2
            or term in _ENGLISH_QUESTION_TERMS
            or term in terms
        ):
            continue
        terms.append(term)
    if _CJK_PATTERN.search(question) and len(terms) < 2:
        return None
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms[:16])


def _fts_row_key(row: sqlite3.Row) -> tuple[float, int, int]:
    return (
        float(row["relevance"]),
        -int(row["sent_at"]),
        -int(row["chunk_id"]),
    )


def _substring_row_key(row: sqlite3.Row) -> tuple[int, int, int]:
    return (
        -int(row["lexical_score"]),
        -int(row["sent_at"]),
        -int(row["chunk_id"]),
    )


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _evidence_from_row(row: sqlite3.Row) -> AgentEvidence:
    return AgentEvidence(
        message_id=int(row["message_id"]),
        part_id=int(row["message_part_id"]),
        conversation_id=str(row["conversation_id"]),
        sender_name=str(row["sender_name"]),
        sent_at=int(row["sent_at"]),
        excerpt=str(row["text_content"])[:500],
    )
