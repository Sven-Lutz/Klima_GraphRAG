"""Verified reuse of unchanged cases; preserve each original technical receipt verbatim."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def aggregate(old_catalog, catalog, previous, corrected, output):
    old = {q["id"]: q for q in json.loads(old_catalog.read_text())}
    current = {q["id"]: q for q in json.loads(catalog.read_text())}
    same = {key for key in current if old.get(key) == current[key]}
    previous_binding = json.loads((previous / "BINDING.json").read_text())
    corrected_binding = json.loads((corrected / "BINDING.json").read_text())
    assert previous_binding["implementations"] == corrected_binding["implementations"]
    assert previous_binding["snapshot_id"] == corrected_binding["snapshot_id"]
    rows = []
    for key, q in sorted(current.items()):
        origin = previous if key in same else corrected
        for method in q["allowed_methods"]:
            row = json.loads((origin / "checkpoints" / (key + "--" + method + ".json")).read_text())
            expected_hash = (
                previous_binding["catalog_sha256"]
                if key in same
                else corrected_binding["catalog_sha256"]
            )
            assert row["catalog_sha256"] == expected_hash
            rows.append(
                {
                    "case_id": key,
                    "category": q["category"],
                    "source_run": str(origin),
                    "mapping": "IDENTICAL_FULL_CASE" if key in same else "CORRECTED_CASE_RERUN",
                    "original_receipt": row,
                }
            )
    output.mkdir(parents=True, exist_ok=True)
    (output / "RESULTS.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["category"], row["original_receipt"]["method"])].append(
            row["original_receipt"]
        )
    summary = []
    for (category, method), rs in sorted(grouped.items()):
        latency = sorted(r["latency_seconds"] for r in rs)
        summary.append(
            {
                "category": category,
                "method": method,
                "runs": len(rs),
                "http200": sum(r["http_status"] == 200 for r in rs),
                "nonempty": sum(r["evidence_count"] > 0 for r in rs),
                "unexpected_errors": sum(bool(r.get("technical_error")) for r in rs),
                "median_seconds": latency[len(latency) // 2],
                "maxrss_bytes": max(r["maxrss_bytes_macos"] for r in rs),
                "correctness": "UNASSESSED",
            }
        )
    report = {
        "cases": len(current),
        "runs": len(rows),
        "unchanged_cases_reused": len(same),
        "corrected_cases_rerun": len(current) - len(same),
        "current_catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "snapshot_id": previous_binding["snapshot_id"],
        "by_category_method": summary,
        "model_calls": 0,
        "human_gold": False,
        "confirmation_executed": 0,
    }
    (output / "TECHNICAL_SUMMARY.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "by_category_method"}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ["old-catalog", "catalog", "previous", "corrected", "output"]:
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    aggregate(a.old_catalog, a.catalog, a.previous, a.corrected, a.output)
