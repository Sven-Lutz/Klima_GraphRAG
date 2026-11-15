"""Small technical fixtures; no real judgments, corpus copies or network."""

import json

import pytest

from research.question_evaluation.runner import load_checkpoints, output_bytes


def test_checkpoint_resume_preserves_unknown_and_ignores_uncommitted_pending(tmp_path):
    row = {
        "question_id": "q1",
        "method": "lexical",
        "catalog_sha256": "sha",
        "correctness": "UNASSESSED_NO_HUMAN_QRELS",
    }
    (tmp_path / "q1--lexical.json").write_text(json.dumps(row))
    (tmp_path / "unfinished.pending").write_text("{")
    assert load_checkpoints(tmp_path, "sha", {("q1", "lexical")}) == {("q1", "lexical"): row}
    with pytest.raises(ValueError, match="catalog"):
        load_checkpoints(tmp_path, "other", {("q1", "lexical")})
    with pytest.raises(ValueError, match="Unknown"):
        load_checkpoints(tmp_path, "sha", {("foreign", "lexical")})


def test_misnamed_checkpoint_is_not_silently_accepted(tmp_path):
    (tmp_path / "foreign.json").write_text(
        json.dumps({"question_id": "q", "method": "graph", "catalog_sha256": "sha"})
    )
    with pytest.raises(ValueError, match="misnamed"):
        load_checkpoints(tmp_path, "sha", {("q", "graph")})


def test_output_budget_counts_nested_checkpoint_files(tmp_path):
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "results.jsonl").write_bytes(b"123")
    (tmp_path / "checkpoints" / "q.json").write_bytes(b"45678")
    assert output_bytes(tmp_path) == 8


def test_versioned_catalog_is_unique_development_only_and_has_no_gold():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "docs/product/graph-release/CATALOG.json").read_text())
    assert len(catalog) == 600
    assert len({q["id"] for q in catalog}) == 600
    assert len({q["question"] for q in catalog}) == 600
    assert {q["split"] for q in catalog} == {"development_diagnostic"}
    assert all(q["reference_answer"] is None and not q["confirmation_eligible"] for q in catalog)
    for q in catalog:
        assert q["origin"]["source_sha256"]
        if q["category"] == "graph_paths":
            assert len(q["origin"]["relation_ids"]) == 2
            assert q["origin"]["source_sha256"] in q["origin"]["source_sha256s"]
            assert q["origin"]["unit_id"] in q["origin"]["unit_ids"]


def test_case_reuse_keeps_original_receipt_and_rejects_backend_drift(tmp_path):
    from research.question_evaluation.summarize import aggregate

    case = {"id": "q", "category": "source_scope", "allowed_methods": ["lexical"]}
    old_catalog = tmp_path / "old.json"
    current_catalog = tmp_path / "current.json"
    old_catalog.write_text(json.dumps([case]))
    current_catalog.write_text(json.dumps([case]))
    previous = tmp_path / "previous"
    corrected = tmp_path / "corrected"
    for directory, sha in [(previous, "old-sha"), (corrected, "new-sha")]:
        (directory / "checkpoints").mkdir(parents=True)
        (directory / "BINDING.json").write_text(
            json.dumps(
                {
                    "implementations": {"backend": "same"},
                    "snapshot_id": "fixture",
                    "catalog_sha256": sha,
                }
            )
        )
    row = {
        "question_id": "q",
        "method": "lexical",
        "catalog_sha256": "old-sha",
        "http_status": 200,
        "evidence_count": 0,
        "latency_seconds": 0.1,
        "maxrss_bytes_macos": 123,
        "correctness": "UNASSESSED_NO_HUMAN_QRELS",
    }
    (previous / "checkpoints/q--lexical.json").write_text(json.dumps(row))
    output = tmp_path / "summary"
    aggregate(old_catalog, current_catalog, previous, corrected, output)
    saved = json.loads((output / "RESULTS.json").read_text())
    assert saved[0]["original_receipt"] == row
    assert saved[0]["mapping"] == "IDENTICAL_FULL_CASE"
    binding = json.loads((corrected / "BINDING.json").read_text())
    binding["implementations"]["backend"] = "different"
    (corrected / "BINDING.json").write_text(json.dumps(binding))
    with pytest.raises(AssertionError):
        aggregate(old_catalog, current_catalog, previous, corrected, output)
