from __future__ import annotations

import json
from dataclasses import dataclass

from crawler.core.evidence_verification import EvidenceAssessment, EvidenceVerdict


from crawler.core.measure_matcher import passage_matches_measure

PASSAGE_VERIFIER_PROMPT_VERSION = "passage-verifier-v5"

PASSAGE_VERIFIER_SYSTEM_TEMPLATE = """Analyse ONE evidence passage against ONE question. Extract only facts explicitly supported by this passage.

Question:
{question}

Evidence passage:
{passage}

Return evidence-grounded fields:

1. same_measure
TRUE only if the passage explicitly names or unambiguously denotes the exact measure asked about. Technical equivalents count, but broad related categories do not.
measure_quote must be an exact quotation copied from the passage supporting this decision. If no such span exists, use false and an empty quote.

2. same_support_type
TRUE only if the passage explicitly states the exact requested support type for that measure. For a municipal subsidy/funding question, Zuschuss, explicit finanzielle Förderung, subsidy, or an explicit statement that purchase/installation costs are funded may count.
Feed-in compensation/Einspeisevergütung, cheaper electricity, Mieterstrom savings, commercial prices, product sales, advice, consultation, information, or general climate programmes do NOT count as a municipal subsidy.
support_quote must be an exact quotation copied from the passage that explicitly establishes the requested support. If no such span exists, use false and an empty quote.

3. current_status
AVAILABLE only if an exact passage span establishes that the matching requested support is currently available.
ENDED only if an exact passage span states that it ended, expired, stopped, is no longer available, or does not exist in the latest/current stated period.
UNCLEAR otherwise.
status_quote must be an exact quotation copied from the passage supporting AVAILABLE or ENDED. For UNCLEAR use an empty quote.

Do not infer missing facts. Do not use titles, suggestive wording, general financial benefits, or outside knowledge as proof of a subsidy. Never expand a general programme to a specific measure unless the passage itself explicitly connects them.

Return only JSON with exactly these fields:
{{"same_measure":true,"measure_quote":"exact passage text","same_support_type":true,"support_quote":"exact passage text","current_status":"AVAILABLE|ENDED|UNCLEAR","status_quote":"exact passage text","rationale":"brief reason"}}
"""


@dataclass(frozen=True)
class PassageVerificationRequest:
    question: str
    policy_id: str
    evidence_id: str
    passage: str


def build_passage_verifier_prompt(request: PassageVerificationRequest) -> str:
    return PASSAGE_VERIFIER_SYSTEM_TEMPLATE.format(
        question=request.question.strip(),
        passage=request.passage.strip(),
    )


def _quote_supported(quote: str, passage: str) -> bool:
    quote_norm = " ".join(quote.split())
    passage_norm = " ".join(passage.split())
    return bool(quote_norm) and quote_norm in passage_norm


def parse_passage_verifier_response(
    request: PassageVerificationRequest,
    response_text: str,
) -> EvidenceAssessment:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Passage verifier returned invalid JSON") from exc

    required = {"same_measure", "measure_quote", "same_support_type", "support_quote", "current_status", "status_quote", "rationale"}
    if set(payload) != required:
        raise ValueError("Passage verifier JSON has unexpected fields")

    model_same_measure = payload["same_measure"]
    same_support_type = payload["same_support_type"]
    if not isinstance(model_same_measure, bool) or not isinstance(same_support_type, bool):
        raise ValueError("same_measure and same_support_type must be booleans")

    measure_quote = str(payload["measure_quote"]).strip()
    support_quote = str(payload["support_quote"]).strip()
    status_quote = str(payload["status_quote"]).strip()
    current_status = str(payload["current_status"]).strip().upper()

    if current_status not in {"AVAILABLE", "ENDED", "UNCLEAR"}:
        raise ValueError(f"Invalid current_status: {current_status!r}")

    same_measure = passage_matches_measure(request.policy_id, request.passage)
    if same_support_type and not _quote_supported(support_quote, request.passage):
        same_support_type = False
    if current_status in {"AVAILABLE", "ENDED"} and not _quote_supported(status_quote, request.passage):
        current_status = "UNCLEAR"

    if same_measure and same_support_type and current_status == "AVAILABLE":
        verdict = EvidenceVerdict.YES
    elif same_measure and same_support_type and current_status == "ENDED":
        verdict = EvidenceVerdict.NO
    else:
        verdict = EvidenceVerdict.INSUFFICIENT_EVIDENCE

    return EvidenceAssessment(
        evidence_id=request.evidence_id,
        verdict=verdict,
        rationale=str(payload["rationale"]).strip(),
    )
