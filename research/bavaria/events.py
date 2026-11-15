"""Narrow actor-action-object candidates; co-occurrence never becomes a relation."""

import re

PATTERN = re.compile(
    r"\b(?P<actor>(?:Stadt|Gemeinde|Markt|Stadtwerke|Landkreis|Stadtrat|Gemeinderat) "
    r"[A-ZÄÖÜ][\w-]*(?:\s+(?:[A-ZÄÖÜ][\w-]*|an|der|am|im|dem|von|zu)){0,7}?)\s+"
    r"(?P<verb>plant|fördert|finanziert|errichtet|betreibt|installiert|beschließt)\s+"
    r"(?P<object>[^.!?\n]{8,220})(?:[.!?\n]|$)"
)
PREDICATES = {
    "plant": "PLANS",
    "fördert": "FUNDS",
    "finanziert": "FUNDS",
    "errichtet": "IMPLEMENTS",
    "betreibt": "OPERATES",
    "installiert": "IMPLEMENTS",
    "beschließt": "ADOPTS",
}


def extract_events(text):
    result = []
    for match in PATTERN.finditer(text):
        quote = text[match.start() : match.end()]
        if re.search(r"\b(?:nicht|kein\w*|könnte|möglicherweise)\b", quote, re.I):
            continue
        result.append(
            {
                "schema": "bavaria-event-candidate-v1",
                "subject": match["actor"].strip(),
                "predicate": PREDICATES[match["verb"]],
                "object": match["object"].strip(),
                "start_char": match.start(),
                "end_char": match.end(),
                "quote": quote,
                "subject_span": [match.start("actor"), match.end("actor")],
                "object_span": [match.start("object"), match.end("object")],
                "municipality_ags": None,
                "reference_date": None,
                "role": "PLANNED" if match["verb"] == "plant" else "SOURCE_REPORTED_ACTION",
                "implementation_independently_verified": False,
                "status": "RULE_CANDIDATE_REQUIRES_ENTAILMENT_SCOPE_TIME_REVIEW",
                "method": "IE4-finite-actor-verb-object-v1",
            }
        )
    return result
