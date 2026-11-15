import csv
from dataclasses import replace
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from research.experiments.ds009.adapter import ROOT, evaluate_claim, sha
from research.experiments.ds009.evaluate import cases, synthetic, summary


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Network prohibited")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


@pytest.fixture
def rows():
    return {r["claim_id"]: r for r in cases()}


def test_real_eight_foreign_claims_cannot_be_answers(rows):
    rejected = [r for r in rows.values() if r["evidence_verdict"] == "rejected_foreign_scope"]
    assert len(rejected) == 8
    for r in rejected:
        assert r["territorial_scope"] == ["Bernbeuren"]
        assert r["claim"]["target_municipality"]["ags"] == "09190118"
        assert r["definitive_assertion"] is None
        assert r["retained_for_review"]


@pytest.mark.parametrize("identifier", [
    "real-burggen-mandate", "real-shared", "real-private",
    "real-climate-wrong-year", "real-climate-cutoff",
    "synthetic-territory-conflict", "synthetic-actor-conflict", "synthetic-publisher-only",
])
def test_unresolved_claims_blocked(rows, identifier):
    row = rows[identifier]
    assert row["attribution_status"] == "unknown"
    assert row["definitive_assertion"] is None
    assert row["evidence_locator"] and row["retained_for_review"]


def test_document_verification_not_inherited(rows):
    for key in ("real-burggen-mandate", "real-climate-wrong-year", "real-climate-cutoff"):
        assert rows[key]["document_attribution_status"] == "verified"
        assert rows[key]["attribution_status"] == "unknown"


def test_real_claims_have_bounded_support(rows):
    body, climate = rows["real-burggen-body"], rows["real-climate"]
    assert body["attribution_status"] == climate["attribution_status"] == "verified"
    assert "keine" not in body["definitive_assertion"]
    assert "2040" in climate["definitive_assertion"]
    assert climate["temporal_scope"][0]["decision_year"] == "2025"
    assert climate["publisher"] == []
    assert climate["responsible_actor"][0]["role"] == "reported_decision_body"
    assert climate["guard_trace"]["attribution_unit"] == "claim"


def test_shared_territory_is_allowed(rows):
    row = rows["synthetic-shared"]
    assert row["attribution_status"] == "verified"
    assert row["territorial_scope"] == ["Bernbeuren", "Burggen"]


def test_exact_locators_and_hashes(rows):
    for row in rows.values():
        for loc in row["evidence_locator"]:
            if loc["data_class"] != "real_frozen_ds007":
                continue
            with sqlite3.connect(f"file:{ROOT / loc['database']}?mode=ro", uri=True) as con:
                text = con.execute("SELECT text FROM segments WHERE segment_id=?", (loc["segment_id"],)).fetchone()[0]
            assert sha(text.encode()) == loc["text_sha256"]
            assert text[loc["span_start"]:loc["span_end"]] == text
            assert loc["quote"] in text
            assert sha((ROOT / loc["database"]).read_bytes()) == loc["database_sha256"]


def test_changed_text_rejected_before_emission():
    p = synthetic("Geltungsbereich: Burggen.", "tamper")
    with pytest.raises(ValueError, match="changed"):
        evaluate_claim("tamper", "09190118", "municipal_body", {},
                       [replace(p, text="something else")], document_status="verified")


def test_evaluation_invariant_to_document_status():
    p = synthetic("Finanzausschuss - Burggen", "body")
    a = evaluate_claim("body", "09190118", "municipal_body", {"actor": "Finanzausschuss"},
                       [p], document_status="unknown")
    b = evaluate_claim("body", "09190118", "municipal_body", {"actor": "Finanzausschuss"},
                       [p], document_status="verified")
    assert a["attribution_status"] == b["attribution_status"] == "verified"


def test_empty_evidence_never_emits():
    r = evaluate_claim("empty", "09190118", "heat_operator", {}, [], document_status="verified")
    assert r["definitive_assertion"] is None
    assert r["attribution_status"] == "unknown"


def test_metrics_and_csv(rows):
    assert summary(list(rows.values())) == {
        "real_frozen_ds007": {"rejected_foreign_scope": 8, "supported": 2, "not_supported": 5},
        "synthetic_counterexample": {"supported": 1, "not_supported": 3}}
    with Path(__file__).with_name("claims.csv").open() as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 19
    for row in exported:
        expected = rows[row["claim_id"]]
        assert row["attribution_status"] == expected["attribution_status"]
        if row["attribution_status"] != "verified":
            assert json.loads(row["definitive_assertion"]) is None
