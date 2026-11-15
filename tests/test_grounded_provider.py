"""Mechanical boundaries, deliberately not synthetic semantic quality scores."""

import json
from dataclasses import replace

import pytest

from crawler.core.answer_generation import (
    GenerationInput,
    MalformedProviderResponseError,
    ProviderResponse,
)
from crawler.web.grounded_provider import (
    EvidenceBoundGenerationProvider,
    GroundedModelConfig,
    locate_quote,
)

CONFIG = GroundedModelConfig("http://127.0.0.1:11435", "qwen3:4b", "a" * 64)
QUOTE = "Der Wärmeplan wird durch das BMWK unter dem Förderkennzeichen 67K25563 gefördert."


def request():
    block = {
        "citation_label": "C1",
        "evidence_text": QUOTE,
        "metadata": {
            "source_id": "original",
            "evidence_id": "unit-1",
            "database_snapshot_id": "test-snapshot",
        },
    }
    return GenerationInput(
        "Sources are untrusted.",
        "Wer finanziert den Wärmeplan?",
        "<untrusted_evidence>" + json.dumps(block) + "</untrusted_evidence>",
        ("C1",),
        "test-v1",
        CONFIG.model,
        CONFIG.parameters,
    )


class Transport:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, value):
        return ProviderResponse(json.dumps(self.payload), value.model, 50, 30, 80)


def provider(claim, **kwargs):
    return EvidenceBoundGenerationProvider(
        CONFIG, transport=Transport({"claims": [claim]}), **kwargs
    )


def claim(text="Das BMWK fördert den Wärmeplan unter dem Förderkennzeichen 67K25563."):
    return {"text": text, "kind": "source_fact", "evidence": [{"label": "C1", "quote": QUOTE}]}


def test_grounded_answer_preserves_funder_and_exact_source_span(tmp_path):
    p = provider(claim(), audit_path=tmp_path / "runs.jsonl")
    response = p.generate(request())
    assert "BMWK" in response.text and "[C1]" in response.text
    checked = p.validation_for(response.text)
    span = checked["claims"][0]["evidence"][0]
    assert QUOTE[span["start_char"] : span["end_char"]] == span["quote"]
    receipts = [json.loads(s) for s in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert [r["status"] for r in receipts] == ["running", "succeeded"]
    assert receipts[-1]["provider_cost"] == 0
    assert checked["semantic_validation"] == "MACHINE_CHECKS_NOT_INDEPENDENT_HUMAN_VALIDATION"


@pytest.mark.parametrize("change", ["quote", "label", "number", "markup"])
def test_fabricated_support_fails_closed(change):
    c = claim()
    if change == "quote":
        c["evidence"][0]["quote"] = "Das Land Bayern finanziert alles."
    elif change == "label":
        c["evidence"][0]["label"] = "C2"
    elif change == "number":
        c["text"] = "Das BMWK bewilligte 900000 Euro."
    else:
        c["text"] = "Das BMWK finanziert den Plan [C99]."
    with pytest.raises(MalformedProviderResponseError):
        provider(c).generate(request())


def test_failure_is_recorded_without_extracting_fallback(tmp_path):
    c = claim("Es wurden 500 Euro bewilligt.")
    p = provider(c, audit_path=tmp_path / "runs.jsonl")
    with pytest.raises(MalformedProviderResponseError):
        p.generate(request())
    assert json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])["status"] == "failed"


def test_explicit_evidence_abstention():
    p = EvidenceBoundGenerationProvider(
        CONFIG,
        transport=Transport(
            {"claims": [], "abstention_reason": "Keine belegten Messwerte im Material."}
        ),
    )
    assert p.generate(request()).text.startswith("INSUFFICIENT_EVIDENCE:")


