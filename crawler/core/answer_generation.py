"""Canonical, provider-independent grounded answer-generation pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no advisory flock
    _fcntl = None

from crawler.core.answer_context import (
    CANONICAL_ANSWER_PROMPT_VERSION,
    AnswerContext,
    EvidenceBudget,
    ExcludedEvidence,
    build_answer_context,
)
from crawler.core.citations import Citation, CitationRegistry, render_citations_markdown
from crawler.core.grounding import GroundingReport, validate_grounded_answer
from crawler.core.provenance import ProvenanceError, evidence_id_for
from crawler.core.retrieval import CanonicalRetriever, RetrievalRequest, RetrievalResult

INSUFFICIENT_EVIDENCE_MARKER = "INSUFFICIENT_EVIDENCE:"
ANSWER_RUN_SCHEMA_VERSION = 1
CANONICAL_INSUFFICIENT_EVIDENCE_TEXT = (
    "The supplied evidence is insufficient to answer the question."
)


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    GENERATION_FAILED = "generation_failed"
    GROUNDING_FAILED = "grounding_failed"


class AnswerGenerationError(RuntimeError):
    """Base class for sanitized answer-generation failures."""

    code = "answer_generation_error"
    retryable = False

    def __init__(self, safe_message: str):
        self.safe_message = safe_message
        super().__init__(safe_message)


class AnswerContractError(AnswerGenerationError):
    code = "invalid_answer_contract"


class AnswerRunPersistenceError(AnswerGenerationError):
    code = "answer_run_persistence_failed"


class ProviderError(AnswerGenerationError):
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
    retryable = True


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    retryable = True


class MalformedProviderResponseError(ProviderError):
    code = "malformed_provider_response"


class EmptyProviderResponseError(ProviderError):
    code = "empty_provider_response"


class UnsupportedProviderConfigurationError(ProviderError):
    code = "unsupported_provider_configuration"


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    temperature: float = 0.0
    max_output_tokens: int = 1024
    seed: int | None = None
    timeout_seconds: float = 120.0

    def validate(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise AnswerContractError("Generation temperature must be between 0 and 2")
        if not 1 <= self.max_output_tokens <= 32_768:
            raise AnswerContractError("max_output_tokens must be between 1 and 32768")
        if self.timeout_seconds <= 0:
            raise AnswerContractError("Generation timeout must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnswerRequest:
    question: str
    retrieval_request: RetrievalRequest
    database_snapshot_id: str
    generation_provider: str
    generation_model: str
    generation_parameters: GenerationParameters = GenerationParameters()
    prompt_version: str = CANONICAL_ANSWER_PROMPT_VERSION
    evidence_budget: EvidenceBudget = EvidenceBudget()

    def validate(self) -> None:
        if not self.question.strip():
            raise AnswerContractError("Answer question must not be empty")
        self.retrieval_request.validate()
        if self.retrieval_request.query != self.question:
            raise AnswerContractError("Answer question must exactly match the retrieval query")
        if not self.database_snapshot_id.strip():
            raise AnswerContractError("Answer request requires an explicit database snapshot ID")
        if not self.generation_provider.strip() or not self.generation_model.strip():
            raise AnswerContractError("Generation provider and model must be explicit")
        if self.prompt_version != CANONICAL_ANSWER_PROMPT_VERSION:
            raise AnswerContractError(f"Unsupported answer prompt version: {self.prompt_version!r}")
        self.generation_parameters.validate()
        try:
            self.evidence_budget.validate()
        except ValueError as exc:
            raise AnswerContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class GenerationInput:
    system_instructions: str
    user_question: str
    evidence_context: str
    allowed_citation_labels: tuple[str, ...]
    prompt_version: str
    model: str
    parameters: GenerationParameters


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None


class GenerationProvider(Protocol):
    name: str

    def generate(self, generation_input: GenerationInput) -> ProviderResponse: ...


@dataclass(frozen=True, slots=True)
class AnswerFailure:
    code: str
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    max_items: int
    max_characters: int
    selected_evidence_ids: tuple[str, ...]
    excluded_evidence: tuple[ExcludedEvidence, ...]
    evidence_characters: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GenerationMetadata:
    provider: str
    model: str
    prompt_version: str
    parameters: GenerationParameters
    started_at: str
    completed_at: str
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass(frozen=True, slots=True)
class AnswerRunRecord:
    schema_version: int
    run_id: str
    status: str
    provider: str
    model: str
    prompt_version: str
    parameters: GenerationParameters
    seed: int | None
    database_snapshot_id: str
    retrieval_mode: str
    retrieval_top_k: int
    projection_id: str | None
    extraction_run_ids: tuple[str, ...]
    embedding_artifact_id: str | None
    started_at: str
    completed_at: str | None
    input_identifiers: tuple[str, ...]
    output_identifiers: tuple[str, ...]
    error_classification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnswerRunRecorder(Protocol):
    def record(self, record: AnswerRunRecord) -> None: ...


class JsonlAnswerRunRecorder:
    """Append safe lifecycle records to an explicit file, never to source databases."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.parent.is_dir():
            raise AnswerRunPersistenceError("Answer run-log parent directory does not exist")

    def record(self, record: AnswerRunRecord) -> None:
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        try:
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                encoded = payload.encode("utf-8")
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_EX)
                written = 0
                while written < len(encoded):
                    chunk_written = os.write(descriptor, encoded[written:])
                    if chunk_written < 1:
                        raise OSError("short write while persisting answer run metadata")
                    written += chunk_written
                os.fsync(descriptor)
            finally:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                os.close(descriptor)
        except OSError as exc:
            raise AnswerRunPersistenceError("Answer run metadata could not be persisted") from exc


