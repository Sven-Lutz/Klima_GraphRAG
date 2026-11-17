from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


# Formal planning instruments are different from subsidy programmes. Reuse
# one vocabulary in intent recognition, retrieval and evidence verification.
FORMAL_POLICY_PATTERNS = {
    'climate_concept_adoption': r'\b(?:klimaschutz[-\s]*konzept\w*|klimaschutz[-\s]*fahrplan\w*|integriert\w*\s+klimaschutz\w*|climate action plan\w*|climate mitigation (?:plan|strateg)\w*)',
    'heat_plan_adoption': r'\b(?:w[aä]rme[-\s]*plan\w*|waerme[-\s]*plan\w*|municipal heat(?:ing)? plan\w*|heat planning)\b',
    'adaptation_concept_adoption': r'\b(?:klima(?:wandel)?[-\s]*anpassungs[-\s]*(?:konzept|strateg)\w*|anpassungsstrateg\w*\s+an\s+den\s+klimawandel|climate adaptation (?:plan|strateg|concept)\w*)',
    'mobility_plan_adoption': r'\b(?:mobilit[aä]ts[-\s]*(?:plan|konzept|beschluss|strategie)\w*|mobilitaets[-\s]*(?:plan|konzept|beschluss|strategie)\w*|verkehrsentwicklungs[-\s]*(?:plan|konzept)\w*|masterplan\s+(?:nachhaltige\s+)?mobilit[aä]t|(?:sustainable urban )?mobility (?:plan|strateg|concept)\w*|transport development plan\w*)',
    'climate_neutrality_target': r'\b(?:klima[-\s]*neutral\w*|treibhausgas[-\s]*neutral\w*|thg[-\s]*neutral\w*|climate[-\s]*neutral\w*|net[-\s]*zero)\b',
}
FORMAL_POLICY_ALIASES = {
    'climate_concept_adoption': ('Klimaschutzkonzept', 'integriertes Klimaschutzkonzept', 'Klimaschutzfahrplan'),
    'heat_plan_adoption': ('kommunaler Wärmeplan', 'kommunale Wärmeplanung', 'Wärmeplan'),
    'adaptation_concept_adoption': ('Klimaanpassungskonzept', 'Klimaanpassungsstrategie'),
    'mobility_plan_adoption': ('Mobilitätsplan', 'Mobilitätskonzept', 'Mobilitätsbeschluss', 'Verkehrsentwicklungsplan', 'Verkehrsentwicklungskonzept'),
    'climate_neutrality_target': ('Klimaneutralität', 'klimaneutral', 'Treibhausgasneutralität', 'THG-Neutralität'),
}
FORMAL_ADOPTION_CUE = re.compile(r'\b(?:beschloss\w*|\w*beschlu[ssß]+\w*|verabschied\w*|angenommen|adopt\w*|approv\w*|festgelegt|festgesetzt|setzt\w*\s+.*?ziel)\b', re.I)
_FORMAL_QUERY_CUE = re.compile(r'\b(?:beschloss\w*|beschlu[ssß]+\w*|verabschied\w*|adopt\w*|approv\w*|angenommen|festgelegt|festgesetzt|ziel\w*|target\w*|commit\w*)\b', re.I)


def formal_policy_family(text: str) -> str | None:
    """Recognize adoption/target questions, not all mentions of a policy topic."""
    if not _FORMAL_QUERY_CUE.search(text):
        return None
    # Target requests can mention an underlying plan; a plan adoption request
    # must not become a target query merely because climate neutrality appears.
    if (re.search(FORMAL_POLICY_PATTERNS['climate_neutrality_target'], text, re.I)
            and re.search(r'ziel|target|deadline|commit|bis\s+(?:sp[aä]testens\s+)?20\d\d|by\s+20\d\d', text, re.I)):
        return 'climate_neutrality_target'
    for family, pattern in FORMAL_POLICY_PATTERNS.items():
        if family != 'climate_neutrality_target' and re.search(pattern, text, re.I):
            return family
    return None


def formal_policy_deadline(text: str) -> int | None:
    years = set(re.findall(r'\b(?:bis\s+(?:sp[aä]testens\s+)?|by\s+(?:no later than\s+)?)(20\d{2})\b', text, re.I))
    return int(next(iter(years))) if len(years) == 1 else None


def formal_policy_text_score(text: str, family: str) -> int:
    """Retrieval relevance only; never derive a YES/NO from these cues."""
    pattern = FORMAL_POLICY_PATTERNS.get(family)
    if not pattern:
        return 0
    text = ' '.join(re.sub(r'-\s*\n\s*', '', text).split())
    best = 0
    for match in re.finditer(pattern, text, re.I):
        window = text[max(0, match.start()-180):match.end()+180]
        # Prefer nearby decision language rather than a different decision
        # elsewhere in the chunk. This remains a relevance score only.
        near = text[max(0, match.start()-75):match.end()+75]
        score = 1 + 2 * bool(FORMAL_ADOPTION_CUE.search(near))
        score += bool(re.search(r'\b(?:stadt\s*rat\w*|gemeinde\s*rat\w*|council|abstimmung)\b', window, re.I))
        if family == 'climate_neutrality_target':
            score += 2 * bool(re.search(r'\b(?:bis|vor|by|before)\s+(?:sp[aä]testens\s+)?(?:zum\s+jahr\s+)?20\d{2}\b', near, re.I))
            if re.search(r'\b(?:stadtverwaltung|stadtwerke?|wärmeversorgung|szenario|studie)\b', near, re.I):
                score = max(1, score - 2)
        else:
            score += bool(re.search(r'\b20\d{2}\b', window))
        best = max(best, score)
    return best


