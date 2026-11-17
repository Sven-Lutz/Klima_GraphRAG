"""Regression tests for bounded claim extraction and honest truncation status."""
import json

import pytest

from crawler.core.answer_generation import (
    _POLICY_CLAIM_FORMAT_VERSION,
    _POLICY_CLAIM_SYSTEM,
    _require_complete_claim_response,
    GenerationInput,
    GenerationParameters,
    OllamaGenerationProvider,
    PolicyClaimOutputTruncatedError,
    ProviderResponse,
)


def test_claim_prompt_requires_short_cross_date_status_quote():
    assert 'FOUR' in _POLICY_CLAIM_SYSTEM
    assert '160 characters' in _POLICY_CLAIM_SYSTEM
    assert 'reopenings' in _POLICY_CLAIM_SYSTEM
    assert 'positive and negative' in _POLICY_CLAIM_SYSTEM


def test_claim_extraction_has_structurally_bounded_schema(monkeypatch):
    from crawler.core import answer_generation as generation
    seen = {}

    class Stub:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def read(self):
            return json.dumps({'model': 'fixture', 'message': {'content': '{"claims":[]}'}}).encode()

    def opener(request, timeout):
        seen['body'] = json.loads(request.data)
        return Stub()

    monkeypatch.setattr(generation.urllib.request, 'urlopen', opener)
    result = OllamaGenerationProvider('http://127.0.0.1:11434').generate(
        GenerationInput(
            system_instructions=_POLICY_CLAIM_SYSTEM,
            user_question='Does Regensburg subsidize balcony solar systems?',
            evidence_context='evidence',
            allowed_citation_labels=('C1', 'C2', 'C3'),
            prompt_version=_POLICY_CLAIM_FORMAT_VERSION,
            model='fixture',
            parameters=GenerationParameters(max_output_tokens=768),
        )
    )
    assert result.text == '{"claims":[]}'
    schema = seen['body']['format']['properties']['claims']
    assert schema['maxItems'] == 4
    assert schema['items']['properties']['quote']['maxLength'] == 160
    assert schema['items']['properties']['citation']['enum'] == ['C1','C2','C3']
    assert seen['body']['options']['num_predict'] == 768


@pytest.mark.parametrize('finish_reason', ['length', 'max_tokens'])
def test_truncated_output_cannot_be_accepted_as_complete_claim_ledger(finish_reason):
    # This also covers syntactically valid but *incomplete* claim lists.
    response = ProviderResponse(text='{"claims":[]}', model='fixture', finish_reason=finish_reason)
    with pytest.raises(PolicyClaimOutputTruncatedError) as error:
        _require_complete_claim_response(response)
    assert error.value.code == 'policy_claim_output_truncated'


def test_complete_output_can_proceed_to_evidence_resolution():
    _require_complete_claim_response(
        ProviderResponse(text='{"claims":[]}', model='fixture', finish_reason='stop')
    )