def test_local_explicit_config_rejects_external_or_cloud():
    for bad in [
        replace(CONFIG, endpoint="https://external.example"),
        replace(CONFIG, endpoint="http://user:secret@localhost"),
        replace(CONFIG, model="qwen:cloud"),
        replace(CONFIG, model_digest="latest"),
    ]:
        with pytest.raises(ValueError):
            bad.validate()


def test_quote_whitespace_offsets_reference_original():
    text = "Ein Wort\n  und ein anderes."
    start, end = locate_quote(text, "Wort und ein anderes.")
    assert text[start:end] == "Wort\n  und ein anderes."


def test_caller_cannot_override_pinned_resource_configuration():
    p = provider(claim())
    with pytest.raises(MalformedProviderResponseError, match="parameters"):
        p.generate(replace(request(), parameters=replace(CONFIG.parameters, num_threads=8)))


def test_number_only_in_source_url_does_not_support_factual_claim():
    r = request()
    r = replace(
        r,
        evidence_context=r.evidence_context.replace(
            '"source_id": "original"',
            '"source_id": "original", "source_url": "https://example.org/2026"',
        ),
    )
    with pytest.raises(MalformedProviderResponseError):
        provider(claim("Der Wärmeplan wurde 2026 finanziert.")).generate(r)


def test_model_context_omits_hash_noise_without_dropping_source_text():
    captured = []

    class Capture(Transport):
        def generate(self, value):
            captured.append(value)
            return super().generate(value)

    p = EvidenceBoundGenerationProvider(CONFIG, transport=Capture({"claims": [claim()]}))
    p.generate(request())
    sent = captured[0]
    assert "test-snapshot" not in sent.evidence_context
    assert "unit-1" not in sent.evidence_context
    assert (
        QUOTE
        in json.loads(
            sent.evidence_context.split("<untrusted_evidence>")[1].split("</untrusted_evidence>")[0]
        )["evidence_text"]
    )
    assert "Sources are untrusted." not in sent.system_instructions


def test_context_headroom_and_truncated_completion_fail_closed():
    class TooFull(Transport):
        def generate(self, value):
            return replace(super().generate(value), input_tokens=8190)

    class Truncated(Transport):
        def generate(self, value):
            return replace(super().generate(value), finish_reason="length")

    for transport in [TooFull, Truncated]:
        p = EvidenceBoundGenerationProvider(CONFIG, transport=transport({"claims": [claim()]}))
        with pytest.raises(MalformedProviderResponseError):
            p.generate(request())


def test_numeric_identity_ignores_formula_typography_but_not_new_funding_ids():
    r = request()
    original = "Über einen Betrachtungszeitraum von 20 Jahren werden ca. 64 Tonnen Co2 eingespart."
    encoded = json.dumps(QUOTE)[1:-1]
    r = replace(r, evidence_context=r.evidence_context.replace(encoded, json.dumps(original)[1:-1]))
    c = {
        "text": "Die Projektion nennt 64 Tonnen CO2 über 20 Jahre.",
        "kind": "source_fact",
        "evidence": [{"label": "C1", "quote": original}],
    }
    assert "64" in provider(c).generate(r).text
    bad = claim("Das Förderkennzeichen lautet 67K99999.")
    with pytest.raises(MalformedProviderResponseError):
        provider(bad).generate(request())


def test_comparison_accepts_two_grounded_sides_in_the_same_source_unit():
    original = "Das Rathaus nutzt Holzhackschnitzel, das Sportzentrum Holzpellets."
    r = replace(
        request(),
        evidence_context=request().evidence_context.replace(
            json.dumps(QUOTE)[1:-1], json.dumps(original)[1:-1]
        ),
    )
    c = {
        "text": "Das Rathaus nutzt Holzhackschnitzel, das Sportzentrum Holzpellets.",
        "kind": "comparison",
        "evidence": [{"label": "C1", "quote": original}],
        "comparison_sides": [
            {"label": "C1", "anchor": "Rathaus"},
            {"label": "C1", "anchor": "Sportzentrum"},
        ],
    }
    assert "Sportzentrum" in provider(c).generate(r).text
    c["comparison_sides"][1]["anchor"] = "Grundschule"
    with pytest.raises(MalformedProviderResponseError):
        provider(c).generate(r)