class SupportType(str, Enum):
    FINANCIAL = "financial_support"
    FINANCIAL_OR_PRACTICAL = "financial_or_practical_support"
    PRACTICAL = "practical_support"
    ADVICE = "advice"
    INFORMATION = "information"
    REGULATION = "regulation"
    UNSPECIFIED = "unspecified"


class TemporalScope(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class QuestionIntent:
    original_question: str
    municipality_id: str | None
    municipality_name: str | None
    policy_family: str | None
    support_type: SupportType
    temporal_scope: TemporalScope


_MUNICIPALITIES = {
    "ingolstadt": "09161000",
    "münchen": "09162000",
    "munich": "09162000",
    "regensburg": "09362000",
    "aschaffenburg": "09661000",
    "schweinfurt": "09662000",
    "bayreuth": "09462000",
    "erlangen": "09562000",
    "fürth": "09563000",
    "fuerth": "09563000",
    "nürnberg": "09564000",
    "nuernberg": "09564000",
    "würzburg": "09663000",
    "wuerzburg": "09663000",
    "augsburg": "09761000",
}

_CANONICAL_NAMES = {
    "09161000": "Ingolstadt",
    "09162000": "München",
    "09362000": "Regensburg",
    "09661000": "Aschaffenburg",
    "09662000": "Schweinfurt",
    "09462000": "Bayreuth",
    "09562000": "Erlangen",
    "09563000": "Fürth",
    "09564000": "Nürnberg",
    "09663000": "Würzburg",
    "09761000": "Augsburg",
}

_POLICY_PATTERNS = (
    ("balkonkraftwerk_subsidy", (
        r"\bbalkonkraftwerk(?:e|en)?\b",
        r"\bbalcony\s+(?:solar|pv|photovoltaic)(?:\s+(?:systems?|panels?))?\b",
        r"\bplug[\s-]?in\s+solar\b",
        r"\bstecker[- ]?solar\b",
        r"\bsteckbare[nrs]? photovoltaik\b",
    )),
    ("rooftop_pv_subsidy", (
        r"\bdach[- ]?pv\b",
        r"\bphotovoltaik(?:anlage|anlagen)?\b.*\bdach\b",
        r"\bsolaranlage(?:n)?\b.*\bdach\b",
    )),
    ("heat_pump_heating_subsidy", (
        r"\bwärmepumpe(?:n)?\b",
        r"\bheat pump(?:s)?\b",
        r"\bklimafreundliche[nrs]? heizung(?:en)?\b",
    )),
    ("building_energy_renovation", (
        r"\benergetische[nrs]? sanierung(?:en)?\b",
        r"\bgebäudesanierung(?:en)?\b",
        r"\bbuilding renovation\b",
    )),
    ("ev_charging_emobility", (
        r"\bwallbox(?:en)?\b",
        r"\bladeinfrastruktur\b",
        r"\bladestation(?:en)?\b",
        r"\bladesäule(?:n)?\b",
        r"\be[- ]?mobility\b",
        r"\belektromobilität\b",
        r"\bcharging\s+(?:infrastructure|stations?|points?)\b",
    )),
)

_FINANCIAL_TERMS = (
    'subsidies', 'financial support', 'financial assistance', 'subsidized',
    "zuschuss", "förderung", "foerderung", "fördert", "foerdert", "fördern", "foerdern", "finanziell", "subsidy",
    "grant", "funding", "gefördert", "gefoerdert",
)
_ADVICE_TERMS = ("beratung", "beraten", "consultation", "advice", "advisory")
_PRACTICAL_TERMS = ("unterstützung", "unterstuetzung", "practical support", "technical assistance",
                    "installation support", "installationshilfe", "hilfe bei", "support")
# An OR-question accepts a grant OR concrete practical assistance. A request for
# financial support alone must never be answered by an advice-only statement.
_MIXED_SUPPORT = re.compile(
    r"\b(?:f[oö]rderung|foerderung|zuschuss|subsid(?:y|ies)|funding|grants?)\b"
    r".{0,32}?\b(?:oder|or)\b.{0,32}?"
    r"\b(?:unterst[uü]tzung|unterstuetzung|hilfe|beratung|support|assistance)\b"
    r"|\b(?:unterst[uü]tzung|unterstuetzung|support|assistance)\b"
    r".{0,32}?\b(?:oder|or)\b.{0,32}?"
    r"\b(?:f[oö]rderung|foerderung|zuschuss|subsid(?:y|ies)|funding|grants?)\b",
    re.I,
)
_INFORMATION_TERMS = ("information", "informationen", "info", "hinweis")
_REGULATION_TERMS = ("pflicht", "verbot", "satzung", "regelung", "regulation", "ban", "requirement")
_CURRENT_TERMS = (
    "aktuell", "derzeit", "momentan", "heute", "current", "currently",
    "jetzt", "noch", "2026",
)
_HISTORICAL_TERMS = (
    "früher", "historisch", "damals", "previously", "formerly", "past",
    "2020", "2021", "2022", "2023", "2024", "2025",
)


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def extract_question_intent(question: str) -> QuestionIntent:
    text = _normalise(question)

    municipality_id = None
    for name, mid in _MUNICIPALITIES.items():
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            municipality_id = mid
            break

    policy_family = None
    for family, patterns in _POLICY_PATTERNS:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            policy_family = family
            break

    if _MIXED_SUPPORT.search(text):
        support_type = SupportType.FINANCIAL_OR_PRACTICAL
    elif any(term in text for term in _FINANCIAL_TERMS):
        support_type = SupportType.FINANCIAL
    elif any(term in text for term in _ADVICE_TERMS):
        support_type = SupportType.ADVICE
    elif any(term in text for term in _PRACTICAL_TERMS):
        support_type = SupportType.PRACTICAL
    elif any(term in text for term in _INFORMATION_TERMS):
        support_type = SupportType.INFORMATION
    elif any(term in text for term in _REGULATION_TERMS):
        support_type = SupportType.REGULATION
    else:
        support_type = SupportType.UNSPECIFIED

    if any(term in text for term in _CURRENT_TERMS):
        temporal_scope = TemporalScope.CURRENT
    elif any(term in text for term in _HISTORICAL_TERMS):
        temporal_scope = TemporalScope.HISTORICAL
    else:
        temporal_scope = TemporalScope.CURRENT

    return QuestionIntent(
        original_question=question,
        municipality_id=municipality_id,
        municipality_name=_CANONICAL_NAMES.get(municipality_id),
        policy_family=policy_family,
        support_type=support_type,
        temporal_scope=temporal_scope,
    )

# KLIMA_GENERAL_POLICY_FAMILY_COVERAGE_V1_BEGIN
# General-language fallback only; never overwrite an already recognized family.
# These are policy-topic classifiers, not Yes/No policy decisions.
from dataclasses import replace as _klima_intent_replace
import re as _klima_intent_re

_klima_original_extract_question_intent = extract_question_intent


def extract_question_intent(question: str) -> QuestionIntent:
    intent = _klima_original_extract_question_intent(question)
    if intent.policy_family is not None:
        return intent

    q = str(question).casefold()
    # Rooftop and facade photovoltaics. The roof/facade requirement avoids
    # conflating these with separate balcony-solar subsidy programmes.
    pv = bool(_klima_intent_re.search(r"photovoltaik|\bpv\b", q))
    roof_or_facade = bool(_klima_intent_re.search(r"\bdach\w*|\bdäch\w*|fassad\w*", q))
    if pv and roof_or_facade:
        return _klima_intent_replace(intent, policy_family='rooftop_pv_subsidy')

    # Building-envelope energy renovations, including insulation and windows.
    building = bool(_klima_intent_re.search(
        r"gebäudehüll\w*|wohngebäud\w*|gebäudesanier\w*|"
        r"energetisch\w*|\bdämm\w*|fenstertausch\w*", q
    ))
    renovation = bool(_klima_intent_re.search(
        r"sanier\w*|gebäudehüll\w*|\bdämm\w*|fenstertausch\w*", q
    ))
    if building and renovation:
        return _klima_intent_replace(intent, policy_family='building_energy_renovation')

    return intent
# KLIMA_GENERAL_POLICY_FAMILY_COVERAGE_V1_END

# KLIMA_POLICY_V4_BRIDGE_20261108: general PV is broader than rooftop or balcony PV.
_klima_pv_previous_extract = extract_question_intent

def extract_question_intent(question: str) -> QuestionIntent:
    intent = _klima_pv_previous_extract(question)
    formal_family = formal_policy_family(question)
    if formal_family and intent.support_type == SupportType.UNSPECIFIED:
        # A target year is a requested horizon, not an historical as-of date.
        scope = intent.temporal_scope
        if formal_family == 'climate_neutrality_target' and formal_policy_deadline(question) is not None:
            scope = TemporalScope.CURRENT
        return _klima_intent_replace(intent, policy_family=formal_family, temporal_scope=scope)
    if intent.policy_family is not None:
        return intent
    q = str(question).casefold()
    if re.search(r'\b(?:photovoltaik\w*|pv[- ]?anlagen?|pv|solarstrom)\b', q):
        return _klima_intent_replace(intent, policy_family='photovoltaics_general')
    return intent
