"""Regression checks based on observed ingestion failures, with general rules."""

import pytest

from crawler.core.retrieval import _lexical_rank_candidate


_ALIASES = ('Balkonkraftwerk', 'Mini-Solaranlagen', 'Mini-PV', 'Steckersolar')
_URL = 'https://www.ingolstadt.de/sessionnet/getfile.php?id=219662&type=do'


def evidence_allowed(body, heading='Mini-Solaranlagen Förderprogramm', url=_URL):
    return _lexical_rank_candidate(
        body, heading, url,
        query='Bietet Ingolstadt einen Zuschuss für Balkonkraftwerke an?',
        aliases=_ALIASES, family='balkonkraftwerk_subsidy',
        support_kind='financial', channels={'concept_support': (1, -3.0)},
        query_city='ingolstadt', query_terms=frozenset({'balkonkraftwerk','zuschuss'}),
    )[1]


@pytest.mark.parametrize('fragment', [
    'Der Antrag wird abgelehnt.timmung zum Antrag der AfD-Fraktion:edoch keine pauschale einehng,',
    'Kraft. Siehe hierzu unseren noch nicht bearbeiteten Antrag vom 10.05.em 01.01.2024 insungmit',
    'Oberbürgermeister Drnne. besprechen, welches allerdings unter dem Tagesordnungspunkt 3en zum',
    'Hinweise zu „TAB 2023 v2.0“ Seite 21 von 70netzagentur aber als Wirkleistung angegeben werden.nischen Regelwerkin',
    'www.bdew.de Seite 20 von 61hluss der Bundesnetzagentur aber als Wirkleistung angegeben werden.nischen Regelwerkin',
])
def test_broken_pdf_snippets_not_safe_as_evidence(fragment):
    assert not evidence_allowed(fragment)


def test_explicit_short_discontinuation_retained():
    assert evidence_allowed('Die Förderung von Mini-Solaranlagen wurde zum 1. Januar 2025 eingestellt.')


def test_agenda_not_excluded_by_pdf_quality_rule():
    assert evidence_allowed('Ö 8 Kommunale Förderprogramme: Förderungen durch die Stadt Ingolstadt von Mini-Solaranlagen.',
                            url='https://www.ingolstadt.de/sessionnet/si0056.php?__ksinr=8764')
