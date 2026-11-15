"""Deterministic post-generation citation and evidence-coverage validation."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from crawler.core.citations import CitationRegistry

_CITATION = re.compile(r"\[([A-Za-z]+\d+)\]")
_BRACKETED = re.compile(r"\[([^\]\n]*)\]")
_SENTENCE = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")


class GroundingValidationError(RuntimeError):
    """Raised when generated output violates the supplied citation allow-list."""

    def __init__(self, report: GroundingReport):
        self.report = report
        super().__init__(report.summary)


@dataclass(frozen=True, slots=True)
class GroundingReport:
    citations_referenced: tuple[str, ...]
    malformed_citations: tuple[str, ...]
    unknown_citations: tuple[str, ...]
    unsupported_citations: tuple[str, ...]
    retrieved_evidence_unused: tuple[str, ...]
    uncited_claims: tuple[str, ...]
    citation_resolution_success: bool

    @property
    def accepted(self) -> bool:
        return not (
            self.malformed_citations
            or self.unknown_citations
            or self.unsupported_citations
            or self.uncited_claims
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def summary(self) -> str:
        return (
            "Generated answer failed grounding validation: "
            f"malformed={list(self.malformed_citations)}, "
            f"unknown={list(self.unknown_citations)}, "
            f"unsupported={list(self.unsupported_citations)}, "
            f"uncited_claims={len(self.uncited_claims)}"
        )


def _requires_citation(sentence: str) -> bool:
    stripped = _CITATION.sub("", sentence).strip()
    if not stripped:
        return False
    words = re.findall(r"\w+", stripped)
    return len(words) >= 4


def validate_grounded_answer(
    answer: str,
    registry: CitationRegistry,
    *,
    require_citations: bool = True,
    supported_labels: set[str] | None = None,
    reject_invalid: bool = True,
) -> GroundingReport:
    """Validate labels and report coverage; this does not claim semantic faithfulness."""
    known = registry.by_label
    referenced = tuple(dict.fromkeys(_CITATION.findall(answer)))
    malformed = tuple(
        dict.fromkeys(
            f"[{match.group(1)}]"
            for match in _BRACKETED.finditer(answer)
            if re.search(r"[Cc]\d", match.group(1)) and _CITATION.fullmatch(match.group(0)) is None
        )
    )
    unknown = tuple(label for label in referenced if label not in known)
    allowed = set(known) if supported_labels is None else set(supported_labels)
    unsupported = tuple(label for label in referenced if label in known and label not in allowed)
    uncited = tuple(
        sentence.strip()
        for sentence in _SENTENCE.split(answer)
        if require_citations and _requires_citation(sentence) and not _CITATION.search(sentence)
    )
    if require_citations and not referenced and not uncited:
        uncited = (answer.strip() or "<empty answer>",)
    used_evidence = {known[label].evidence_id for label in referenced if label in known}
    unused = tuple(
        citation.evidence_id
        for citation in registry.citations
        if citation.evidence_id not in used_evidence
    )
    report = GroundingReport(
        citations_referenced=referenced,
        malformed_citations=malformed,
        unknown_citations=unknown,
        unsupported_citations=unsupported,
        retrieved_evidence_unused=unused,
        uncited_claims=uncited,
        citation_resolution_success=not unknown,
    )
    if reject_invalid and not report.accepted:
        raise GroundingValidationError(report)
    return report
