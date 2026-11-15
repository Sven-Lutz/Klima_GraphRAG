"""Synthetic unit regression for the observed Günzburg opening-hours false answer."""
import json

import numpy as np
import pytest

from crawler.core.answer_generation import GenerationInput, GenerationParameters
from crawler.web.evidence_synthesis import MODEL, EvidenceSynthesisProvider


@pytest.mark.parametrize('text,accepted', [
    ('Öffnungszeiten: 8.30 bis 12 Uhr & 14 bis 17 Uhr Fr.', False),
    ('In Günzburg gibt es 24 Ladepunkte.', False),
    ('Die Stadt hat Ladesäulen. Das Rathaus öffnet um 8 Uhr.', False),
    ('Im dokumentierten Stand 2025 gibt es 12 Ladesäulen mit 24 Ladepunkten.', True),
])
def test_count_answer_requires_explicit_quantity_for_requested_unit(text, accepted):
    block = dict(citation_label='C1', evidence_text=text,
                 metadata=dict(municipality_ids=['09774135']))
    class Encoder:
        def encode(self, texts):
            return np.ones((len(texts), 2))

    answer = EvidenceSynthesisProvider(encoder=lambda: Encoder()).generate(GenerationInput(
        '', 'Wie viele Ladesäulen gibt es in Günzburg?',
        '<untrusted_evidence>' + json.dumps(block) + '</untrusted_evidence>',
        ('C1',), 'synthetic-count', MODEL, GenerationParameters(),
    ))
    assert (not answer.text.startswith('INSUFFICIENT_EVIDENCE:')) is accepted
