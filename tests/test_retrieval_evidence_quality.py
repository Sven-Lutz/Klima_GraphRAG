"""Focused fail-closed checks for policy evidence quality and source relevance."""

from crawler.core.retrieval import _lexical_rank_candidate


_ALIASES = ("Balkonkraftwerk", "Mini-Solaranlagen", "Mini-PV", "Steckersolar")
_CI = "https://www.ingolstadt.de/sessionnet/getfile.php?id=219662&type=do"
_QUERY = "Bietet Ingolstadt einen Zuschuss für Balkonkraftwerke an?"


def _accepted(text, url=_CI, heading="", channels=None):
    return _lexical_rank_candidate(
        text, heading, url,
        query=_QUERY, aliases=_ALIASES, family="balkonkraftwerk_subsidy",
        support_kind="financial", channels=channels or {"concept_support": (1, -3.0)},
        query_city="ingolstadt", query_terms=frozenset({"balkonkraftwerk", "zuschuss"}),
    )


def test_clipped_council_pdf_must_not_be_treated_as_decision_evidence():
    score, allowed = _accepted("Der Antrag wird abgelehnt.timmung zum Antrag der AfD-Fraktion:edoch keine pauschale einehng,", "https://www.ingolstadt.de/sessionnet/getfile.php?id=219662&type=do", "Mini-Solaranlagen Förderprogramm")
    assert not allowed


def test_short_coherent_official_cancellation_remains_retrievable():
    score, allowed = _accepted(
        "Die Förderung von Mini-Solaranlagen wurde zum 1. Januar 2025 eingestellt.",
        "https://www.ingolstadt.de/sessionnet/getfile.php?id=123&type=do",
    )
    assert allowed


def test_agenda_is_retrievable_but_does_not_prove_active_policy():
    score, allowed = _accepted(
        "Ö 8 Kommunale Förderprogramme: Förderungen durch die Stadt Ingolstadt von Mini-Solaranlagen.",
        "https://www.ingolstadt.de/sessionnet/si0056.php?__ksinr=8764",
    )
    assert allowed


def test_commercial_offer_is_not_city_grant_evidence():
    score, allowed = _accepted(
        "Im Jahressteuergesetz 2022 wurde von der Bundesregierung beschlossen, dass die Besteuerung auf Mini-PV-Anlagen entfällt. Unser Kooperationspartner bietet die Anlagen jetzt günstiger an.",
        "https://sw-i.de/bauen-wohnen/mini-pv-anlage-balkonkraftwerk",
    )
    assert not allowed


def test_clean_council_pdf_scores_better_than_an_agenda():
    decision = _accepted(
        "Mit Beschluss vom 29.02.2024 wurde die Förderung von Mini-Solaranlagen aufgehoben und die Zuschüsse eingestellt.",
        "https://www.ingolstadt.de/sessionnet/getfile.php?id=116&type=do",
    )
    agenda = _accepted(
        "Ö 8 Kommunale Förderprogramme: Förderungen durch die Stadt Ingolstadt von Mini-Solaranlagen, Lastenfahrrädern, Stromsparcheck.",
        "https://www.ingolstadt.de/sessionnet/si0056.php",
    )
    assert decision[1] and agenda[1] and decision[0] > agenda[0]
