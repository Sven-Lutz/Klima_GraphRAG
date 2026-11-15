"""Read-only audit accounting/annotation overlay; never changes old inventories."""

import csv
import json
from collections import Counter
from pathlib import Path

from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def main():
    frozen = BASE / "freeze01"
    inventory = json.loads((frozen / "inventory.json").read_bytes())
    sample = json.loads((frozen / "sample_freeze.json").read_bytes())
    reviews = json.loads((BASE / "sample_reviews.json").read_bytes())["reviews"]
    groups = json.loads((frozen / "duplicates.json").read_bytes())["groups"]
    group_reviews = json.loads((BASE / "duplicate_group_reviews.json").read_bytes())["groups"]
    lookup = {r["inventory_id"]: r for r in reviews}
    d.require(
        set(lookup) == set(sample["selected_ids"]) and len(lookup) == 48, "Review/sample mismatch"
    )
    overlay = []
    for r in inventory["added"]:
        review = lookup.get(r["id"])
        overlay.append(
            {
                "id": r["id"],
                "whole_segment_status": "PROVISIONAL_AGENT_REVIEWED" if review else "UNASSESSED",
                "review_object": review["review_object"] if review else None,
                "relevance": review["relevance"] if review else None,
                "prior_control_spans_are_not_whole_segment_review": True,
            }
        )
    summary = {
        "status": "READ_ONLY_AUDIT_COMPLETE_REVIEW_PENDING",
        "additional_denominator": 338,
        "removed_denominator": 2,
        "reviewed_additional": 48,
        "unreviewed_additional": 290,
        "sample_counts": dict(Counter(r["relevance"] for r in reviews)),
        "original_fidelity_counts": dict(Counter(r["original_fidelity"] for r in reviews)),
        "context_axis_counts": dict(Counter(r["context_dependence"] for r in reviews)),
        "reviewed_characters_by_relevance": {
            label: sum(
                r["characters"]
                for r in inventory["added"]
                if r["id"] in lookup and lookup[r["id"]]["relevance"] == label
            )
            for label in sorted({x["relevance"] for x in reviews})
        },
        "inventory_contribution": {
            "added_characters": sum(r["characters"] for r in inventory["added"]),
            "removed_characters": sum(r["characters"] for r in inventory["removed"]),
            "added_cl100k_tokens": sum(r["token_count"] or 0 for r in inventory["added"]),
            "removed_cl100k_tokens": sum(r["token_count"] or 0 for r in inventory["removed"]),
            "token_status": inventory["token_metric"],
        },
        "inventory_origin_status_counts": dict(
            Counter(
                r["origin"]["origin_mapping_status"]
                for r in inventory["added"] + inventory["removed"]
            )
        ),
        "previous_no_control_span_whole_reviewed": sum(
            r["id"] in lookup and not r["prior_control_spans"] for r in inventory["added"]
        ),
        "duplicate_excess_by_review_category": {
            label: sum(g["excess"] for g in group_reviews if g["category"] == label)
            for label in sorted({x["category"] for x in group_reviews})
        },
        "duplicate_occurrences": 107,
        "duplicate_groups": 32,
        "duplicate_excess": 75,
        "within_source_excess": 33,
        "cross_source_excess": 42,
        "unassessed_not_extrapolated": True,
        "historical_data_changed": False,
        "sample_freeze_sha256": d.sha(frozen / "sample_freeze.json"),
    }
    # Explicitly enumerate all 75 accounting excesses. The first element is an accounting
    # representative only, not a semantic instruction to discard the rest.
    excess_rows = []
    for g in groups:
        representative = g["occurrence_ids"][0]
        for identity in g["occurrence_ids"][1:]:
            excess_rows.append(
                {
                    "group": g["id"],
                    "accounting_representative": representative,
                    "excess_occurrence": identity,
                    "safe_to_delete": False,
                    "group_category": next(
                        x["category"] for x in group_reviews if x["group_id"] == g["id"]
                    ),
                }
            )
    d.require(len(excess_rows) == 75, "Missing duplicate accounting occurrences")
    d.write(BASE / "inventory_review_overlay.json", overlay)
    d.write(BASE / "audit_summary.json", summary)
    d.write(BASE / "duplicate_excess_inventory.json", excess_rows)
    with (BASE / "inventory.csv").open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "change",
                "id",
                "raw_sha256",
                "source_url",
                "acquisition_ags",
                "page_ref",
                "dom_path",
                "characters",
                "tokens_cl100k",
                "admission_reason",
                "duplicate_group",
                "review_status",
            ]
        )
        for kind in ["added", "removed"]:
            for r in inventory[kind]:
                status = (
                    "PROVISIONAL_AGENT_REVIEWED"
                    if r["id"] in lookup
                    else "MANDATORY_TARGET_AUDIT"
                    if kind == "removed"
                    else "UNASSESSED"
                )
                w.writerow(
                    [
                        kind,
                        r["id"],
                        r["raw_sha256"],
                        r["source_url"],
                        r["acquisition_ags"],
                        r["page_ref"],
                        r["origin"].get("dom_path"),
                        r["characters"],
                        r["token_count"],
                        r["admission_reason"],
                        r["duplicate_group"],
                        status,
                    ]
                )
    per_source = []
    for raw in sorted({r["raw_sha256"] for r in inventory["added"]}):
        rows = [r for r in inventory["added"] if r["raw_sha256"] == raw]
        reviewed = [lookup[r["id"]] for r in rows if r["id"] in lookup]
        per_source.append(
            {
                "raw_sha256": raw,
                "url": rows[0]["source_url"],
                "added": len(rows),
                "sample_reviewed": len(reviewed),
                "sample_labels": dict(Counter(r["relevance"] for r in reviewed)),
                "not_whole_reviewed": len(rows) - len(reviewed),
            }
        )
    d.write(BASE / "per_source_summary.json", per_source)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
