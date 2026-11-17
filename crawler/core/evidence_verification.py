from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceVerdict(str, Enum):
    YES = "YES"
    NO = "NO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class EvidenceAssessment:
    evidence_id: str
    verdict: EvidenceVerdict
    rationale: str = ""


@dataclass(frozen=True)
class AggregatedEvidenceDecision:
    verdict: EvidenceVerdict
    supporting_evidence_ids: tuple[str, ...]
    conflicting_evidence_ids: tuple[str, ...]


def aggregate_evidence_assessments(
    assessments: tuple[EvidenceAssessment, ...],
) -> AggregatedEvidenceDecision:
    yes_ids = tuple(a.evidence_id for a in assessments if a.verdict == EvidenceVerdict.YES)
    no_ids = tuple(a.evidence_id for a in assessments if a.verdict == EvidenceVerdict.NO)

    if yes_ids and no_ids:
        return AggregatedEvidenceDecision(
            verdict=EvidenceVerdict.INSUFFICIENT_EVIDENCE,
            supporting_evidence_ids=(),
            conflicting_evidence_ids=yes_ids + no_ids,
        )

    if yes_ids:
        return AggregatedEvidenceDecision(
            verdict=EvidenceVerdict.YES,
            supporting_evidence_ids=yes_ids,
            conflicting_evidence_ids=(),
        )

    if no_ids:
        return AggregatedEvidenceDecision(
            verdict=EvidenceVerdict.NO,
            supporting_evidence_ids=no_ids,
            conflicting_evidence_ids=(),
        )

    return AggregatedEvidenceDecision(
        verdict=EvidenceVerdict.INSUFFICIENT_EVIDENCE,
        supporting_evidence_ids=(),
        conflicting_evidence_ids=(),
    )
