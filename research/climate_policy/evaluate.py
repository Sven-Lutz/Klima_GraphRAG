"""Paired exploratory comparisons with explicit finite, provisional denominators."""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.climate_policy import store
from research.climate_policy.answer import analyze
from research.offline_evaluation.contracts import require


def equal_value(a, b):
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def evaluate(benchmark, out):
    from research.experiments.m4a_parsing_admission.diagnose import guard

    sys.addaudithook(guard)
    require(not out.exists(), "Use a fresh result directory")
    tasks = json.loads(Path(benchmark).read_bytes())
    freeze_path = Path(benchmark).parent / "EVALUATION_FREEZE.json"
    freeze = json.loads(freeze_path.read_bytes())
    require(store.sha(benchmark) == freeze["tasks_sha256"], "Benchmark changed")
    for name, expected in freeze["implementation_sha256"].items():
        require(
            store.sha(Path("research/climate_policy") / name) == expected,
            "Comparison code changed: " + name,
        )
    require(
        store.sha(store.EXPORT / "corpus.jsonl") == tasks["corpus_sha256"]
        and store.sha(store.EXPORT / "catalog.jsonl") == tasks["catalog_sha256"],
        "Corpus/catalog changed",
    )
    out.mkdir(parents=True)
    build = store.build(out / "evidence.sqlite")
    with store.connect(out / "evidence.sqlite") as conn:
        unit_text = {
            r["unit_id"]: r["text"] for r in conn.execute("SELECT unit_id,text FROM evidence")
        }
    outputs = []
    metrics = []
    resources = []
    for i, task in enumerate(tasks["tasks"]):
        # Rotate method order; deterministic outcomes, timing order is not a model factor.
        methods = tasks["methods"][i % 4 :] + tasks["methods"][: i % 4]
        groups = defaultdict(list)
        for uid in task["known_reference_units"]:
            groups[store.ident(unit_text[uid])].append(uid)
        for method in methods:
            start = time.perf_counter()
            cpu = time.process_time()
            result = analyze(out / "evidence.sqlite", task["question"], method, 5, 6000)
            elapsed = time.perf_counter() - start
            cpu_elapsed = time.process_time() - cpu
            cited = {c["unit_id"] for c in result["citations"]}
            claims = result["claims"]
            matched = []
            for number, slot in enumerate(task["slots"]):
                found = any(
                    c["unit_id"] in slot["reference_units"]
                    and c["field"] == slot["field"]
                    and equal_value(c["value"], slot["value"])
                    and c["unit"] == slot["unit"]
                    and c["role"] == slot["role"]
                    for c in claims
                )
                if found:
                    matched.append(number)
            guard = task["guard"]
            guard_pass = None
            if guard == "no_positive_typed_claim":
                guard_pass = not claims
            elif guard == "current_status_warning":
                guard_pass = any("Realweltstatus" in w for w in result["warnings"])
            elif guard == "measurement_warning":
                guard_pass = any("keine ausdrücklich gemessene" in w for w in result["warnings"])
            resolved = True
            for cite in result["citations"]:
                resolved &= (
                    unit_text[cite["unit_id"]][cite["start_char"] : cite["end_char"]]
                    == cite["quote"]
                )
            for c in claims:
                resolved &= unit_text[c["unit_id"]][c["start_char"] : c["end_char"]] == c["quote"]
            metrics.append(
                {
                    "task_id": task["id"],
                    "family": task["family"],
                    "method": method,
                    "known_anchor_groups": len(groups),
                    "known_anchor_groups_captured": sum(
                        bool(set(v) & cited) for v in groups.values()
                    ),
                    "known_bundle_complete": all(set(v) & cited for v in groups.values()),
                    "expected_slots": len(task["slots"]),
                    "matched_slots": len(matched),
                    "matched_slot_indices": matched,
                    "structural_citations_resolve": bool(resolved),
                    "guard_expected": guard,
                    "guard_pass": guard_pass,
                    "context_chars": result["retrieval"]["context_chars"],
                    "returned_evidence": len(result["citations"]),
                    "returned_claims": len(claims),
                    "candidate_units": result["retrieval"]["candidate_units"],
                    "access_scope_sha256": result["retrieval"]["access_scope_sha256"],
                    "status": result["status"],
                }
            )
            outputs.append({"task_id": task["id"], "response": result})
            resources.append(
                {
                    "task_id": task["id"],
                    "method": method,
                    "wall_seconds": elapsed,
                    "cpu_seconds": cpu_elapsed,
                    "direct_model_calls": 0,
                    "direct_api_cost_eur": 0,
                    "tool_operations": result["retrieval"]["operations"],
                }
            )
    metrics.sort(key=lambda r: (r["task_id"], r["method"]))
    outputs.sort(key=lambda r: (r["task_id"], r["response"]["method"]))
    aggregates = {}
    for method in tasks["methods"]:
        rows = [m for m in metrics if m["method"] == method]
        aggregates[method] = {
            "tasks": len(rows),
            "anchor_groups_captured": sum(r["known_anchor_groups_captured"] for r in rows),
            "anchor_groups_denominator": sum(r["known_anchor_groups"] for r in rows),
            "complete_known_bundles": sum(r["known_bundle_complete"] for r in rows),
            "field_slots_matched": sum(r["matched_slots"] for r in rows),
            "field_slots_denominator": sum(r["expected_slots"] for r in rows),
            "all_structural_citations_resolve": all(
                r["structural_citations_resolve"] for r in rows
            ),
            "predefined_guards_passed": sum(r["guard_pass"] is True for r in rows),
            "guard_denominator": sum(r["guard_expected"] is not None for r in rows),
            "wall_seconds": sum(r["wall_seconds"] for r in resources if r["method"] == method),
            "per_family": {
                f: {
                    "tasks": sum(r["family"] == f for r in rows),
                    "captured": sum(
                        r["known_anchor_groups_captured"] for r in rows if r["family"] == f
                    ),
                    "anchor_denominator": sum(
                        r["known_anchor_groups"] for r in rows if r["family"] == f
                    ),
                    "slots_matched": sum(r["matched_slots"] for r in rows if r["family"] == f),
                    "slot_denominator": sum(r["expected_slots"] for r in rows if r["family"] == f),
                }
                for f in sorted({r["family"] for r in rows})
            },
        }

    def write(name, value):
        (out / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )

    write("outputs.json", outputs)
    write("metrics.json", metrics)
    write("aggregate.json", aggregates)
    write(
        "resources.json",
        {
            "build": build,
            "queries": resources,
            "database_bytes": (out / "evidence.sqlite").stat().st_size,
            "direct_paid_calls": 0,
            "direct_api_cost_eur": 0,
            "assistant_internal_usage": "not observable",
        },
    )
    write(
        "run_manifest.json",
        {
            "benchmark_sha256": store.sha(benchmark),
            "freeze_sha256": store.sha(freeze_path),
            "code_sha256": freeze["implementation_sha256"],
            "outputs_sha256": store.sha(out / "outputs.json"),
            "metrics_sha256": store.sha(out / "metrics.json"),
            "independent_gold": False,
            "status": "EXPLORATORY_CONDITIONAL_ON_PROVISIONAL_LABELS",
            "calls": len(outputs),
        },
    )
    with (out / "metrics.csv").open("w", newline="") as f:
        fields = [k for k in metrics[0] if k != "matched_slot_indices"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(metrics)
    print(
        json.dumps(
            {m: {k: v for k, v in a.items() if k != "per_family"} for m, a in aggregates.items()}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.benchmark, args.out)
