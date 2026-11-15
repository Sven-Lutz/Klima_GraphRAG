"""Stored receipts must fail closed when identities or completeness are altered."""

import json
import shutil
from pathlib import Path

import pytest

from scripts.qa.research_receipt_audit import audit

ROOT = Path(__file__).resolve().parents[1] / "docs/product/graph-release"


def test_frozen_receipts_are_complete_and_unlabeled():
    result = audit(ROOT)
    assert result["method_pairs"] == 1320
    assert result["counts"]["http_422"] == 60
    assert result["development_nonempty"] == {"lexical": 3, "lexical_expanded": 9}
    assert result["queries_executed"] == 0


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "foreign_snapshot", "false_reuse"])
def test_reject_corrupted_receipt_contract(tmp_path, mutation):
    for name in (
        "FREEZE.json",
        "CATALOG.json",
        "CATALOG-v3.json",
        "RESULTS.json",
        "CATALOG_SCOPE_CORRECTION.json",
        "DEVELOPMENT_PAIRED.json",
    ):
        shutil.copyfile(ROOT / name, tmp_path / name)
    path = tmp_path / "RESULTS.json"
    rows = json.loads(path.read_text())
    if mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "missing":
        rows.pop()
    elif mutation == "foreign_snapshot":
        rows[0]["original_receipt"]["snapshot_id"] = "foreign"
    else:
        rows[0]["mapping"] = "CORRECTED_CASE_RERUN"
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError):
        audit(tmp_path)
