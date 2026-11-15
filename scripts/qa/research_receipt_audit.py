"""Audit frozen QA receipts without queries, models, or scientific labels."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def audit(root: Path) -> dict:
    def read(name):
        return json.loads((root / name).read_text())

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    freeze = read("FREEZE.json")
    catalog = read("CATALOG.json")
    previous = {c["id"]: c for c in read("CATALOG-v3.json")}
    correction = read("CATALOG_SCOPE_CORRECTION.json")
    corrected = set(correction["corrected_cases"])
    require(
        hashlib.sha256((root / "CATALOG.json").read_bytes()).hexdigest()
        == freeze["catalog_sha256"],
        "Catalog byte hash mismatch",
    )
    require(
        hashlib.sha256((root / "CATALOG-v3.json").read_bytes()).hexdigest()
        == correction["old_catalog_sha256"],
        "Previous catalog byte hash mismatch",
    )
    cases = {c["id"]: c for c in catalog}
    require(len(cases) == len(catalog) == freeze["cases"], "Duplicate/missing catalog cases")
    require(
        all(
            c["split"] == "development_diagnostic"
            and not c["confirmation_eligible"]
            and c["reference_answer"] is None
            for c in catalog
        ),
        "Non-development or labeled case",
    )
    require(
        {c["id"] for c in catalog if c != previous.get(c["id"])} == corrected,
        "Changed cases disagree with correction contract",
    )
    expected = {(c["id"], m) for c in catalog for m in c["allowed_methods"]}
    rows = read("RESULTS.json")
    seen = set()
    counts = Counter()
    for row in rows:
        r = row["original_receipt"]
        pair = (row["case_id"], r["method"])
        require(pair in expected and pair not in seen, f"Foreign/duplicate receipt: {pair}")
        seen.add(pair)
        c = cases[pair[0]]
        require(
            r["question_id"] == c["id"] and row["category"] == c["category"],
            "Case mapping mismatch",
        )
        changed = c["id"] in corrected
        require(
            row["mapping"] == ("CORRECTED_CASE_RERUN" if changed else "IDENTICAL_FULL_CASE"),
            "Invalid reuse mapping",
        )
        require(
            r["catalog_sha256"]
            == (freeze["catalog_sha256"] if changed else correction["old_catalog_sha256"]),
            "Receipt catalog identity mismatch",
        )
        require(
            r["snapshot_id"] == freeze["snapshot_id"]
            if r["http_status"] == 200
            else r["snapshot_id"] is None,
            "Snapshot identity mismatch",
        )
        require(
            r["http_status"] == r["expected_http"] == c["expected_http"]
            and not r["technical_error"],
            "Unexpected technical failure",
        )
        require(
            r["generation_status"] == "disabled"
            and r["answer_count"] == 0
            and r["answer_text"] is None,
            "Unexpected generated answer",
        )
        require(r["correctness"] == "UNASSESSED_NO_HUMAN_QRELS", "Unexpected correctness label")
        if r["http_status"] == 200 and r["method"] != "graph":
            require(r["citation_resolution"] is True, "Unresolved citation receipt")
            require(
                len(r["evidence_ids"])
                == len(r["source_ids"])
                == len(r["original_versions"])
                == r["evidence_count"],
                "Evidence cardinality mismatch",
            )
        counts[f"http_{r['http_status']}"] += 1
        counts[f"{c['category']}/{r['method']}/nonempty"] += r["evidence_count"] > 0
    require(seen == expected, "Incomplete method pairs")
    paired = read("DEVELOPMENT_PAIRED.json")
    require(len({r["task_id"] for r in paired}) == len(paired) == 21, "Development case identities")
    for r in paired:
        base = r["arms"]["lexical"]["evidence_ids"]
        expanded = r["arms"]["lexical_expanded"]["evidence_ids"]
        require(
            expanded[: len(base)] == base and r["baseline_preserved"], "Baseline prefix regression"
        )
        require(
            r["relevance"] == "UNASSESSED" and r["snapshot_id"] == freeze["snapshot_id"],
            "Development scientific identity mismatch",
        )
    return {
        "schema": "klima-research-receipt-audit-v1",
        "status": "PASS",
        "scope": "stored receipt consistency, not rerun or source/semantic verification",
        "cases": len(catalog),
        "method_pairs": len(rows),
        "counts": dict(sorted(counts.items())),
        "development_cases": len(paired),
        "development_nonempty": {
            m: sum(bool(r["arms"][m]["evidence_ids"]) for r in paired)
            for m in ("lexical", "lexical_expanded")
        },
        "source_versions": len(
            {v for r in rows for v in r["original_receipt"].get("original_versions", [])}
        ),
        "input_sha256": {
            n: hashlib.sha256((root / n).read_bytes()).hexdigest()
            for n in (
                "FREEZE.json",
                "CATALOG.json",
                "CATALOG-v3.json",
                "RESULTS.json",
                "CATALOG_SCOPE_CORRECTION.json",
                "DEVELOPMENT_PAIRED.json",
            )
        },
        "queries_executed": 0,
        "model_calls": 0,
        "human_gold_created": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, default=Path("docs/product/graph-release"))
    args = parser.parse_args()
    print(json.dumps(audit(args.receipts), ensure_ascii=False, indent=2))
