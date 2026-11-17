"""Conservative, auditable decision gate for municipality/policy existence.

This module deliberately does NOT infer a policy from keyword matches or from an
LLM's unverified conclusion. It consumes verified, source-anchored assertions
produced by a separate extraction/review stage. The authoritative document text
is required for exact-quote verification; model-produced snippets alone are not
sufficient. See docs/IMPLEMENTATION.md.

No external dependencies. Python >=3.10.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
import json
import re

Label = Literal["yes", "no", "unknown", "conflict"]
Claim = Literal["offers", "explicitly_not_offered", "ended", "planned", "mentions"]
Authority = Literal["municipality", "municipal_utility", "district", "state", "federal", "other", "unknown"]
_AGS = re.compile(r"^[0-9]{8}$")
_CLAIMS = {"offers", "explicitly_not_offered", "ended", "planned", "mentions"}
_AUTHORITIES = {"municipality", "municipal_utility", "district", "state", "federal", "other", "unknown"}


class EvidenceValidationError(ValueError):
    """An untrusted evidence dossier violates its required data contract."""


def _date(s: str | None, field: str) -> date | None:
    if s is None or s == "":
        return None
    if not isinstance(s, str):
        raise EvidenceValidationError(f"{field} must be an ISO YYYY-MM-DD string")
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise EvidenceValidationError(f"{field} must be ISO YYYY-MM-DD: {s!r}") from exc


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class PolicyQuestion:
    question_id: str
    municipality_id: str
    policy_id: str
    as_of: str
    # A current assertion without an explicit effective_until must be this recent.
    # This is a configurable conservatism parameter, NOT proof of current status.
    max_document_age_days: int = 548

    def __post_init__(self) -> None:
        if not self.question_id.strip() or not self.policy_id.strip():
            raise EvidenceValidationError("question_id and policy_id cannot be empty")
        if not _AGS.fullmatch(self.municipality_id):
            raise EvidenceValidationError("municipality_id must be an 8-digit AGS")
        if _date(self.as_of, "as_of") is None:
            raise EvidenceValidationError("as_of is required")
        if not 1 <= self.max_document_age_days <= 36500:
            raise EvidenceValidationError("max_document_age_days out of range")


@dataclass(frozen=True)
class PolicyEvidence:
    evidence_id: str
    municipality_id: str
    policy_id: str
    issuer_ags: str | None
    authority: Authority
    claim: Claim
    source_url: str
    # The complete source chunk / document span that actually contains `quote`.
    document_text: str
    quote: str
    published_at: str | None = None
    effective_from: str | None = None
    effective_until: str | None = None
    # Explicit review by an independent human or validated extraction stage.
    # 'verified' alone is never sufficient: full-text containment is also checked.
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise EvidenceValidationError("evidence_id cannot be empty")
        if not _AGS.fullmatch(self.municipality_id):
            raise EvidenceValidationError("evidence municipality_id must be an 8-digit AGS")
        if self.issuer_ags is not None and not _AGS.fullmatch(self.issuer_ags):
            raise EvidenceValidationError("issuer_ags must be an 8-digit AGS when supplied")
        if self.authority not in _AUTHORITIES:
            raise EvidenceValidationError(f"invalid authority: {self.authority}")
        if self.claim not in _CLAIMS:
            raise EvidenceValidationError(f"invalid claim: {self.claim}")
        url = urlsplit(self.source_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise EvidenceValidationError("source_url must be an ordinary http(s) URL")
        if not self.quote.strip() or not self.document_text.strip():
            raise EvidenceValidationError("quote and document_text must be nonempty")
        if len(self.quote) > 2000:
            raise EvidenceValidationError("quote is too long for an auditable evidence excerpt")
        if _normalise(self.quote) not in _normalise(self.document_text):
            raise EvidenceValidationError("exact quote is not contained in source document_text")
        begin = _date(self.effective_from, "effective_from")
        end = _date(self.effective_until, "effective_until")
        _date(self.published_at, "published_at")
        if begin and end and begin > end:
            raise EvidenceValidationError("effective_from must not be later than effective_until")


@dataclass(frozen=True)
class PolicyDecision:
    question_id: str
    municipality_id: str
    policy_id: str
    as_of: str
    label: Label
    reason: str
    evidence_ids: tuple[str, ...]
    citations: tuple[dict[str, str], ...]
    excluded_evidence_ids: tuple[str, ...]
    # The result is NOT a probability. Never present this as calibrated confidence.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(question: PolicyQuestion, evidence: list[PolicyEvidence]) -> PolicyDecision:
    """Return yes/no only for sufficiently verified, directly issued statements.

    Conflicting definitive statements abstain with `conflict`. Missing or stale
    evidence abstains with `unknown`. A planned programme is never an active YES.
    Absence of evidence can NEVER yield NO, irrespective of crawl completeness.
    """
    as_of = _date(question.as_of, "as_of")
    assert as_of is not None
    ids: set[str] = set()
    usable: dict[str, list[PolicyEvidence]] = {"yes": [], "no": [], "planned": []}
    excluded: list[str] = []
    for item in evidence:
        if item.evidence_id in ids:
            raise EvidenceValidationError(f"duplicate evidence_id: {item.evidence_id}")
        ids.add(item.evidence_id)
        # Guard against cross-municipality and policy leakage before ranking.
        if item.municipality_id != question.municipality_id or item.policy_id != question.policy_id:
            excluded.append(item.evidence_id)
            continue
        # A municipal utility's offering is not automatically a municipality policy.
        if item.authority != "municipality" or item.issuer_ags != question.municipality_id:
            excluded.append(item.evidence_id)
            continue
        if not item.verified or not item.published_at:
            excluded.append(item.evidence_id)
            continue
        published = _date(item.published_at, "published_at")
        effective_from = _date(item.effective_from, "effective_from")
        effective_until = _date(item.effective_until, "effective_until")
        assert published is not None
        if published > as_of:
            excluded.append(item.evidence_id)
            continue
        if effective_from and effective_from > as_of:
            usable["planned"].append(item)
            continue
        if item.claim in ("mentions", "planned"):
            usable["planned"].append(item) if item.claim == "planned" else excluded.append(item.evidence_id)
            continue
        if item.claim == "offers" and effective_until and effective_until < as_of:
            # Proven historical offer, not proof of *current* non-offering.
            excluded.append(item.evidence_id)
            continue
        if item.claim == "explicitly_not_offered" and effective_until and effective_until < as_of:
            excluded.append(item.evidence_id)
            continue
        if published < as_of - timedelta(days=question.max_document_age_days) and not (
            effective_until and effective_until >= as_of
        ):
            # Recency is a conservative admission rule; it does not guarantee truth.
            excluded.append(item.evidence_id)
            continue
        if item.claim == "offers":
            usable["yes"].append(item)
        else:
            usable["no"].append(item)

    yes, no = usable["yes"], usable["no"]
    if yes and no:
        label: Label = "conflict"
        selected = yes + no
        reason = "Conflicting verified official statements; manual reconciliation required."
    elif yes:
        label = "yes"
        selected = yes
        reason = "Verified municipality-issued evidence supports this policy as of the requested date."
    elif no:
        label = "no"
        selected = no
        reason = "Verified explicit municipality-issued non-offering/termination statement; not inferred from missing search results."
    else:
        label = "unknown"
        selected = usable["planned"]
        reason = (
            "Only planned/future statements found; no verified current policy offering."
            if selected else "Insufficient current, directly attributable, verified official evidence."
        )
    selected = sorted(selected, key=lambda e: e.evidence_id)
    citations = tuple({"evidence_id": e.evidence_id, "url": e.source_url, "quote": e.quote} for e in selected)
    return PolicyDecision(
        question_id=question.question_id, municipality_id=question.municipality_id,
        policy_id=question.policy_id, as_of=question.as_of, label=label,
        reason=reason, evidence_ids=tuple(e.evidence_id for e in selected),
        citations=citations, excluded_evidence_ids=tuple(sorted(excluded)),
    )


def decide_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Simple JSON interface: {'question': {...}, 'evidence': [{...}, ...]}."""
    if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
        raise EvidenceValidationError("payload must contain question object and evidence list")
    q = PolicyQuestion(**payload["question"])
    entries = [PolicyEvidence(**item) for item in payload["evidence"]]
    return decide(q, entries).to_dict()


def main() -> None:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Fail-closed municipality policy evidence decision gate")
    ap.add_argument("--input", required=True, type=Path, help="One JSON dossier or JSON array of dossiers")
    ap.add_argument("--output", type=Path, help="Where to save JSON results (also printed if omitted)")
    args = ap.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    results = [decide_dict(item) for item in payload] if isinstance(payload, list) else decide_dict(payload)
    result_text = json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result_text, encoding="utf-8")
    else:
        print(result_text, end="")


if __name__ == "__main__":
    main()
