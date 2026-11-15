"""Conservative, inspectable subject-frame conditions for product retrieval.

Municipal assets and territorial totals are distinct scientific populations. Unknown
passage scope stays unknown; these rules do not infer claim-level municipality truth.
"""

import re

from research.bavaria.workspace.core import norm

NEGATIVE_TERRITORY = re.compile(
    r"\b(?:kein\w*\s+(?:Werte|Daten|Zahlen|Angaben)\s+(?:für|fuer|zu|über)\s+"
    r"(?:(?:die|den|das)\s+)?(?:gesamte\w*\s+Stadt|Gesamtstadt)|"
    r"ohne\s+(?:Gesamtstadt\w*|gesamtstädtisch\w*\s+(?:Werte|Daten|Zahlen)))",
    re.I,
)
ASSETS = re.compile(r"\b(?:kommunal|stadtisch)\w*\s+(?:liegenschaft|gebaud)\w*\b")
TERRITORY = re.compile(r"\b(?:gesamtstadt\w*|gesamte\w*\s+stadt)\b")


def subject_conditions(question):
    excluded = ["territory"] if NEGATIVE_TERRITORY.search(question) else []
    positive = NEGATIVE_TERRITORY.sub(" ", question)
    folded = norm(positive)
    requested = []
    if ASSETS.search(folded):
        requested.append("municipal_assets")
    if TERRITORY.search(folded):
        requested.append("territory")
    # Controlled terminology normalization, never a replacement for the original question.
    semantic_query = re.sub(
        r"\b(?:städtisch|staedtisch)\w*\s+Gebäude\w*\b",
        "kommunale Liegenschaften",
        positive,
        flags=re.I,
    )
    expansions = (
        ["kommunale Liegenschaften", "Wärmebedarf"]
        if ("municipal_assets" in requested and re.search(r"warme(?:verbrauch|bedarf)", folded))
        else ["kommunale Liegenschaften"]
        if "municipal_assets" in requested
        else []
    )
    return dict(
        version="subject-conditions-v1",
        requested=requested,
        excluded=excluded,
        positive_query=positive.strip(),
        semantic_query=semantic_query.strip(),
        controlled_expansion=expansions,
        semantics="DOCUMENT_SECTION_FRAME_NOT_INDEPENDENT_CLAIM_VALIDATION",
    )


def passage_subject(text, heading=None):
    # A section heading is stronger than an incidental mention in the paragraph.
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    section = norm(first_line[:200])
    if ASSETS.search(section):
        return "municipal_assets"
    if TERRITORY.search(section):
        return "territory"
    section = norm(heading or "")
    if ASSETS.search(section):
        return "municipal_assets"
    if TERRITORY.search(section):
        return "territory"
    return "unknown"
