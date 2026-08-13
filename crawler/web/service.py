"""Application adapter between HTTP presentation and canonical backend contracts."""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from crawler.core.answer_context import EvidenceBudget
from crawler.core.answer_generation import (
    AnswerRequest,
    AnswerResult,
    CanonicalAnswerGenerator,
    GenerationParameters,
    render_answer_json,
    render_answer_markdown,
)
from crawler.core.citations import CitationRegistry, build_citation_registry
from crawler.core.retrieval import (
    CanonicalRetriever,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    render_retrieval_json,
    render_retrieval_markdown,
)


class ProviderAvailability(str, Enum):
    RETRIEVAL_ONLY_READY = "retrieval_only_ready"
    ANSWER_GENERATION_READY = "answer_generation_ready"
    ANSWER_GENERATION_UNAVAILABLE = "answer_generation_unavailable"


@dataclass(frozen=True, slots=True)
class ProviderGate:
    availability: ProviderAvailability
    acceptance_gate: str
    provider: str | None
    model: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["availability"] = self.availability.value
        return payload


@dataclass(frozen=True, slots=True)
class QueryOptions:
    question: str
    mode: RetrievalMode
    top_k: int
    max_evidence_items: int
    max_evidence_characters: int
    generate_answer: bool


@dataclass(frozen=True, slots=True)
class StoredResult:
    result_id: str
    canonical: RetrievalResult | AnswerResult


