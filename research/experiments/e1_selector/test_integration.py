"""Real frozen A-T3 integration and boundary regressions; no network."""
import ast
import csv
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sqlite3

import pytest

from crawler.core.bounded_passages import (
    PassageBoundaryError, SQLiteSnapshot, select_bounded_passages,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).with_name("fixtures")
HASHES = {
    "nuernberg-heuristic": "b2357a9dc624037fa8c275caf756e9356b925062757db0093149f95816ebfe40",
    "nuernberg-structured": "ae4b29f2f7b1213a51417c8399d0fb1bb9a0aa3bc569b747171db65957b72921",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("E1 tests prohibit networking")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def request(**changes):
    args = dict(enabled=True, corpus=ROOT / "snapshots/e1_selector_v04/corpus.jsonl",
        fixtures=FIXTURES, snapshots={name: SQLiteSnapshot(ROOT / f"generated/ds007/{name}/pilot.sqlite", sha)
                                    for name, sha in HASHES.items()},
        task_id="A-T3", acquisition_ags="09564000", budget_chars=8000)
    args.update(changes)
    return args


def test_disabled_default_does_no_io():
    args = request(corpus=Path("missing"), fixtures=Path("missing"), snapshots={})
    del args["enabled"]
    assert select_bounded_passages(**args)["status"] == "disabled"


def test_real_n_ergie_found_without_first_stage_oracle_leakage():
    result = select_bounded_passages(**request())
    # Anchor is read ONLY after selection, never passed to the adapter.
    anchors = list(csv.DictReader((FIXTURES / "anchors_dev.csv").open()))
    anchor = next(a for a in anchors if a["anchor_id"] == "AH-NETWORK")
    top = next(json.loads(l) for l in (FIXTURES / "L0_union.jsonl").read_text().splitlines()
               if json.loads(l)["task_id"] == "A-T3")["results"]
    assert not set(anchor["unit_ids"].split("|")) & {r["unit_id"] for r in top}
    found = [r for r in result["items"] if anchor["quote"] in r["evidence_text"]]
    assert found and found[0]["rank"] == 7
    assert found[0]["page_ref"] == "32"
    assert result["context_chars"] == len(result["context"]) <= 8000
    assert result["scanned_chars"] <= 2_000_000
    assert result["scanned_units"] <= 1779


def test_exact_text_versions_offsets_and_evidence_interface():
    result = select_bounded_passages(**request())
    corpus = {u["unit_id"]: u for u in map(json.loads, request()["corpus"].read_text().splitlines())}
    # Structural integration with the existing mapping boundary, without importing
    # the unrelated numpy/Neo4j retrieval runtime.
    tree = ast.parse((ROOT / "crawler/core/retrieval.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RetrievedEvidence")
    fields = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
    for item in result["items"]:
        assert fields <= item.keys()
        parent = corpus[item["parent_unit_id"]]
        assert item["evidence_text"] == parent["text"][item["start_char"]:item["end_char"]]
        assert item["document"]["source_url"] == parent["source_url"]
        assert item["acquisition_ags"] == parent["acquisition_ags"]
        assert {(item["acquisition_ags"], parent["source_url"], h) for h in item["raw_hashes"]} <= set(result["authorized_versions"])
        assert {o["raw_hash"] for o in item["origins"]} == set(item["raw_hashes"])
        assert item["source_id"] == item["chunk_id"]
        assert item["claim_supported"] == "NOT_ASSESSED"
        assert item["attribution_status"] == "unknown"
        assert item["territorial_scope"] == [] and item["assertions"] == []


@pytest.mark.parametrize("budget", [0, -1, 16001, True, 8.5, float("inf")])
def test_invalid_budgets_rejected(budget):
    with pytest.raises(PassageBoundaryError, match="budget"):
        select_bounded_passages(**request(budget_chars=budget))


def test_tiny_budget_no_context_or_document_fallback():
    result = select_bounded_passages(**request(budget_chars=1))
    assert result["items"] == [] and result["context"] == ""


def test_foreign_acquisition_ags_rejected():
    with pytest.raises(PassageBoundaryError, match="AGS"):
        select_bounded_passages(**request(acquisition_ags="09190118"))


@pytest.mark.parametrize("change", ["body", "ags"])
def test_unauthorized_source_versions_rejected(change):
    original = select_bounded_passages(**request())
    versions = list(original["authorized_versions"])
    ags, url, body = versions[0]
    versions[0] = ("09190118" if change == "ags" else ags, url, "f" * 64 if change == "body" else body)
    with pytest.raises(PassageBoundaryError, match="versions"):
        select_bounded_passages(**request(authorized_versions=versions))


def test_frozen_ranking_tampering_rejected(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    path = tmp_path / "fixtures/L0_union.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(PassageBoundaryError, match="hash"):
        select_bounded_passages(**request(fixtures=path.parent))


def test_missing_binding_never_creates_fallback_database():
    with pytest.raises(PassageBoundaryError, match="binding"):
        select_bounded_passages(**request(snapshots={}))


@pytest.mark.parametrize("field,value,reason", [
    ("raw_hash", "f" * 64, "body-version"),
    ("segment_id", "absent", "segment provenance"),
    ("municipality_id", "09190118", "AGS/URL"),
])
def test_invalid_sqlite_provenance_even_with_explicit_new_snapshot_hash(tmp_path, field, value, reason):
    args = request()
    run = "nuernberg-heuristic"
    copy = tmp_path / "tampered.sqlite"
    shutil.copyfile(args["snapshots"][run].path, copy)
    with sqlite3.connect(copy) as con:
        # All rows are modified to ensure the fixed candidate pool encounters it.
        table = "documents_raw" if field == "raw_hash" else "chunks"
        con.execute(f"UPDATE {table} SET {field}=?", (value,))
    args["snapshots"][run] = SQLiteSnapshot(copy, hashlib.sha256(copy.read_bytes()).hexdigest())
    with pytest.raises(PassageBoundaryError, match=reason):
        select_bounded_passages(**args)


def test_snapshot_drift_rejected(tmp_path):
    args = request()
    run = "nuernberg-heuristic"
    copy = tmp_path / "drift.sqlite"
    shutil.copyfile(args["snapshots"][run].path, copy)
    with copy.open("ab") as stream:
        stream.write(b"drift")
    args["snapshots"][run] = SQLiteSnapshot(copy, HASHES[run])
    with pytest.raises(PassageBoundaryError, match="hash"):
        select_bounded_passages(**args)
