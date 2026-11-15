"""Strict restoration of persisted retrieval provenance for local source navigation."""

from __future__ import annotations

import hashlib

from crawler.core.provenance import evidence_id_for
from crawler.core.retrieval import (
    DocumentSource,
    GraphAssertion,
    RetrievalResult,
    RetrievalTrace,
    RetrievedEvidence,
    SearchScope,
)


def restore_retrieval(value, snapshot):
    if value["database_snapshot_id"] != snapshot:
        raise ValueError("History belongs to another snapshot")
    items = []
    for row in value["items"]:
        row = dict(row)
        if row["database_snapshot_id"] != snapshot:
            raise ValueError("History evidence crosses snapshot")
        if (
            evidence_id_for(
                row["source_type"],
                row["source_id"],
                hashlib.sha256(row["evidence_text"].encode()).hexdigest(),
            )
            != row["evidence_id"]
        ):
            raise ValueError("History evidence hash mismatch")
        document = dict(row["document"])
        document["municipality_ids"] = tuple(document["municipality_ids"])
        row["document"] = DocumentSource(**document)
        assertions = []
        for assertion in row["assertions"]:
            assertion = dict(assertion)
            assertion["required_source_ids"] = tuple(assertion.get("required_source_ids", []))
            if assertion.get("claim_scope_ags") is not None:
                assertion["claim_scope_ags"] = tuple(assertion["claim_scope_ags"])
            assertions.append(GraphAssertion(**assertion))
        row["assertions"] = tuple(assertions)
        row["retrieval_traces"] = tuple(RetrievalTrace(**t) for t in row["retrieval_traces"])
        for key in ["extraction_run_ids", "graph_artifact_ids"]:
            row[key] = tuple(row[key])
        items.append(RetrievedEvidence(**row))
    scope = dict(value["search_scope"]) if value.get("search_scope") else None
    if scope:
        scope["requested_ags"] = tuple(scope["requested_ags"])
        scope["effective_ags"] = tuple(scope["effective_ags"])
    return RetrievalResult(
        value["query"],
        value["mode"],
        snapshot,
        value["projection_id"],
        tuple(items),
        embedding_artifact_id=value.get("embedding_artifact_id"),
        warnings=tuple(value.get("warnings", [])),
        search_scope=SearchScope(**scope) if scope else None,
        diagnostics=value.get("diagnostics"),
    )


def restore_result(payload, snapshot):
    """Reconstitute the canonical answer, preserving citation/grounding provenance."""
    from crawler.core.answer_context import ExcludedEvidence
    from crawler.core.answer_generation import (
        AnswerFailure,
        AnswerResult,
        AnswerStatus,
        EvidenceSelection,
        GenerationMetadata,
        GenerationParameters,
        RetrievalScope,
    )
    from crawler.core.citations import Citation, build_citation_registry
    from crawler.core.grounding import GroundingReport, validate_grounded_answer

    retrieval = restore_retrieval(payload["retrieval_result"], snapshot)
    if not payload.get("answer_result"):
        return retrieval
    row = dict(payload["answer_result"])
    if row["database_snapshot_id"] != snapshot:
        raise ValueError("Answer history snapshot mismatch")
    row["retrieval_result"] = retrieval
    row["status"] = AnswerStatus(row["status"])
    registry = build_citation_registry(retrieval.items)
    citations = []
    for value in row["citations"]:
        value = dict(value)
        for key in ["extraction_run_ids", "graph_artifact_ids", "municipality_ids"]:
            value[key] = tuple(value[key])
        citation = Citation(**value)
        if citation != registry.by_label.get(citation.label):
            raise ValueError("Persisted citation differs from source registry")
        citations.append(citation)
    row["citations"] = tuple(citations)
    grounding = dict(row["grounding"])
    for key, value in grounding.items():
        if isinstance(value, list):
            grounding[key] = tuple(value)
    row["grounding"] = GroundingReport(**grounding)
    if row["status"] is AnswerStatus.ANSWERED:
        validate_grounded_answer(row["answer_text"], registry)
    selection = dict(row["evidence_selection"])
    selection["selected_evidence_ids"] = tuple(selection["selected_evidence_ids"])
    selection["excluded_evidence"] = tuple(
        ExcludedEvidence(**v) for v in selection["excluded_evidence"]
    )
    row["evidence_selection"] = EvidenceSelection(**selection)
    generation = dict(row["generation"])
    generation["parameters"] = GenerationParameters(**generation["parameters"])
    row["generation"] = GenerationMetadata(**generation)
    scope = dict(row["retrieval_scope"])
    scope["extraction_run_ids"] = tuple(scope["extraction_run_ids"])
    row["retrieval_scope"] = RetrievalScope(**scope)
    row["warnings"] = tuple(row["warnings"])
    row["failure"] = AnswerFailure(**row["failure"]) if row.get("failure") else None
    return AnswerResult(**row)
