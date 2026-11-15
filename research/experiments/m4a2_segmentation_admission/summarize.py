"""Post-result metadata analysis only; no new transformations or label changes."""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def key(source, segment):
    return (
        source["raw_sha256"],
        segment["text_sha256"],
        segment["heading_context_sha256"],
        segment["page_ref"],
    )


def main():
    a = (BASE / "run01/results.json").read_bytes()
    d.require(a == (BASE / "run02/results.json").read_bytes(), "Non-deterministic results")
    result = json.loads(a)
    controls = json.loads((BASE / "controls.json").read_bytes())["controls"]
    baseline = Counter(
        key(s, u)
        for s in result["outputs"]
        if s["arm"] == "B00"
        for u in s["segments"]
        if u["admitted"]
    )
    inventories, duplicates, transitions = {}, {}, []
    base_obs = {(o["group"], o["id"]): o for o in result["observations"] if o["arm"] == "B00"}
    for arm in ["B00", "B10", "B01", "B11"]:
        outputs = [s for s in result["outputs"] if s["arm"] == arm]
        current = Counter(key(s, u) for s in outputs for u in s["segments"] if u["admitted"])
        added, removed = current - baseline, baseline - current
        grouped = defaultdict(list)
        for s in outputs:
            for u in s["segments"]:
                if u["admitted"]:
                    grouped[u["text_sha256"]].append(
                        {
                            "raw_sha256": s["raw_sha256"],
                            "segment_index": u["segment_index"],
                            "page_ref": u["page_ref"],
                            "heading_context_sha256": u["heading_context_sha256"],
                        }
                    )
        duplicates[arm] = [
            {"text_sha256": h, "locations": v} for h, v in sorted(grouped.items()) if len(v) > 1
        ]
        by_label = defaultdict(set)
        for o in result["observations"]:
            if o["arm"] != arm:
                continue
            before = base_obs[o["group"], o["id"]]
            if before["status"] != o["status"]:
                transitions.append(
                    {
                        "arm": arm,
                        "group": o["group"],
                        "id": o["id"],
                        "before": before["status"],
                        "after": o["status"],
                    }
                )
            if o["group"] == "control":
                for hit in o["output_matches"]:
                    for piece in hit["pieces"]:
                        by_label[o["expected"]].add((o["raw_sha256"], piece["segment_index"]))
        additional = []
        budget = added.copy()
        for s in outputs:
            for u in s["segments"]:
                k = key(s, u)
                if u["admitted"] and budget[k]:
                    labels = [
                        label
                        for label, values in by_label.items()
                        if (s["raw_sha256"], u["segment_index"]) in values
                    ]
                    additional.append(
                        {
                            "raw_sha256": s["raw_sha256"],
                            "segment_index": u["segment_index"],
                            "text_sha256": u["text_sha256"],
                            "characters": u["characters"],
                            "contains_control_labels": labels,
                            "whole_unit_semantic_relevance": "UNASSESSED",
                        }
                    )
                    budget[k] -= 1
        inventories[arm] = {
            "additional_units_vs_B00": sum(added.values()),
            "removed_units_vs_B00": sum(removed.values()),
            "additional_characters": sum(u["characters"] for u in additional),
            "additional_units": additional,
            "additional_units_without_any_selected_control_span": sum(
                not u["contains_control_labels"] for u in additional
            ),
        }
    with (BASE / "per_source.csv").open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "arm",
                "raw_sha256",
                "source_url",
                "acquisition_ags",
                "parsed_segments",
                "admitted_segments",
                "admitted_characters",
                "dev_found",
                "dev_denominator",
                "control_positive_found",
                "control_positive_denominator",
                "control_negative_admitted",
                "control_negative_denominator",
                "control_unresolved",
            ]
        )
        for s in result["outputs"]:
            obs = [
                o
                for o in result["observations"]
                if o["arm"] == s["arm"] and o["raw_sha256"] == s["raw_sha256"]
            ]
            dev = [o for o in obs if o["group"] == "development"]
            ctrl = [o for o in obs if o["group"] == "control"]
            pos = [o for o in ctrl if o["expected"] == "POSITIVE"]
            neg = [o for o in ctrl if o["expected"] == "NEGATIVE"]
            w.writerow(
                [
                    s[k]
                    for k in [
                        "arm",
                        "raw_sha256",
                        "source_url",
                        "acquisition_ags",
                        "parsed_segments",
                        "admitted_segments",
                        "admitted_characters",
                    ]
                ]
                + [
                    sum(o["status"] == "FOUND" for o in dev),
                    len(dev),
                    sum(o["status"] == "FOUND" for o in pos),
                    len(pos),
                    sum(o["output_found"] for o in neg),
                    len(neg),
                    sum(o["status"] == "UNRESOLVED_MAPPING" for o in ctrl),
                ]
            )
    resources = {}
    for run in ["run01", "run02"]:
        value = json.loads((BASE / run / "resources.json").read_bytes())
        timings = value["timings"]
        resources[run] = {
            "process_peak_rss_bytes": value["peak_process_rss_bytes_macos"],
            "operations": {
                op: {
                    "seconds": sum(t["seconds"] for t in timings if t["operation"] == op),
                    "max_python_peak_bytes": max(
                        (
                            t["python_peak_bytes"]
                            for t in timings
                            if t["operation"] == op and "python_peak_bytes" in t
                        ),
                        default=None,
                    ),
                }
                for op in sorted({t["operation"] for t in timings})
            },
        }
    d.write(
        BASE / "post_analysis.json",
        {
            "status": "POST_RESULT_ANALYSIS_NO_RULE_OR_LABEL_CHANGES",
            "results_sha256": d.sha(BASE / "run01/results.json"),
            "inventories": inventories,
            "duplicate_groups": duplicates,
            "transitions": transitions,
            "resources": resources,
            "control_label_counts": dict(Counter(c["expected"] for c in controls)),
            "analysis_source_sha256": d.sha(Path(__file__)),
        },
    )
    print(
        json.dumps(
            {
                a: {k: v for k, v in values.items() if k != "additional_units"}
                for a, values in inventories.items()
            }
        )
    )


if __name__ == "__main__":
    main()