class ImmutableResultStore:
    """Session-lifetime immutable storage used only for canonical exports."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, StoredResult] = {}

    def put(self, canonical: RetrievalResult | AnswerResult) -> str:
        result_id = "result-" + uuid.uuid4().hex
        with self._lock:
            self._results[result_id] = StoredResult(result_id, canonical)
        return result_id

    def get(self, result_id: str) -> StoredResult | None:
        with self._lock:
            return self._results.get(result_id)


def _citation_registry(result: RetrievalResult | AnswerResult) -> CitationRegistry:
    retrieval = result.retrieval_result if isinstance(result, AnswerResult) else result
    selected_ids = (
        set(result.evidence_selection.selected_evidence_ids)
        if isinstance(result, AnswerResult)
        else None
    )
    selected = tuple(
        item for item in retrieval.items if selected_ids is None or item.evidence_id in selected_ids
    )
    return build_citation_registry(selected)


def _evidence_payload(
    result: RetrievalResult | AnswerResult, registry: CitationRegistry
) -> list[dict[str, Any]]:
    retrieval = result.retrieval_result if isinstance(result, AnswerResult) else result
    citations = {citation.evidence_id: citation for citation in registry.citations}
    payload: list[dict[str, Any]] = []
    for item in retrieval.items:
        entry = item.to_dict()
        citation = citations.get(item.evidence_id)
        entry["citation"] = citation.to_dict() if citation else None
        entry["evidence_kind"] = (
            "derived_graph_assertion" if item.assertions else "authoritative_source_evidence"
        )
        payload.append(entry)
    return payload


def _graph_payload(result: RetrievalResult | AnswerResult) -> dict[str, Any]:
    retrieval = result.retrieval_result if isinstance(result, AnswerResult) else result
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for item in retrieval.items:
        document_id = f"document:{item.document.document_id}"
        evidence_id = f"evidence:{item.evidence_id}"
        nodes[document_id] = {
            "id": document_id,
            "kind": "document",
            "label": item.document.heading_context or item.document.document_id,
            "document_id": item.document.document_id,
            "municipality_ids": list(item.document.municipality_ids),
            "source_url": item.document.source_url,
        }
        nodes[evidence_id] = {
            "id": evidence_id,
            "kind": "evidence",
            "label": item.source_id,
            "evidence_id": item.evidence_id,
        }
        edge_id = f"evidence-document:{item.evidence_id}:{item.document.document_id}"
        edges[edge_id] = {
            "id": edge_id,
            "kind": "evidence_link",
            "source": evidence_id,
            "target": document_id,
            "evidence_ids": [item.evidence_id],
        }
        for assertion in item.assertions:
            subject_id = f"entity:{assertion.subject_id}"
            object_id = f"entity:{assertion.object_id}"
            nodes[subject_id] = {
                "id": subject_id,
                "kind": "entity",
                "label": assertion.subject_name,
                "entity_id": assertion.subject_id,
            }
            nodes[object_id] = {
                "id": object_id,
                "kind": "entity",
                "label": assertion.object_name,
                "entity_id": assertion.object_id,
            }
            edges[assertion.assertion_id] = {
                "id": assertion.assertion_id,
                "kind": "assertion",
                "label": assertion.relation_type,
                "source": subject_id,
                "target": object_id,
                "evidence_ids": [item.evidence_id],
                "graph_artifact_id": assertion.graph_artifact_id,
                "extraction_run_id": assertion.extraction_run_id,
                "evidence_excerpt": assertion.evidence_excerpt,
            }
    return {
        "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
        "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
        "notice": (
            "Graph connectivity is a derived navigation aid and does not by itself prove truth."
        ),
    }


def _inline_answer_parts(result: AnswerResult) -> list[dict[str, str]]:
    """Tokenize canonical labels for links without changing wording or identity."""
    parts: list[dict[str, str]] = []
    cursor = 0
    known = {citation.label for citation in result.citations}
    for match in re.finditer(r"\[(C\d+)\]", result.answer_text):
        if match.start() > cursor:
            parts.append({"kind": "text", "text": result.answer_text[cursor : match.start()]})
        label = match.group(1)
        parts.append(
            {
                "kind": "citation" if label in known else "text",
                "text": match.group(0),
                "label": label,
            }
        )
        cursor = match.end()
    if cursor < len(result.answer_text):
        parts.append({"kind": "text", "text": result.answer_text[cursor:]})
    return parts


class ResearchApplicationService:
    """Canonical query orchestrator; presentation code never accesses stores/providers."""

    def __init__(
        self,
        *,
        retriever: CanonicalRetriever,
        generator: CanonicalAnswerGenerator | None,
        manifest_path: str,
        database_snapshot_id: str,
        projection_id: str | None = None,
        extraction_run_ids: tuple[str, ...] = (),
        provider_gate: ProviderGate,
        demo_mode: bool = False,
        demo_notice: str | None = None,
        store: ImmutableResultStore | None = None,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.manifest_path = manifest_path
        self.database_snapshot_id = database_snapshot_id
        self.projection_id = projection_id
        self.extraction_run_ids = extraction_run_ids
        self.provider_gate = provider_gate
        self.demo_mode = demo_mode
        self.demo_notice = demo_notice
        self.store = store or ImmutableResultStore()

    def status(self) -> dict[str, Any]:
        return {
            "application": "GraphRAG_AL Research",
            "demo_mode": self.demo_mode,
            "demo_notice": (
                self.demo_notice or "Controlled fixture data — not real municipal research data."
                if self.demo_mode
                else None
            ),
            "provider_gate": self.provider_gate.to_dict(),
            "database_snapshot_id": self.database_snapshot_id,
            "projection_id": self.projection_id,
            "supported_modes": [mode.value for mode in RetrievalMode],
        }

    def execute(self, options: QueryOptions) -> dict[str, Any]:
        graph_mode = options.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}
        request = RetrievalRequest(
            query=options.question,
            manifest_path=self.manifest_path,
            mode=options.mode,
            top_k=options.top_k,
            projection_id=self.projection_id if graph_mode else None,
            extraction_run_ids=self.extraction_run_ids if graph_mode else (),
        )
        if options.generate_answer:
            if self.generator is None:
                raise RuntimeError("answer_generation_not_configured")
            canonical: RetrievalResult | AnswerResult = self.generator.generate(
                AnswerRequest(
                    question=options.question,
                    retrieval_request=request,
                    database_snapshot_id=self.database_snapshot_id,
                    generation_provider=self.provider_gate.provider or "unconfigured",
                    generation_model=self.provider_gate.model or "unconfigured",
                    generation_parameters=GenerationParameters(),
                    evidence_budget=EvidenceBudget(
                        options.max_evidence_items,
                        options.max_evidence_characters,
                    ),
                )
            )
        else:
            canonical = self.retriever.retrieve(request)

        result_id = self.store.put(canonical)
        registry = _citation_registry(canonical)
        retrieval = canonical.retrieval_result if isinstance(canonical, AnswerResult) else canonical
        return {
            "result_id": result_id,
            "result_kind": ("answer" if isinstance(canonical, AnswerResult) else "retrieval"),
            "answer_result": (canonical.to_dict() if isinstance(canonical, AnswerResult) else None),
            "retrieval_result": retrieval.to_dict(),
            "inline_answer_parts": (
                _inline_answer_parts(canonical) if isinstance(canonical, AnswerResult) else []
            ),
            "citation_registry": registry.to_dict(),
            "evidence": _evidence_payload(canonical, registry),
            "graph": _graph_payload(canonical),
            "provider_gate": self.provider_gate.to_dict(),
            "demo_mode": self.demo_mode,
        }

    def export(self, result_id: str, format_name: str) -> str | None:
        stored = self.store.get(result_id)
        if stored is None:
            return None
        if isinstance(stored.canonical, AnswerResult):
            renderer = render_answer_json if format_name == "json" else render_answer_markdown
        else:
            renderer = render_retrieval_json if format_name == "json" else render_retrieval_markdown
        return renderer(stored.canonical)

    def close(self) -> None:
        backend = self.retriever.graph_backend
        if backend is not None:
            backend.close()
