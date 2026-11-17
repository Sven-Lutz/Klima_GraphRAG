"""Regressions for source-anchored policy claim ledger (no Ollama calls)."""
import json
from datetime import date
from types import SimpleNamespace as Obj

import pytest

from crawler.core.answer_generation import resolve_municipal_policy_claims

Q = 'Does Regensburg offer subsidies for balcony solar systems?'
H = 'Did Regensburg offer subsidies for balcony solar systems in 2023?'
POS_2023 = 'Die Stadt Regensburg fördert Balkonkraftwerke seit März 2023.'
NEG_2025 = 'Der Erwerb von Balkonkraftwerken wir seit 1. Januar 2025 nicht mehr bezuschusst.'
POS_2026 = 'Ab 2. Januar 2026 fördert die Stadt Regensburg Balkonkraftwerke erneut.'
POS_CURRENT = 'Seit 1. Februar 2026 fördert die Stadt Regensburg Balkonkraftwerke.'
NEG_CURRENT = 'Seit 1. Februar 2026 werden Balkonkraftwerke nicht mehr bezuschusst.'
OTHER_POLICY = 'Der Erwerb von Wärmepumpen wird seit 1. Januar 2025 nicht mehr bezuschusst.'


def claim(citation, quote, status, effective_date='', issuer='MUNICIPALITY'):
    return dict(citation=citation, quote=quote, status=status, effective_date=effective_date, issuer=issuer)


def assess(claims, evidence, question=Q, target='09362000', as_of=date(2026,11,3)):
    selected = tuple(f'e{i+1}' for i in range(len(evidence)))
    citations = tuple(Obj(label=f'C{i+1}') for i in range(len(evidence)))
    items = tuple(Obj(evidence_id=selected[i], evidence_text=text,
                      document=Obj(source_url=url,municipality_ids=(municipality,)))
                  for i,(text,url,municipality) in enumerate(evidence))
    ctx = Obj(selected_evidence_ids=selected,citations=Obj(citations=citations))
    retrieval = Obj(items=items)
    return resolve_municipal_policy_claims(json.dumps({'claims':claims}),question,target,ctx,retrieval,as_of=as_of)


CITY='https://www.regensburg.de/fm/haushalt-2026.pdf'
HIST='https://www.regensburg.de/leben/agenda-2030/ziel-7'
UTILITY='https://rewag.de/foerderung'


def test_termination_overrides_older_funding():
    actual=assess([claim('C1',POS_2023,'ACTIVE'), claim('C2',NEG_2025,'ENDED','2025-01-01')],
                  [(POS_2023,HIST,'09362000'),(NEG_2025,CITY,'09362000')])
    assert actual.verdict=='NO' and actual.citation=='C2' and actual.effective_date=='2025-01-01'


def test_reopened_policy_after_explicit_termination():
    actual=assess([claim('C1',NEG_2025,'ENDED','2025-01-01'),claim('C2',POS_2026,'ACTIVE','2026-01-02')],
                  [(NEG_2025,CITY,'09362000'),(POS_2026,CITY,'09362000')])
    assert actual.verdict=='YES' and actual.citation=='C2'


