"""Offline regression against saved real sources plus labeled counterexamples."""
import csv
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from crawler.core.attribution import EvidenceText, attribute
from research.experiments.ds008.evaluate import evaluate, metrics, NAMES

TARGET = dict(ags="09190118", name="Burggen")
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DS008 tests must not access network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def synthetic(text, kind="body", primary=False):
    return EvidenceText(text, kind, {"source_id": "synthetic:counterexample", "line": 1}, primary)


@pytest.mark.parametrize("text,expected", [
    ("Geltungsbereich: Burggen.", "verified"),
    ("Geltungsbereich: Bernbeuren.", "foreign_scope"),
    ("Geltungsbereich: Burggen und Bernbeuren.", "verified"),
    ("Gemeinsame Verwaltung Burggen und Bernbeuren.", "unknown"),
    ("Herausgeber: Gemeinde Bernbeuren.", "unknown"),
    ("Zuständige Institution: Betreiber GmbH.", "unknown"),
    ("Nürnberg berichtet über einen Betrieb in Burggen.", "unknown"),
    ("Kein Geltungsbereich: Burggen.", "unknown"),
    ("Gilt nicht für Burggen.", "unknown"),
    ("Geltungsbereich: Burggen.\nGeltungsbereich: Bernbeuren.", "unknown"),
    ("Geltungsbereich: Burggen.\nGilt nicht für Burggen.", "unknown"),
])
def test_synthetic_explicit_scope_only(text, expected):
    result = attribute(TARGET, [synthetic(text)], municipality_names=NAMES)
    assert result["attribution_status"] == expected
    if expected != "unknown":
        assert result["evidence_locator"]
        assert all(c["quote"] in text for c in result["evidence_locator"])


def test_publisher_actor_and_joint_scope_stay_separate():
    text = ("Herausgeber: Gemeinde Bernbeuren.\n"
            "Zuständige Institution: Gemeinsamer Zweckverband.\n"
            "Geltungsbereich: Burggen und Bernbeuren.")
    result = attribute(TARGET, [synthetic(text)], municipality_names=NAMES)
    assert result["attribution_status"] == "verified"
    assert result["publisher"] == ["Gemeinde Bernbeuren"]
    assert result["source_municipality"] == ["Bernbeuren"]
    assert result["responsible_actor"][0]["name"] == "Gemeinsamer Zweckverband"
    assert result["territorial_scope"] == ["Bernbeuren", "Burggen"]


def test_title_scope_is_never_inherited_by_private_claim():
    title = synthetic("Finanzausschuss - Burggen", "heading", True)
    assert attribute(TARGET, [title], municipality_names=NAMES)["attribution_status"] == "verified"
    assert attribute(TARGET, [title], municipality_names=NAMES, unit="claim")["attribution_status"] == "unknown"


def test_incidental_secondary_heading_is_not_document_scope():
    mention = synthetic("Finanzausschuss - Bernbeuren", "heading", False)
    assert attribute(TARGET, [mention], municipality_names=NAMES)["attribution_status"] == "unknown"


def test_no_locator_no_attribution():
    with pytest.raises(ValueError, match="source_id"):
        attribute(TARGET, [EvidenceText("Geltungsbereich: Burggen.", "body", {})], municipality_names=NAMES)


def test_acquisition_metadata_alone_never_verifies():
    assert attribute(TARGET, [], municipality_names=NAMES)["attribution_status"] == "unknown"


@pytest.fixture(scope="module")
def real_rows():
    # Absence is a failed reproducibility prerequisite, never a silent skip.
    return evaluate()


def test_real_eight_foreign_document_entries(real_rows):
    foreign = [r for r in real_rows if r["attribution_status"] == "foreign_scope"]
    assert len(foreign) == 8
    assert all(r["arm"] == "burggen-heuristic" for r in foreign)
    assert all(r["territorial_scope"] == ["Bernbeuren"] for r in foreign)
    assert all(r["acquisition_municipality"]["ags"] == "09190118" for r in foreign)
    assert all(any(c["quote"].endswith(" - Bernbeuren") for c in r["evidence_locator"]) for r in foreign)


def test_real_burggen_shared_ris(real_rows):
    rows = [r for r in real_rows if "ris.kommune-aktiv.de" in r["url"]
            and r["attribution_status"] == "verified"]
    assert len(rows) == 2
    assert all(r["territorial_scope"] == ["Burggen"] for r in rows)
    assert all(r["publisher"] == [] for r in rows)  # hosting != publisher


def test_real_shared_ris_unclear_scope(real_rows):
    rows = [r for r in real_rows if r["url"].endswith("/Gremien.html")
            or r["url"].endswith("/Ratsinformationssystem.html")]
    assert len(rows) == 4
    assert all(r["attribution_status"] == "unknown" for r in rows)


def test_real_nuernberg_explicit_climate_plan(real_rows):
    rows = [r for r in real_rows if r["attribution_status"] == "verified"
            and r["acquisition_municipality"]["name"] == "Nürnberg"]
    assert len(rows) == 4
    assert all(r["territorial_scope"] == ["Nürnberg"] for r in rows)
    assert all(r["responsible_actor"] == [] for r in rows)


def test_real_private_operator_abstains(real_rows):
    row = next(r for r in real_rows if r["attribution_unit"] == "claim")
    assert row["attribution_status"] == "unknown"
    assert row["responsible_actor"] == []  # profession is not institution/mandate
    assert row["territorial_scope"] == []
    assert "Nahwäremenetz" in row["inspection_locator"][-1]["quote"]


def test_real_citations_resolve_to_unchanged_source_text(real_rows):
    for row in real_rows:
        for citation in row["evidence_locator"] + row.get("inspection_locator", []):
            with sqlite3.connect(f"file:{ROOT / citation['database']}?mode=ro", uri=True) as con:
                text = con.execute("SELECT text FROM segments WHERE document_id=? AND order_index=?",
                                   (citation["document_id"], citation["order_index"])).fetchone()[0]
            assert citation["quote"] in text
            assert hashlib.sha256(text.encode()).hexdigest() == citation["segment_text_sha256"]
            for candidate in citation["raw_response_candidates"]:
                assert hashlib.sha256((ROOT / candidate["body_path"]).read_bytes()).hexdigest() == candidate["sha256"]


def test_real_counts_and_export_are_reproducible(real_rows):
    summary = metrics(real_rows)
    assert summary["document_entries"] == 62
    assert summary["statuses"] == {"unknown": 48, "verified": 6, "foreign_scope": 8}
    table = Path(__file__).with_name("attribution.csv")
    with table.open() as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == len(real_rows) == 63
    assert {r["row_id"]: r["attribution_status"] for r in exported} == {
        r["row_id"]: r["attribution_status"] for r in real_rows}
    assert all(json.loads(r["acquisition_municipality"])["ags"] for r in exported)
