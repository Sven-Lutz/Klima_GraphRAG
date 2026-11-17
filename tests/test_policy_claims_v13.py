"""Regression tests for source-local time anchoring; no Ollama needed."""
import json
from datetime import date
from types import SimpleNamespace as NS

from crawler.core.answer_generation import (
    _policy_source_local_active_date,
    resolve_municipal_policy_claims,
)

Q = 'Does Regensburg offer subsidies for balcony solar systems?'
CITY = 'https://www.regensburg.de/leben/agenda-2030/ziel-7'
BUDGET = 'https://www.regensburg.de/fm/121/haushalt-2026.pdf'
START = 'Seit März 2023 fördert die Stadt Regensburg Balkonkraftwerke.'
UNDATED = 'Privatpersonen können darüber hinaus auch für den Kauf eines Steckersolargeräts, auch Balkonkraftwerk genannt, eine Förderung beantragen.'
ENDED = 'Der Erwerb von Balkonkraftwerken wir seit 1. Januar 2025 nicht mehr bezuschusst.'
REOPEN = 'Seit März 2026 fördert die Stadt Regensburg Balkonkraftwerke erneut.'


def evaluate(events, claims):
    ids = tuple(f'e{i}' for i in range(1, len(events)+1))
    citations = tuple(NS(label=f'C{i}') for i in range(1, len(events)+1))
    context = NS(citations=NS(citations=citations), selected_evidence_ids=ids)
    items = tuple(NS(evidence_id=ids[i], evidence_text=text,
                     document=NS(source_url=url, municipality_ids=('09362000',)))
                  for i, (text, url) in enumerate(events))
    return resolve_municipal_policy_claims(
        json.dumps({'claims': claims}), Q, '09362000', context, NS(items=items),
        as_of=date(2026,11,3),
    )


def cl(label, quote, status, when=''):
    return {'citation':label,'quote':quote,'status':status,
            'effective_date':when,'issuer':'MUNICIPALITY'}


def test_dated_same_policy_intro_anchors_undated_quote():
    source = START + ' ' + UNDATED
    assert _policy_source_local_active_date(source, UNDATED, 'balkonkraftwerk_subsidy') == ('2023-03-01', True)
    resolved = evaluate([(source, CITY), (ENDED,BUDGET)],
                        [cl('C1',UNDATED,'ACTIVE'),cl('C2',ENDED,'ENDED','2025-01-01')])
    assert resolved.verdict == 'NO' and resolved.citation == 'C2'


def test_undated_official_offer_without_prior_anchor_still_unknown():
    resolved = evaluate([(UNDATED,CITY),(ENDED,BUDGET)],
                        [cl('C1',UNDATED,'ACTIVE'),cl('C2',ENDED,'ENDED','2025-01-01')])
    assert resolved.verdict == 'INSUFFICIENT_EVIDENCE'


def test_unrelated_policy_date_cannot_be_borrowed():
    unrelated = 'Seit März 2026 fördert die Stadt Regensburg Wärmepumpen. '
    source = unrelated + UNDATED
    assert _policy_source_local_active_date(source,UNDATED,'balkonkraftwerk_subsidy') is None
    resolved = evaluate([(source,CITY),(ENDED,BUDGET)],
                        [cl('C1',UNDATED,'ACTIVE'),cl('C2',ENDED,'ENDED','2025-01-01')])
    assert resolved.verdict == 'INSUFFICIENT_EVIDENCE'


def test_genuine_reopening_overrides_termination():
    source = REOPEN + ' ' + UNDATED
    resolved = evaluate([(ENDED,BUDGET),(source,CITY)],
                        [cl('C1',ENDED,'ENDED','2025-01-01'),cl('C2',UNDATED,'ACTIVE')])
    assert resolved.verdict == 'YES' and resolved.effective_date == '2026-03-01'


def test_multiple_local_start_dates_are_ambiguous():
    source = START + ' ' + REOPEN + ' ' + UNDATED
    assert _policy_source_local_active_date(source,UNDATED,'balkonkraftwerk_subsidy') is None


def test_cancelled_between_date_and_quote_blocks_anchor():
    source = START + ' ' + ENDED + ' ' + UNDATED
    assert _policy_source_local_active_date(source,UNDATED,'balkonkraftwerk_subsidy') is None


