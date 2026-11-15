"""Source-only reviewer packets from quarantined candidates, without machine answers."""

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .analysis import digest


def prepare(db, candidates, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh reviewer packet directory")
    tasks = [json.loads(line) for line in Path(candidates).read_text().splitlines()]
    groups = defaultdict(list)
    for task in tasks:
        groups[(task["region"], task["family"])].append(task)
    for group in groups.values():
        group.sort(key=lambda t: digest(["review-selection-v1", t["id"]]))
    selected, municipalities = [], set()
    # Prefer new municipalities while round-robining region and task family.
    while len(selected) < min(200, len(tasks)) and any(groups.values()):
        for key in sorted(groups):
            group = groups[key]
            if not group or len(selected) == 200:
                continue
            index = next((i for i, t in enumerate(group) if t["ags"] not in municipalities), 0)
            task = group.pop(index)
            selected.append(task)
            municipalities.add(task["ags"])
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        paths = dict(conn.execute("SELECT sha256,raw_path FROM documents"))
    output.mkdir(parents=True)
    for reviewer in ("A", "B"):
        directory = output / reviewer
        directory.mkdir()
        ordered = sorted(selected, key=lambda t: digest([reviewer, t["id"]]))
        source_only = [
            {
                key: task[key]
                for key in ("id", "ags", "question", "original_url", "candidate_source", "page_ref")
            }
            | {"original_path": paths[task["candidate_source"]]}
            for task in ordered
        ]
        (directory / "tasks.jsonl").write_text(
            "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in source_only)
        )
        with (directory / "judgments.csv").open("w") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "task_id",
                    "reviewer_identity",
                    "original_seen_at",
                    "answerability",
                    "scope_ags",
                    "value_unit_time_status",
                    "original_evidence",
                    "answer",
                    "uncertainty",
                    "exposure",
                ]
            )
            writer.writerows([task["id"]] + [""] * 9 for task in ordered)
        (directory / "README.md").write_text(
            "# Independent original-first review\n\nRead REVIEW_PROTOCOL.md before annotation. "
            "Record your identity and actual original-inspection time. Inspect the original "
            "before parsed or machine-produced material. Do not consult the other reviewer. "
            "No independent judgments have been prefilled. Gzip originals must be decompressed "
            "losslessly for original HTML/PDF inspection. Shared source rights and actual "
            "reviewer authorization are required before external transfer.\n"
        )
        (directory / "REVIEW_PROTOCOL.md").write_bytes(
            Path("research/bavaria/REVIEW_PROTOCOL.md").read_bytes()
        )
    with (output / "adjudication.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "task_id",
                "reviewer_A",
                "reviewer_B",
                "disagreement_type",
                "original_locator",
                "verdict",
                "rationale",
                "adjudicator",
                "time",
            ]
        )
        writer.writerows([task["id"]] + [""] * 8 for task in selected)
    report = {
        "selected_tasks": len(selected),
        "municipalities": len(municipalities),
        "regions": dict(Counter(t["region"] for t in selected)),
        "families": dict(Counter(t["family"] for t in selected)),
        "selection_id": digest(sorted(t["id"] for t in selected)),
        "accepted_gold": 0,
        "actual_human_reviews": 0,
        "split": "QUARANTINE_UNTIL_SOURCE_PROJECT_DEPENDENCY_AUDIT",
        "planning_hours": (
            "67–133 reviewer-hours for 200 tasks × 2 × 10–20 min; unmeasured, plus adjudication"
        ),
        "instruction": "Pilot 20 tasks to measure annotation time before committing reviewers.",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.db, args.candidates, args.output), indent=2))