@dataclass(frozen=True, slots=True)
class AnswerResult:
    status: AnswerStatus
    answer_text: str
    citations: tuple[Citation, ...]
    retrieval_result: RetrievalResult
    grounding: GroundingReport
    evidence_selection: EvidenceSelection
    generation: GenerationMetadata
    generation_run_id: str
    database_snapshot_id: str
    retrieval_scope: RetrievalScope
    warnings: tuple[str, ...] = ()
    failure: AnswerFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "answer_text": self.answer_text,
            "citations": [citation.to_dict() for citation in self.citations],
            "retrieval_result": self.retrieval_result.to_dict(),
            "grounding": self.grounding.to_dict(),
            "evidence_selection": self.evidence_selection.to_dict(),
            "generation": self.generation.to_dict(),
            "generation_run_id": self.generation_run_id,
            "database_snapshot_id": self.database_snapshot_id,
            "retrieval_scope": self.retrieval_scope.to_dict(),
            "warnings": list(self.warnings),
            "failure": self.failure.to_dict() if self.failure else None,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _question_id(question: str) -> str:
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return f"question-sha256:{digest}"


def _answer_id(answer: str, snapshot_id: str, citations: tuple[Citation, ...]) -> str:
    payload = json.dumps(
        {
            "answer": answer,
            "snapshot_id": snapshot_id,
            "citation_ids": [citation.citation_id for citation in citations],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "answer-sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selection(context: AnswerContext, budget: EvidenceBudget) -> EvidenceSelection:
    return EvidenceSelection(
        max_items=budget.max_items,
        max_characters=budget.max_characters,
        selected_evidence_ids=context.selected_evidence_ids,
        excluded_evidence=context.excluded_evidence,
        evidence_characters=context.evidence_characters,
    )


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    mode: str
    top_k: int
    projection_id: str | None
    extraction_run_ids: tuple[str, ...]
    embedding_artifact_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _retrieval_scope(request: AnswerRequest, retrieval: RetrievalResult) -> RetrievalScope:
    return RetrievalScope(
        mode=retrieval.mode,
        top_k=request.retrieval_request.top_k,
        projection_id=retrieval.projection_id,
        extraction_run_ids=tuple(sorted(request.retrieval_request.extraction_run_ids)),
        embedding_artifact_id=retrieval.embedding_artifact_id,
    )


def _resolved_citations(
    report: GroundingReport, registry: CitationRegistry
) -> tuple[Citation, ...]:
    referenced = set(report.citations_referenced)
    return tuple(citation for citation in registry.citations if citation.label in referenced)


class CanonicalAnswerGenerator:
    """The single production owner of retrieval-to-grounded-answer semantics."""

    def __init__(
        self,
        *,
        retriever: CanonicalRetriever,
        providers: Mapping[str, GenerationProvider],
        run_recorder: AnswerRunRecorder,
    ):
        self.retriever = retriever
        self.providers = dict(providers)
        self.run_recorder = run_recorder

    def _record(
        self,
        request: AnswerRequest,
        *,
        run_id: str,
        started_at: str,
        status: str,
        completed_at: str | None = None,
        input_identifiers: tuple[str, ...] = (),
        output_identifiers: tuple[str, ...] = (),
        error_classification: str | None = None,
        embedding_artifact_id: str | None = None,
    ) -> None:
        self.run_recorder.record(
            AnswerRunRecord(
                schema_version=ANSWER_RUN_SCHEMA_VERSION,
                run_id=run_id,
                status=status,
                provider=request.generation_provider,
                model=request.generation_model,
                prompt_version=request.prompt_version,
                parameters=request.generation_parameters,
                seed=request.generation_parameters.seed,
                database_snapshot_id=request.database_snapshot_id,
                retrieval_mode=request.retrieval_request.mode.value,
                retrieval_top_k=request.retrieval_request.top_k,
                projection_id=request.retrieval_request.projection_id,
                extraction_run_ids=tuple(sorted(request.retrieval_request.extraction_run_ids)),
                embedding_artifact_id=embedding_artifact_id,
                started_at=started_at,
                completed_at=completed_at,
                input_identifiers=(_question_id(request.question), *input_identifiers),
                output_identifiers=output_identifiers,
                error_classification=error_classification,
            )
        )

    def generate(self, request: AnswerRequest) -> AnswerResult:
        run_id = "answer-run-" + uuid.uuid4().hex
        started_at = _utc_now()
        self._record(request, run_id=run_id, started_at=started_at, status="pending")
        try:
            request.validate()
        except Exception as exc:
            classification = (
                exc.code if isinstance(exc, AnswerGenerationError) else "invalid_answer_contract"
            )
            self._record(
                request,
                run_id=run_id,
                started_at=started_at,
                status="failed",
                completed_at=_utc_now(),
                error_classification=classification,
            )
            raise
        self._record(request, run_id=run_id, started_at=started_at, status="running")
        try:
            retrieval = self.retriever.retrieve(request.retrieval_request)
            self._validate_retrieval_binding(request, retrieval)
        except Exception as exc:
            completed_at = _utc_now()
            classification = (
                exc.code if isinstance(exc, AnswerGenerationError) else "retrieval_failed"
            )
            self._record(
                request,
                run_id=run_id,
                started_at=started_at,
                status="failed",
                completed_at=completed_at,
                error_classification=classification,
            )
            raise

        try:
            context = build_answer_context(retrieval, budget=request.evidence_budget)
            selection = _selection(context, request.evidence_budget)
        except Exception:
            self._record(
                request,
                run_id=run_id,
                started_at=started_at,
                status="failed",
                completed_at=_utc_now(),
                error_classification="context_construction_failed",
            )
            raise
        provider_started_at = _utc_now()
        timer_started = monotonic()
        if not context.citations.citations:
            response = ProviderResponse(
                CANONICAL_INSUFFICIENT_EVIDENCE_TEXT,
                model=request.generation_model,
            )
            return self._finalize(
                request,
                retrieval,
                context,
                selection,
                response,
                run_id=run_id,
                run_started_at=started_at,
                provider_started_at=provider_started_at,
                timer_started=timer_started,
                forced_insufficient=True,
            )

        provider = self.providers.get(request.generation_provider)
        try:
            if provider is None:
                raise ProviderUnavailableError("Requested generation provider is not configured")
            if provider.name != request.generation_provider:
                raise UnsupportedProviderConfigurationError(
                    "Configured provider identity does not match the answer request"
                )
            response = provider.generate(
                GenerationInput(
                    system_instructions=context.system_instructions,
                    user_question=context.user_question,
                    evidence_context=context.evidence_context,
                    allowed_citation_labels=context.allowed_citation_labels,
                    prompt_version=context.prompt_version,
                    model=request.generation_model,
                    parameters=request.generation_parameters,
                )
            )
            if not isinstance(response, ProviderResponse):
                raise MalformedProviderResponseError(
                    "Generation provider returned an invalid response contract"
                )
            if not isinstance(response.text, str):
                raise MalformedProviderResponseError(
                    "Generation provider returned non-text answer content"
                )
            if not isinstance(response.model, str) or not response.model.strip():
                raise MalformedProviderResponseError(
                    "Generation provider returned an invalid model identity"
                )
            if response.model != request.generation_model:
                raise UnsupportedProviderConfigurationError(
                    "Generation provider response model does not match the requested model"
                )
            token_counts = (
                response.input_tokens,
                response.output_tokens,
                response.total_tokens,
            )
            if any(
                value is not None
                and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
                for value in token_counts
            ):
                raise MalformedProviderResponseError(
                    "Generation provider returned invalid token metadata"
                )
            if (
                response.input_tokens is not None
                and response.output_tokens is not None
                and response.total_tokens is not None
                and response.total_tokens != response.input_tokens + response.output_tokens
            ):
                raise MalformedProviderResponseError(
                    "Generation provider returned inconsistent token metadata"
                )
            if response.finish_reason is not None and not isinstance(response.finish_reason, str):
                raise MalformedProviderResponseError(
                    "Generation provider returned an invalid finish reason"
                )
            if not response.text.strip():
                raise EmptyProviderResponseError("Generation provider returned an empty response")
        except ProviderError as exc:
            return self._generation_failure(
                request,
                retrieval,
                context,
                selection,
                exc,
                run_id=run_id,
                run_started_at=started_at,
                provider_started_at=provider_started_at,
                timer_started=timer_started,
            )
        except (TimeoutError, socket.timeout) as exc:
            failure = ProviderTimeoutError("Generation provider timed out")
            failure.__cause__ = exc
            return self._generation_failure(
                request,
                retrieval,
                context,
                selection,
                failure,
                run_id=run_id,
                run_started_at=started_at,
                provider_started_at=provider_started_at,
                timer_started=timer_started,
            )
        except Exception as exc:
            failure = ProviderError("Generation provider failed")
            failure.__cause__ = exc
            return self._generation_failure(
                request,
                retrieval,
                context,
                selection,
                failure,
                run_id=run_id,
                run_started_at=started_at,
                provider_started_at=provider_started_at,
                timer_started=timer_started,
            )

        return self._finalize(
            request,
            retrieval,
            context,
            selection,
            response,
            run_id=run_id,
            run_started_at=started_at,
            provider_started_at=provider_started_at,
            timer_started=timer_started,
        )

    @staticmethod
    def _validate_retrieval_binding(request: AnswerRequest, retrieval: RetrievalResult) -> None:
        if retrieval.database_snapshot_id != request.database_snapshot_id:
            raise AnswerContractError(
                "Canonical retrieval result does not match the requested database snapshot"
            )
        if retrieval.query != request.question:
            raise AnswerContractError(
                "Canonical retrieval result query does not match the question"
            )
        if retrieval.mode != request.retrieval_request.mode.value:
            raise AnswerContractError("Canonical retrieval mode does not match the answer request")
        if retrieval.projection_id != request.retrieval_request.projection_id:
            raise AnswerContractError(
                "Canonical retrieval projection does not match the answer request"
            )
        if any(
            item.database_snapshot_id != request.database_snapshot_id for item in retrieval.items
        ):
            raise AnswerContractError("Retrieved evidence crosses the requested snapshot boundary")
        if len(retrieval.items) > request.retrieval_request.top_k:
            raise AnswerContractError("Canonical retrieval exceeded the requested result bound")
        expected_runs = set(request.retrieval_request.extraction_run_ids)
        for rank, item in enumerate(retrieval.items, start=1):
            if item.rank != rank:
                raise AnswerContractError(
                    "Retrieved evidence ranks are not canonical and contiguous"
                )
            content_hash = hashlib.sha256(item.evidence_text.encode("utf-8")).hexdigest()
            try:
                expected_evidence_id = evidence_id_for(
                    item.source_type, item.source_id, content_hash
                )
            except ProvenanceError as exc:
                raise AnswerContractError("Retrieved evidence identity is invalid") from exc
            if item.evidence_id != expected_evidence_id:
                raise AnswerContractError(
                    "Retrieved evidence content does not match its immutable identity"
                )
            item_runs = {assertion.extraction_run_id for assertion in item.assertions}
            item_runs.update(item.extraction_run_ids)
            if expected_runs and not item_runs <= expected_runs:
                raise AnswerContractError(
                    "Retrieved evidence crosses the requested extraction-run scope"
                )

    def _generation_failure(
        self,
        request: AnswerRequest,
        retrieval: RetrievalResult,
        context: AnswerContext,
        selection: EvidenceSelection,
        error: ProviderError,
        *,
        run_id: str,
        run_started_at: str,
        provider_started_at: str,
        timer_started: float,
    ) -> AnswerResult:
        completed_at = _utc_now()
        duration_ms = max(0, round((monotonic() - timer_started) * 1000))
        report = validate_grounded_answer(
            "", context.citations, require_citations=False, reject_invalid=False
        )
        self._record(
            request,
            run_id=run_id,
            started_at=run_started_at,
            status="failed",
            completed_at=completed_at,
            input_identifiers=context.selected_evidence_ids,
            error_classification=error.code,
            embedding_artifact_id=retrieval.embedding_artifact_id,
        )
        return AnswerResult(
            status=AnswerStatus.GENERATION_FAILED,
            answer_text="",
            citations=(),
            retrieval_result=retrieval,
            grounding=report,
            evidence_selection=selection,
            generation=GenerationMetadata(
                provider=request.generation_provider,
                model=request.generation_model,
                prompt_version=request.prompt_version,
                parameters=request.generation_parameters,
                started_at=provider_started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
            generation_run_id=run_id,
            database_snapshot_id=request.database_snapshot_id,
            retrieval_scope=_retrieval_scope(request, retrieval),
            warnings=(
                "No provider fallback was attempted.",
                "Provider errors are sanitized and may omit operational detail.",
            ),
            failure=AnswerFailure(error.code, error.safe_message, error.retryable),
        )

    def _finalize(
        self,
        request: AnswerRequest,
        retrieval: RetrievalResult,
        context: AnswerContext,
        selection: EvidenceSelection,
        response: ProviderResponse,
        *,
        run_id: str,
        run_started_at: str,
        provider_started_at: str,
        timer_started: float,
        forced_insufficient: bool = False,
    ) -> AnswerResult:
        completed_at = _utc_now()
        duration_ms = max(0, round((monotonic() - timer_started) * 1000))
        raw_answer = response.text.strip()
        provider_insufficient = raw_answer.startswith(INSUFFICIENT_EVIDENCE_MARKER)
        if provider_insufficient:
            raw_answer = CANONICAL_INSUFFICIENT_EVIDENCE_TEXT
        insufficient = forced_insufficient or provider_insufficient
        report = validate_grounded_answer(
            raw_answer,
            context.citations,
            require_citations=not insufficient,
            reject_invalid=False,
        )
        citations = _resolved_citations(report, context.citations)
        status = (
            AnswerStatus.GROUNDING_FAILED
            if not report.accepted
            else AnswerStatus.INSUFFICIENT_EVIDENCE
            if insufficient
            else AnswerStatus.ANSWERED
        )
        failure = None
        if status == AnswerStatus.GROUNDING_FAILED:
            failure = AnswerFailure("grounding_validation_failed", report.summary, False)
        answer_identifier = _answer_id(raw_answer, request.database_snapshot_id, citations)
        lifecycle_status = (
            "failed"
            if status == AnswerStatus.GROUNDING_FAILED
            else "skipped"
            if status == AnswerStatus.INSUFFICIENT_EVIDENCE
            else "succeeded"
        )
        self._record(
            request,
            run_id=run_id,
            started_at=run_started_at,
            status=lifecycle_status,
            completed_at=completed_at,
            input_identifiers=context.selected_evidence_ids,
            output_identifiers=(answer_identifier,),
            error_classification=failure.code if failure else None,
            embedding_artifact_id=retrieval.embedding_artifact_id,
        )
        warnings = [
            "Structural citation validation does not establish semantic faithfulness or "
            "factual correctness."
        ]
        warnings.extend(f"Retrieval: {warning}" for warning in retrieval.warnings)
        if context.excluded_evidence:
            warnings.append("Some retrieved evidence was excluded by the explicit evidence budget.")
        if status == AnswerStatus.GROUNDING_FAILED:
            warnings.append("Generated text failed closed and must not be presented as grounded.")
        return AnswerResult(
            status=status,
            answer_text=raw_answer,
            citations=citations,
            retrieval_result=retrieval,
            grounding=report,
            evidence_selection=selection,
            generation=GenerationMetadata(
                provider=request.generation_provider,
                model=request.generation_model,
                prompt_version=request.prompt_version,
                parameters=request.generation_parameters,
                started_at=provider_started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                finish_reason=response.finish_reason,
            ),
            generation_run_id=run_id,
            database_snapshot_id=request.database_snapshot_id,
            retrieval_scope=_retrieval_scope(request, retrieval),
            warnings=tuple(warnings),
            failure=failure,
        )


class OllamaGenerationProvider:
    """Narrow Ollama chat adapter with sanitized, typed failures and no fallback."""

    name = "ollama"

    def __init__(self, endpoint: str):
        endpoint = endpoint.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise UnsupportedProviderConfigurationError("Ollama endpoint must use HTTP or HTTPS")
        self._endpoint = endpoint

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        question_json = (
            json.dumps(
                {"question": generation_input.user_question}, ensure_ascii=False, sort_keys=True
            )
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        allowed_json = json.dumps(generation_input.allowed_citation_labels, ensure_ascii=False)
        user_content = (
            "<user_question_json>\n"
            + question_json
            + "\n</user_question_json>\n\n<allowed_citations_json>\n"
            + allowed_json
            + "\n</allowed_citations_json>\n\n<evidence_collection>\n"
            + generation_input.evidence_context
            + "\n</evidence_collection>"
        )
        parameters = generation_input.parameters
        options: dict[str, Any] = {
            "temperature": parameters.temperature,
            "num_predict": parameters.max_output_tokens,
        }
        if parameters.seed is not None:
            options["seed"] = parameters.seed
        payload = json.dumps(
            {
                "model": generation_input.model,
                "messages": [
                    {"role": "system", "content": generation_input.system_instructions},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
                "options": options,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._endpoint}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=parameters.timeout_seconds) as response:
                raw = response.read()
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError("Ollama generation timed out") from exc
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 404}:
                raise UnsupportedProviderConfigurationError(
                    "Ollama rejected the requested model or configuration"
                ) from exc
            raise ProviderUnavailableError("Ollama generation service is unavailable") from exc
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError("Ollama generation timed out") from exc
            raise ProviderUnavailableError("Ollama generation service is unavailable") from exc
        try:
            body = json.loads(raw)
            text = body["message"]["content"]
            response_model = body["model"]
            if not isinstance(text, str):
                raise TypeError("content is not text")
            if not isinstance(response_model, str) or not response_model.strip():
                raise TypeError("model is not text")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise MalformedProviderResponseError(
                "Ollama returned a malformed generation response"
            ) from exc
        if not text.strip():
            raise EmptyProviderResponseError("Ollama returned an empty generation response")
        input_tokens = body.get("prompt_eval_count")
        output_tokens = body.get("eval_count")
        return ProviderResponse(
            text=text,
            model=response_model,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            total_tokens=(
                input_tokens + output_tokens
                if isinstance(input_tokens, int) and isinstance(output_tokens, int)
                else None
            ),
            finish_reason=body.get("done_reason")
            if isinstance(body.get("done_reason"), str)
            else None,
        )


def render_answer_json(result: AnswerResult, *, indent: int | None = 2) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


def render_answer_markdown(result: AnswerResult) -> str:
    displayed_answer = result.answer_text or "_No answer was generated._"
    if result.status == AnswerStatus.GROUNDING_FAILED:
        displayed_answer = "_Generated text was rejected by grounding validation._"
    lines = ["# Answer", "", f"Status: `{result.status.value}`", "", displayed_answer, ""]
    if result.failure is not None:
        lines.extend([f"Failure: `{result.failure.code}` — {result.failure.message}", ""])
    if result.citations:
        lines.append(render_citations_markdown(CitationRegistry(result.citations)).rstrip())
    else:
        lines.extend(["## Sources", "", "No resolved sources."])
    if result.warnings:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines) + "\n"