def test_nearby_url_year_is_not_a_source_date():
    source = UNDATED
    resolved = evaluate([(source,CITY),(ENDED,BUDGET)],
                        [cl('C1',UNDATED,'ACTIVE'),cl('C2',ENDED,'ENDED','2025-01-01')])
    assert resolved.verdict == 'INSUFFICIENT_EVIDENCE'


def test_quote_must_match_original_source():
    source = START + ' ' + UNDATED
    altered = UNDATED.replace('beantragen', 'bekommen')
    resolved = evaluate([(source,CITY),(ENDED,BUDGET)],
                        [cl('C1',altered,'ACTIVE'),cl('C2',ENDED,'ENDED','2025-01-01')])
    assert resolved.verdict == 'INSUFFICIENT_EVIDENCE'


def test_source_local_anchoring_is_city_independent():
    source = 'Seit April 2024 bezuschusst die Stadt Fürth Balkonkraftwerke. ' + UNDATED
    assert _policy_source_local_active_date(source, UNDATED, 'balkonkraftwerk_subsidy') == ('2024-04-01', True)

# Existing test module: coverage / chronology regressions for the full-source
# evidence pass. The model is intentionally allowed to omit relevant events.

def test_omitted_termination_in_original_evidence_still_changes_outcome():
    result = evaluate([(START, CITY), (ENDED, BUDGET)],
                      [cl('C1', START, 'ACTIVE')])
    assert result.verdict == 'NO' and result.citation == 'C2'


