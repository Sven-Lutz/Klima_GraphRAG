from __future__ import annotations

import re


MEASURE_PATTERNS = {
    "balkonkraftwerk_subsidy": (
        r"\bbalkonkraftwerk(?:e|en)?\b",
        r"\bstecker[- ]?solar(?:gerät|geräte|anlage|anlagen)?\b",
        r"\bsteckbare[nrs]? photovoltaik[- ]?stromerzeugungsgerät(?:e)?\b",
        r"\bsteckerfertige[nrs]? (?:pv|photovoltaik)[- ]?anlage(?:n)?\b",
    ),
    "rooftop_pv_subsidy": (
        r"\bphotovoltaik(?:anlage|anlagen)? auf (?:dem |einem )?dach\b",
        r"\bdach[- ]?pv\b",
        r"\bphotovoltaik auf dachflächen\b",
        r"\bsolaranlage(?:n)? auf (?:dem |einem )?dach\b",
    ),
    "heat_pump_heating_subsidy": (
        r"\bwärmepumpe(?:n)?\b",
        r"\bheat pump(?:s)?\b",
        r"\bklimafreundliche[nrs]? heizung(?:en)?\b",
    ),
    "building_energy_renovation": (
        r"\benergetische[nrs]? sanierung(?:en)?\b",
        r"\benergieeffiziente[nrs]? gebäudesanierung(?:en)?\b",
    ),
    "ev_charging_emobility": (
        r"\bwallbox(?:en)?\b",
        r"\bladeinfrastruktur\b",
        r"\bladestation(?:en)?\b",
        r"\bladesäule(?:n)?\b",
    ),
}


def passage_matches_measure(policy_id: str, passage: str) -> bool:
    patterns = MEASURE_PATTERNS.get(policy_id)
    if not patterns:
        raise ValueError(f"Unsupported policy_id: {policy_id!r}")
    text = " ".join(passage.lower().split())
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
