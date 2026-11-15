"""Deterministic citation identities and renderers independent of LLM wording."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from crawler.core.retrieval import RetrievedEvidence


class CitationError(RuntimeError):
    """Raised when citation inputs are inconsistent or ambiguous."""


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    label: str
    evidence_id: str
    database_snapshot_id: str
    extraction_run_ids: tuple[str, ...]
    graph_artifact_ids: tuple[str, ...]
    source_type: str
    source_id: str
    segment_id: str
    chunk_id: str | None
    document_id: str
    municipality_ids: tuple[str, ...]
    source_url: str
    heading_context: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CitationRegistry:
    citations: tuple[Citation, ...]

    @property
    def by_label(self) -> dict[str, Citation]:
        return {citation.label: citation for citation in self.citations}

    @property
    def by_id(self) -> dict[str, Citation]:
        return {citation.citation_id: citation for citation in self.citations}

    def to_dict(self) -> dict[str, Any]:
        return {"citations": [citation.to_dict() for citation in self.citations]}


def citation_id_for(item: RetrievedEvidence) -> str:
    """Return an immutable identity; display order never participates in it."""
    payload = json.dumps(
        {
            "database_snapshot_id": item.database_snapshot_id,
            "evidence_id": item.evidence_id,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "document_id": item.document.document_id,
            "extraction_run_ids": item.extraction_run_ids,
            "graph_artifact_ids": item.graph_artifact_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "citation-sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_citation_registry(items: tuple[RetrievedEvidence, ...]) -> CitationRegistry:
    seen: set[str] = set()
    citations: list[Citation] = []
    for ordinal, item in enumerate(items, start=1):
        citation_id = citation_id_for(item)
        if citation_id in seen:
            raise CitationError(f"Duplicate citation identity for evidence {item.evidence_id}")
        seen.add(citation_id)
        citations.append(
            Citation(
                citation_id=citation_id,
                label=f"C{ordinal}",
                evidence_id=item.evidence_id,
                database_snapshot_id=item.database_snapshot_id,
                extraction_run_ids=item.extraction_run_ids,
                graph_artifact_ids=item.graph_artifact_ids,
                source_type=item.source_type,
                source_id=item.source_id,
                segment_id=item.segment_id,
                chunk_id=item.chunk_id,
                document_id=item.document.document_id,
                municipality_ids=item.document.municipality_ids,
                source_url=item.document.source_url,
                heading_context=item.document.heading_context,
            )
        )
    return CitationRegistry(tuple(citations))


def render_citations_json(registry: CitationRegistry, *, indent: int | None = 2) -> str:
    return json.dumps(registry.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


def render_citations_markdown(registry: CitationRegistry) -> str:
    lines = ["## Sources", ""]
    for citation in registry.citations:
        locator = f"{citation.source_type}:{citation.source_id}"
        hierarchy = f"segment: `{citation.segment_id}`"
        if citation.chunk_id is not None:
            hierarchy += f"; chunk: `{citation.chunk_id}`"
        municipalities = ", ".join(citation.municipality_ids) or "unassigned"
        heading = f" — {citation.heading_context}" if citation.heading_context else ""
        runs = ", ".join(f"`{value}`" for value in citation.extraction_run_ids) or "n/a"
        artifacts = ", ".join(f"`{value}`" for value in citation.graph_artifact_ids) or "n/a"
        lines.append(
            f"- [{citation.label}] [{citation.document_id}]({citation.source_url}) — "
            f"{locator}{heading}; {hierarchy}; municipalities: {municipalities}; "
            f"snapshot: `{citation.database_snapshot_id}`; evidence: `{citation.evidence_id}`; "
            f"extraction runs: {runs}; graph artifacts: {artifacts}"
        )
    return "\n".join(lines) + "\n"
