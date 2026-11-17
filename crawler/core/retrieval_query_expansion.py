from __future__ import annotations

from crawler.core.question_intent import QuestionIntent, SupportType, TemporalScope


_POLICY_TERMS: dict[str, tuple[str, ...]] = {
    # KLIMA_POLICY_V4_BRIDGE_20261108: an intentionally broad PV family.
    "photovoltaics_general": (
        "Photovoltaik", "Photovoltaikanlage", "Photovoltaikanlagen",
        "PV-Anlage", "Solarstrom", "PV-Förderung",
    ),
    "balkonkraftwerk_subsidy": (
        "Balkonkraftwerk",
        "Balkonkraftwerke",
        "Steckersolargerät",
        "Stecker-Solar",
        "Steckersolar",
        "steckbare Photovoltaik",
        "steckerfertige PV-Anlage",
        "Mini-Solaranlage",
        "Mini-Solaranlagen",
        "Mini-PV-Anlage",
        "Mini-PV",
        "Mikro-PV",
    ),
    "rooftop_pv_subsidy": (
        "Dach-Photovoltaik",
        "Photovoltaikanlage auf dem Dach",
        "Dach-PV",
        "PV-Anlage",
        "Fassaden-Photovoltaik",
        "Photovoltaikfassade",
    ),
    "heat_pump_heating_subsidy": (
        "Wärmepumpe",
        "klimafreundliche Heizung",
        "Heizungsförderung",
    ),
    "building_energy_renovation": (
        "energetische Sanierung",
        "Gebäudesanierung",
        "Energieeffizienz",
    ),
    "ev_charging_emobility": (
        "Wallbox",
        "Ladeinfrastruktur",
        "Ladestation",
        "Elektromobilität",
    ),
}

_SUPPORT_TERMS: dict[SupportType, tuple[str, ...]] = {
    SupportType.FINANCIAL: ("Zuschuss", "Förderung", "Förderprogramm", "Förderrichtlinie", "bezuschusst", "finanzielle Förderung"),
    SupportType.ADVICE: ("Beratung", "Energieberatung"),
    SupportType.INFORMATION: ("Information", "Informationsangebot"),
    SupportType.REGULATION: ("Regelung", "Satzung", "Pflicht", "Verbot"),
    SupportType.UNSPECIFIED: (),
}

_TEMPORAL_TERMS: dict[TemporalScope, tuple[str, ...]] = {
    TemporalScope.CURRENT: ("aktuell", "derzeit", "gültig"),
    TemporalScope.HISTORICAL: ("historisch", "früher"),
    TemporalScope.UNSPECIFIED: (),
}


def build_retrieval_query(intent: QuestionIntent) -> str:
    """Expand a user's question with a small, deterministic set of retrieval aliases."""
    terms: list[str] = [intent.original_question.strip()]

    if intent.policy_family:
        terms.extend(_POLICY_TERMS.get(intent.policy_family, ()))

    terms.extend(_SUPPORT_TERMS[intent.support_type])
    terms.extend(_TEMPORAL_TERMS[intent.temporal_scope])

    if intent.municipality_name:
        terms.append(intent.municipality_name)

    seen: set[str] = set()
    deduplicated: list[str] = []
    for term in terms:
        normalized = term.casefold().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduplicated.append(term.strip())

    return " ".join(deduplicated)