def test_omitted_explicit_reopening_is_not_silently_ignored():
    result = evaluate([(START, CITY), (ENDED, BUDGET), (REOPEN, CITY)],
                      [cl('C1', START, 'ACTIVE'),
                       cl('C2', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'YES' and result.citation == 'C3'


def test_omitted_undated_reopening_keeps_unknown():
    result = evaluate([(START, CITY), (ENDED, BUDGET), (UNDATED, CITY)],
                      [cl('C1', START, 'ACTIVE'),
                       cl('C2', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'INSUFFICIENT_EVIDENCE'


def test_omitted_2026_guideline_exclusion_uses_only_explicit_rule_enactment():
    exclusion = ('Diese Richtlinie tritt am 1. Januar 2026 in Kraft. '
                 'Nicht förderfähig sind reine Freiflächen-Photovoltaikanlagen und Balkonkraftwerke.')
    result = evaluate([(START, CITY), (ENDED, BUDGET), (exclusion, CITY)],
                      [cl('C1', START, 'ACTIVE'),
                       cl('C2', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'NO' and result.citation == 'C3'
    assert result.effective_date == '2026-01-01'


def test_undated_guideline_exclusion_cannot_use_url_filename_as_date():
    exclusion = 'Nicht förderfähig sind reine Freiflächen-Photovoltaikanlagen und Balkonkraftwerke.'
    result = evaluate([(START, CITY), (exclusion,
                       'https://www.regensburg.de/r_upload/20260101_Richtlinie_Photovoltaik.pdf')],
                      [cl('C1', START, 'ACTIVE')])
    assert result.verdict == 'INSUFFICIENT_EVIDENCE'


def test_undated_exclusion_does_not_unnecessarily_veto_dated_termination():
    exclusion = 'Nicht förderfähig sind reine Freiflächen-Photovoltaikanlagen und Balkonkraftwerke.'
    result = evaluate([(ENDED, BUDGET), (exclusion, CITY)],
                      [cl('C1', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'NO'


def test_scan_does_not_invent_termination_from_separate_measure_in_clause():
    combined = ('Seit 1. Januar 2026 fördert die Stadt Regensburg Balkonkraftwerke, '
                'Wärmepumpen werden seit 1. März 2026 nicht mehr bezuschusst.')
    result = evaluate([(combined, CITY)],
                      [cl('C1', 'Seit 1. Januar 2026 fördert die Stadt Regensburg Balkonkraftwerke',
                          'ACTIVE', '2026-01-01')])
    assert result.verdict != 'NO'


def test_undated_c1_is_not_falsely_assumed_to_be_from_2023_other_document():
    result = evaluate([(UNDATED, CITY), (START, CITY), (ENDED, BUDGET)],
                      [cl('C1', UNDATED, 'ACTIVE'),
                       cl('C2', START, 'ACTIVE'),
                       cl('C3', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'INSUFFICIENT_EVIDENCE'


def test_utility_source_does_not_count_even_when_model_omits_it():
    result = evaluate([(ENDED, BUDGET), (REOPEN, 'https://rewag.de/foerderung')],
                      [cl('C1', ENDED, 'ENDED', '2025-01-01')])
    assert result.verdict == 'NO'


def test_city_page_quoting_external_provider_cannot_become_municipal_yes():
    from crawler.core.answer_generation import _policy_source_claim_candidates
    # The city can describe a utility's offer; its website is not proof that
    # the utility programme is a municipal grant.
    utility_announcement = 'Seit 1. März 2026 fördert REWAG Balkonkraftwerke.'
    ids = ('e1',)
    context = NS(citations=NS(citations=(NS(label='C1'),)), selected_evidence_ids=ids)
    retrieval = NS(items=(NS(evidence_id='e1', evidence_text=utility_announcement,
                             document=NS(source_url=CITY, municipality_ids=('09362000',))),))
    candidates, overflow = _policy_source_claim_candidates(Q,'09362000',context,retrieval)
    assert not overflow
    assert not any(c['issuer'] == 'MUNICIPALITY' for c in candidates)


def test_regulation_status_is_source_attached_not_filename_dated():
    from crawler.core.answer_generation import _policy_source_claim_candidates
    exclusion = 'Nicht förderfähig sind reine Freiflächen-Photovoltaikanlagen und Balkonkraftwerke.'
    ids = ('e1',)
    context = NS(citations=NS(citations=(NS(label='C1'),)), selected_evidence_ids=ids)
    retrieval = NS(items=(NS(evidence_id='e1', evidence_text=exclusion,
                             document=NS(source_url='https://www.regensburg.de/20260101_richtlinie.pdf',
                                         municipality_ids=('09362000',))),))
    candidates, overflow = _policy_source_claim_candidates(Q,'09362000',context,retrieval)
    assert not overflow
    assert any(c['status'] == 'EXCLUDED' for c in candidates)
    assert all(c['effective_date'] == '' for c in candidates)


def test_very_long_original_sentence_cannot_crash_on_equal_ranked_aliases():
    from crawler.core.answer_generation import _policy_source_claim_candidates
    long = ('Einleitender Text. ' + ' ' * 15 + 'Die Stadt Regensburg fördert Balkonkraftwerke '
            + 'und Steckersolargeräte seit 1. März 2026 ' + 'mit Richtlinien zu vielen Begünstigten ' * 20
            + '.')
    ids = ('e1',)
    context = NS(citations=NS(citations=(NS(label='C1'),)), selected_evidence_ids=ids)
    retrieval = NS(items=(NS(evidence_id='e1', evidence_text=long,
                             document=NS(source_url=CITY, municipality_ids=('09362000',))),))
    candidates, overflow = _policy_source_claim_candidates(Q,'09362000',context,retrieval)
    assert not overflow
    assert isinstance(candidates,list)


def test_regensburg_diagnostic_case_remains_honest_when_advertisement_undated():
    # Mirrors the four policy claims in the user's diagnostic: the 2026 rule
    # exclusion is present in the original source, not the model's four claims.
    exclusion = ('Nicht förderfähig sind reine Freiflächen-Photovoltaikanlagen und '
                 'Balkonkraftwerke.')
    offer = ('50 € je Haushalt für ein Balkonkraftwerk (steckbare PV-Anlage, '
             'Balkonsolar oder auch Mini-PV-Anlage bzw. Mikro-PV-Anlage) '
             'mit maximal 800 Watt-Peak (Wp) und')
    events = [
        (UNDATED, CITY),
        (ENDED, BUDGET),
        (exclusion, 'https://www.regensburg.de/r_upload/20260101_Richtlinie_Photovoltaik.pdf'),
        (START, 'https://www.regensburg.de/fm/121/haushalt-2024.pdf'),
        (offer, 'https://rewag.de/foerderung'),
    ]
    claims = [cl('C1', UNDATED, 'ACTIVE'), cl('C2', ENDED, 'ENDED','2025-01-01'),
              cl('C4', START, 'ACTIVE'),
              {'citation':'C5','quote':offer,'status':'ACTIVE',
               'effective_date':'','issuer':'UTILITY'}]
    result = evaluate(events, claims)
    assert result.verdict == 'INSUFFICIENT_EVIDENCE'
    assert 'Undated active support' in result.reason