def test_same_day_official_conflict_is_unknown():
    actual=assess([claim('C1',POS_CURRENT,'ACTIVE','2026-02-01'),claim('C2',NEG_CURRENT,'ENDED','2026-02-01')],
                  [(POS_CURRENT,CITY,'09362000'),(NEG_CURRENT,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_utility_offer_does_not_make_municipal_yes():
    actual=assess([claim('C1',POS_CURRENT,'ACTIVE','2026-02-01',issuer='UTILITY')],
                  [(POS_CURRENT,UTILITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_wrong_municipality_is_not_evidence():
    actual=assess([claim('C1',POS_CURRENT,'ACTIVE','2026-02-01')],
                  [(POS_CURRENT,CITY,'09563000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_quote_not_in_source_is_rejected():
    actual=assess([claim('C1',NEG_2025+' Not a source sentence.','ENDED','2025-01-01')],
                  [(NEG_2025,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_model_fabricated_date_rejected():
    actual=assess([claim('C1',NEG_2025,'ENDED','2026-01-01')],
                  [(NEG_2025,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_historical_year_not_changed_by_later_termination():
    actual=assess([claim('C1',POS_2023,'ACTIVE'),claim('C2',NEG_2025,'ENDED','2025-01-01')],
                  [(POS_2023,HIST,'09362000'),(NEG_2025,CITY,'09362000')],question=H)
    assert actual.verdict=='YES' and actual.citation=='C1'


def test_stale_positive_not_sufficient_for_current_yes():
    actual=assess([claim('C1',POS_2023,'ACTIVE')],[(POS_2023,HIST,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_recent_official_active_program_can_be_yes():
    actual=assess([claim('C1',POS_CURRENT,'ACTIVE','2026-02-01')],[(POS_CURRENT,CITY,'09362000')])
    assert actual.verdict=='YES'


def test_url_agenda_2030_does_not_create_effective_date():
    vague='Die Stadt Regensburg fördert Balkonkraftwerke durch einen Zuschuss.'
    actual=assess([claim('C1',vague,'ACTIVE')],[(vague,HIST,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_different_policy_does_not_create_no():
    actual=assess([claim('C1',OTHER_POLICY,'ENDED','2025-01-01')],[(OTHER_POLICY,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_undated_ambiguous_official_positive_prevents_no():
    vague='Die Stadt Regensburg fördert Balkonkraftwerke durch einen Zuschuss.'
    actual=assess([claim('C1',NEG_2025,'ENDED','2025-01-01'),claim('C2',vague,'ACTIVE')],
                  [(NEG_2025,CITY,'09362000'),(vague,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_empty_claim_ledger_means_unknown():
    actual=assess([],[(NEG_2025,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_negative_about_another_measure_in_separate_sentence_is_not_no():
    mixed=('Die Stadt Regensburg fördert Balkonkraftwerke seit 1. Januar 2026. '
           'Wärmepumpen werden seit 1. Februar 2026 nicht mehr bezuschusst.')
    actual=assess([claim('C1',mixed,'ENDED','2026-02-01')],[(mixed,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_pdf_timestamp_does_not_prove_effective_date():
    text='Stand 1. Januar 2026. Die Stadt Regensburg fördert Balkonkraftwerke.'
    actual=assess([claim('C1',text,'ACTIVE','2026-01-01')],[(text,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_future_offering_does_not_prove_current_yes():
    text='Ab 1. Januar 2027 fördert die Stadt Regensburg Balkonkraftwerke.'
    actual=assess([claim('C1',text,'ACTIVE','2027-01-01')],[(text,CITY,'09362000')])
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_malformed_claim_payload_abstains():
    chosen=Obj(selected_evidence_ids=('e1',),citations=Obj(citations=(Obj(label='C1'),)))
    retrieved=Obj(items=(Obj(evidence_id='e1',evidence_text=NEG_2025,
                             document=Obj(source_url=CITY,municipality_ids=('09362000',))),))
    actual=resolve_municipal_policy_claims('not valid JSON',Q,'09362000',chosen,retrieved)
    assert actual.verdict=='INSUFFICIENT_EVIDENCE'


def test_ollama_claim_extraction_uses_structured_schema(monkeypatch):
    from crawler.core.answer_generation import (
        OllamaGenerationProvider, GenerationInput, GenerationParameters,
        _POLICY_CLAIM_FORMAT_VERSION,
    )
    import crawler.core.answer_generation as generation
    seen = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({'model':'fixture-model','message':{'content':'{"claims": []}'}}).encode()

    def fake_urlopen(req, timeout):
        seen['payload'] = json.loads(req.data)
        return Response()

    monkeypatch.setattr(generation.urllib.request,'urlopen',fake_urlopen)
    provider=OllamaGenerationProvider('http://127.0.0.1:11434')
    result=provider.generate(GenerationInput(
        system_instructions='extract claims',user_question=Q,evidence_context='test quoted evidence',
        allowed_citation_labels=('C1',),prompt_version=_POLICY_CLAIM_FORMAT_VERSION,
        model='fixture-model',parameters=GenerationParameters(max_output_tokens=256),
    ))
    assert result.text == '{"claims": []}'
    assert seen['payload']['format']['properties']['claims']['items']['properties']['citation']['enum']==['C1']
    assert seen['payload']['messages'][0]['content']=='extract claims'