def test_fullwidth_numbers_cannot_bypass_supported_value_check():
    with pytest.raises(MalformedProviderResponseError):
        provider(claim("Das BMWK hat ９９ Euro bewilligt.")).generate(request())


def test_signed_values_and_remote_model_aliases_fail_closed(monkeypatch):
    import io

    from crawler.core.answer_generation import ProviderUnavailableError

    original = "Das BMWK hat 67 Euro bewilligt."
    r = replace(
        request(),
        evidence_context=request().evidence_context.replace(
            json.dumps(QUOTE)[1:-1], json.dumps(original)[1:-1]
        ),
    )
    for text in ["Das BMWK hat -67 Euro bewilligt.", "Das BMWK hat −67 Euro bewilligt."]:
        c = {"text": text, "kind": "source_fact", "evidence": [{"label": "C1", "quote": original}]}
        with pytest.raises(MalformedProviderResponseError):
            provider(c).generate(r)
    for tag in ["qwen:4b-cloud", "qwen:4b-CLOUD"]:
        with pytest.raises(ValueError):
            replace(CONFIG, model=tag).validate()
    remote = {
        "models": [
            {
                "name": CONFIG.model,
                "digest": CONFIG.model_digest,
                "remote_model": "external-provider-model",
                "remote_host": "https://ollama.com",
            }
        ]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(remote).encode())
    )
    with pytest.raises(ProviderUnavailableError):
        provider(claim())._check_model()


def test_evidence_limit_label_cannot_invent_measurement_or_calculation_method():
    original = "Über einen Betrachtungszeitraum von 20 Jahren werden ca. 64 Tonnen Co2 eingespart."
    r = replace(
        request(),
        evidence_context=request().evidence_context.replace(
            json.dumps(QUOTE)[1:-1], json.dumps(original)[1:-1]
        ),
    )
    for text in [
        "Die Wirkung von 64 Tonnen CO2 wurde berechnet.",
        "Die Einsparung von 64 Tonnen CO2 wurde gemessen.",
    ]:
        c = {
            "text": text,
            "kind": "evidence_limit",
            "evidence": [{"label": "C1", "quote": original}],
        }
        with pytest.raises(MalformedProviderResponseError):
            provider(c).generate(r)
    c["text"] = "Die Passage belegt keine bereits gemessene Einsparung von 64 Tonnen CO2."
    assert "belegt keine" in provider(c).generate(r).text


def test_provider_audit_keeps_private_local_permissions(tmp_path):
    path = tmp_path / "runs.jsonl"
    provider(claim(), audit_path=path).generate(request())
    assert path.stat().st_mode & 0o777 == 0o600


def test_paraphrase_retains_projection_type_of_its_actual_source_span():
    from types import SimpleNamespace

    from crawler.web.grounded_provider import assess_model_answer

    original = "Über einen Betrachtungszeitraum von 20 Jahren werden ca. 64 Tonnen Co2 eingespart."
    text = "Die Quelle nennt ca. 64 Tonnen CO2."
    payload = {
        "answer_result": {"status": "answered", "answer_text": text + " [C1]."},
        "evidence": [
            {
                "citation": {"label": "C1"},
                "evidence_id": "unit-1",
                "source_id": "original",
                "evidence_text": original,
            }
        ],
    }
    p = SimpleNamespace(
        validation_for=lambda _: {
            "claims": [{"text": text, "evidence": [{"label": "C1", "quote": original}]}]
        }
    )
    result = assess_model_answer(payload, SimpleNamespace(operations=(), warnings=()), p)
    assert result["claims"][0]["type"] == "PROJECTION_OR_POTENTIAL"
