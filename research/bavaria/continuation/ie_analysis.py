"""Cached-output diagnostics, conservative ablations and clustered uncertainty.

This is explicitly post-hoc exploratory analysis of provisional labels, not
independent gold or a confirmatory hypothesis test. No inference is performed.
"""

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .ie_compare import FIELDS, empty, normalize, structurally_supported, summary


def analyze(sample, comparison, output):
    sample, comparison, output = Path(sample), Path(comparison), Path(output)
    if output.exists():
        raise ValueError("Use a fresh analysis directory")
    output.mkdir(parents=True)
    cases = {
        c["case_id"]: c for c in map(json.loads, (sample / "cases.jsonl").read_text().splitlines())
    }
    rows = list(map(json.loads, (comparison / "results.jsonl").read_text().splitlines()))
    lookup = {(r["method"], r["case_id"]): r for r in rows}
    (output / "ANALYSIS_PROTOCOL.json").write_text(
        json.dumps(
            {
                "status": "POSTHOC_EXPLORATORY_NO_MODEL_CALLS",
                "seed": 42,
                "resamples": 2000,
                "cluster": "frozen source/municipality connected components",
                "reference": "PROVISIONAL_AGENT_NOT_HUMAN_GOLD",
                "estimand": "difference in micro field F1 conditional on this requested-field inventory",  # noqa: E501 — frozen text/regex literal
                "unknown_institution_project_dependence": "not eliminated; intervals not population-valid",  # noqa: E501 — frozen text/regex literal
                "ablation": "quote-only validation; full validation without rule fill; validated model preferred",  # noqa: E501 — frozen text/regex literal
                "sample_sha256": hashlib.sha256((sample / "cases.jsonl").read_bytes()).hexdigest(),
                "results_sha256": hashlib.sha256(
                    (comparison / "results.jsonl").read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
    )
    diagnostics, variants = [], []
    for identifier, case in cases.items():
        model, rules = lookup["model", identifier]["output"], lookup["rules", identifier]["output"]
        context = (case.get("heading") or "") + "\n" + case["text"]
        for variant in (
            "quote_only_then_rules",
            "validated_model_only",
            "validated_model_preferred",
        ):
            predicted = empty()
            for field in FIELDS:
                quote = model.get("evidence", {}).get(field)
                supported = structurally_supported(field, model, context)
                accepted = bool(model[field] is not None and quote and quote in context)
                if variant != "quote_only_then_rules":
                    accepted = accepted and supported
                if accepted:
                    predicted[field] = model[field]
                    predicted["evidence"][field] = quote
                elif variant != "validated_model_only" and structurally_supported(
                    field, rules, context
                ):
                    predicted[field] = rules[field]
                    if field in rules["evidence"]:
                        predicted["evidence"][field] = rules["evidence"][field]
            variants.append({**lookup["model", identifier], "method": variant, "output": predicted})
        for method in ("rules", "model", "hybrid"):
            record = lookup[method, identifier]
            for field in FIELDS:
                expected, actual = record["reference"][field], record["output"][field]
                if normalize(field, expected) == normalize(field, actual):
                    continue
                category = (
                    "MISSED_EXPECTED_FIELD"
                    if actual is None
                    else "OUTPUT_WHERE_REFERENCE_UNKNOWN"
                    if expected is None
                    else "WRONG_REQUESTED_FIELD_OR_NORMALIZATION"
                )
                diagnostics.append(
                    {
                        "case_id": identifier,
                        "method": method,
                        "field": field,
                        "category": category,
                        "reference": expected,
                        "actual": actual,
                        "quote": record["output"]["evidence"].get(field),
                        "structural_support": record["structurally_grounded"][field],
                        "source_sha256": case["source_sha256"],
                        "unit_id": case["unit_id"],
                        "source_url": case["source_url"],
                        "warning": "Diagnostic vs provisional target; not every mismatch is hallucination.",  # noqa: E501 — frozen text/regex literal
                    }
                )
    extended = rows + variants
    report = {
        "primary": {
            m: summary([r for r in rows if r["method"] == m]) for m in ("rules", "model", "hybrid")
        },
        "posthoc_ablations": {
            m: summary([r for r in variants if r["method"] == m])
            for m in sorted({r["method"] for r in variants})
        },
        "error_counts": {
            m: dict(Counter(r["category"] for r in diagnostics if r["method"] == m))
            for m in ("rules", "model", "hybrid")
        },
        "structural_unsupported_nonnull_fields": {
            m: sum(
                r["output"][f] is not None and not r["structurally_grounded"][f]
                for r in rows
                if r["method"] == m
                for f in FIELDS
            )
            for m in ("rules", "model", "hybrid")
        },
        "basis": "strict requested-field comparison, provisional source-exposed labels",
        "independent_human_judgments": 0,
    }
    eval_rows = [r for r in rows if r["split"] == "evaluation"]
    clusters = sorted({r["component"] for r in eval_rows})
    grouped = {
        (method, cluster): [
            r for r in eval_rows if r["method"] == method and r["component"] == cluster
        ]
        for method in ("rules", "model", "hybrid")
        for cluster in clusters
    }
    rng, differences = random.Random(42), defaultdict(list)
    for _ in range(2000):
        selected = rng.choices(clusters, k=len(clusters))
        scores = {
            method: summary([r for component in selected for r in grouped[method, component]])[
                "micro"
            ]["f1"]
            for method in ("rules", "model", "hybrid")
        }
        for method in ("model", "hybrid"):
            differences[method + "_minus_rules"].append(scores[method] - scores["rules"])
    report["conditional_component_bootstrap"] = {
        "components": len(clusters),
        "resamples": 2000,
        "seed": 42,
        "percentile_95_intervals": {
            key: [sorted(values)[49], sorted(values)[1949]] for key, values in differences.items()
        },
        "interpretation": "Exploratory conditional variability only; no human-gold or population inference.",  # noqa: E501 — frozen text/regex literal
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "errors.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in diagnostics)
    )
    (output / "ablation-results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in variants)
    )
    with (output / "field_metrics.csv").open("w") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "method",
                "field",
                "tp",
                "fp",
                "fn",
                "precision",
                "recall",
                "f1",
                "unknown_slots",
                "correct_unknown",
            ]
        )
        for method in sorted({r["method"] for r in extended}):
            values = summary([r for r in extended if r["method"] == method])
            for field, metrics in values["fields"].items():
                writer.writerow(
                    [method, field]
                    + [
                        metrics.get(k, 0)
                        for k in (
                            "tp",
                            "fp",
                            "fn",
                            "precision",
                            "recall",
                            "f1",
                            "unknown_slots",
                            "correct_unknown",
                        )
                    ]
                )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(
        json.dumps(analyze(args.sample, args.comparison, args.output), ensure_ascii=False, indent=2)
    )
