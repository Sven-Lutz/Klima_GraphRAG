"""Synthetic adversarial sentence selection, never independent research labels."""

import json

import numpy as np
import pytest

from crawler.core.answer_generation import GenerationInput, GenerationParameters
from crawler.web.evidence_synthesis import MODEL, EvidenceSynthesisProvider


class SimilarEncoder:
    def encode(self, texts):
        # Even maximal similarity must not authorize an unrelated answer.
        return np.ones((len(texts), 2))


def answer(question, text):
    block = dict(
        citation_label="C1",
        evidence_text=text,
        metadata=dict(municipality_ids=[], heading_context=question),
    )
    return (
        EvidenceSynthesisProvider(encoder=lambda: SimilarEncoder())
        .generate(
            GenerationInput(
                "",
                question,
                "<untrusted_evidence>" + json.dumps(block) + "</untrusted_evidence>",
                ("C1",),
                "synthetic-relevance",
                MODEL,
                GenerationParameters(),
            )
        )
        .text
    )


@pytest.mark.parametrize(
    "question,text",
    [
        (
            "Welche Solarenergieprojekte nennt Musterstadt?",
            "Wie läuft der Ausbau erneuerbarer Energien im Stadtgebiet?",
        ),
        (
            "Wer gibt das Fördergeld für den Wärmeplan?",
            "Das Ergebnis soll ein kommunaler Wärmeplan sein.",
        ),
        (
            "Welche Rolle übernehmen kommunale Versorger?",
            "In Sanierungsgebieten können Eigentümer steuerliche Abschreibungen nutzen.",
        ),
        (
            "Womit werden Rathaus und Sportzentrum beheizt?",
            "Auch die Gemeinde ist an der Genossenschaft beteiligt.",
        ),
        (
            "Welcher Förderansatz begrünt eine Hauswand?",
            "Die Ladeinfrastruktur wird durch ein städtisches Förderprogramm verbessert.",
        ),
        (
            "Welcher Förderansatz begrünt eine Hauswand?",
            "An kahlen Hauswänden und tristen Dächern ist überall Grün.",
        ),
        (
            "Welche Solarenergieprojekte gibt es?",
            "Weitere Informationen zu Solarenergie und Photovoltaik.",
        ),
        (
            "Welche Klimaanpassung dokumentiert Beispielort?",
            "Was als Bekenntnis zum Klimaschutz begann, ist eine lebendige Bewegung.",
        ),
        (
            "Welche Maßnahmen dienen dem Hochwasserschutz?",
            "Hier können Sie mehr über den Hochwasserschutz erfahren.",
        ),
    ],
)
def test_unanswering_sentences_abstain_despite_similarity_and_matching_heading(question, text):
    assert answer(question, text).startswith("INSUFFICIENT_EVIDENCE:")


@pytest.mark.parametrize(
    "question,text,expected",
    [
        (
            "Womit wird das Rathaus beheizt?",
            "Das Rathaus wird mit regionalen Holzpellets beheizt.",
            "Holzpellets",
        ),
        (
            "Welche Rolle übernehmen kommunale Versorger?",
            "Die Versorgungsunternehmen betreiben die kommunalen Wärmenetze.",
            "betreiben",
        ),
        (
            "Welche Solarenergieprojekte gibt es?",
            "Die Gemeinde betreibt eine Photovoltaikanlage auf dem Rathaus.",
            "Photovoltaikanlage",
        ),
        (
            "Wer finanziert den Wärmeplan?",
            "Die Wärmeplanung wird vom Beispielministerium gefördert.",
            "Beispielministerium",
        ),
        (
            "Welche Förderung begrünt Hauswände?",
            "Das Förderprogramm unterstützt die Begrünung von Hauswänden.",
            "Begrünung",
        ),
    ],
)
def test_source_bound_paraphrases_remain_available(question, text, expected):
    assert expected in answer(question, text)


@pytest.mark.parametrize("irrelevant_first", [False, True])
def test_mixed_passage_does_not_append_unanswering_filler(irrelevant_first):
    fact = "Das Rathaus wird mit regionalen Holzpellets beheizt."
    filler = "Auch die Gemeinde ist an der Genossenschaft beteiligt."
    text = " ".join([filler, fact] if irrelevant_first else [fact, filler])
    result = answer("Womit wird das Rathaus beheizt?", text)
    assert "Holzpellets" in result
    assert "Genossenschaft" not in result
