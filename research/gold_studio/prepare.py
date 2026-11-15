# ruff: noqa: E501
"""Freeze review selection and method-blind candidate pools without altering benchmarks."""

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "generated/bavaria/completion/independent-review-final"
RELEASE = ROOT / "research/bavaria/completion/release"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def focus_source(task):
    """Locate the displayed candidate, never infer claim scope from its host."""
    focus = task.get("focus") or {}
    explicit = task.get("focal_source_sha256") or focus.get("sha256")
    if explicit in task["allowed_original_sha256"]:
        return explicit
    matches = {
        row["source_sha256"]
        for row in task.get("pool", [])
        if row.get("text") == focus.get("text")
        and row["source_sha256"] in task["allowed_original_sha256"]
    }
    return next(iter(matches)) if len(matches) == 1 else None


def prepare(packet=PACKET, release=RELEASE):
    packet, release = Path(packet), Path(release)
    manifest = load(packet / "PACKET.json")
    for name in ["questions.json", "source_manifest.json"]:
        if sha(packet / name) != manifest["files_sha256"][name]:
            raise ValueError("Frozen packet identity mismatch")
    questions = load(packet / "questions.json")
    splits = {
        r["task_id"]: r
        for r in csv.DictReader(
            (ROOT / "research/bavaria/qualification/results/dependency_split.csv").open()
        )
    }
    new = {t["task_id"]: t for t in lines(release / "relations02/tasks.jsonl")}
    # Exposed development only; the old 70/130 tasks never enter calibration.
    # Round-robin task families, deterministic hash ordering, no outcome-based selection.
    groups: dict[str, list[str]] = {}
    for t in new.values():
        groups.setdefault(t["family"], []).append(t["task_id"])
    for ids in groups.values():
        ids.sort(key=lambda x: hashlib.sha256(("studio-calibration-v1" + x).encode()).hexdigest())
    chosen: list[str] = []
    while len(chosen) < 12:
        for key in sorted(groups):
            if groups[key] and len(chosen) < 12:
                chosen.append(groups[key].pop(0))
    selected = ["completion:" + x for x in chosen]
    selection = {
        "schema": "research-gold-studio-selection-v1",
        "seed": "studio-calibration-v1",
        "rule": "12 exposed new relation tasks, family round-robin and hash order; never old confirmation tasks",
        "calibration": selected,
        "main": [q["task_id"] for q in questions],
        "main_notes": "238 tasks, overlaps calibration; calibration labels excluded from confirmatory inference; original 200 remain quarantined.",
        "planned_old_split_counts": dict(Counter(r["planned_split"] for r in splits.values())),
        "packet_sha256": sha(packet / "PACKET.json"),
        "no_human_labels": True,
    }
    candidates: dict[str, dict[str, dict[str, Any]]] = {}
    admin = []
    db = ROOT / "generated/bavaria/completion/relations02/evidence.sqlite"
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        evidence = {r["evidence_id"]: dict(r) for r in c.execute("SELECT * FROM evidence")}
    for r in lines(release / "retrieval03/results.jsonl"):
        qid = "completion:" + r["task_id"]
        for eid in r["selected_evidence"]:
            row = evidence[eid]
            pid = hashlib.sha256((qid + eid).encode()).hexdigest()[:20]
            candidates.setdefault(qid, {})[pid] = {
                "id": pid,
                "evidence_id": eid,
                "source_sha256": row["source_sha256"],
                "text": row["text"],
                "page": row["page_ref"],
                "heading": row["heading"],
            }
            admin.append({"task_id": qid, "candidate_id": pid, "method": r["method"]})
    # Only source spans and neutral candidate IDs reach the reviewer API, never this admin map.
    tasks = []
    for q in questions:
        q = dict(q)
        tid = q["task_id"]
        short = tid.removeprefix("completion:")
        q["split"] = splits.get(tid, {}).get(
            "planned_split", new.get(short, {}).get("split", "quarantine")
        )
        q["effective_split"] = (
            "QUARANTINE" if q["cohort"] == "existing_200" else "DEVELOPMENT_EXPOSED"
        )
        q["components"] = new.get(short, {}).get(
            "component_ids", [splits.get(tid, {}).get("component_id", tid)]
        )
        q["pool"] = sorted(
            candidates.get(tid, {}).values(),
            key=lambda x: hashlib.sha256(("pool-v1" + x["id"]).encode()).hexdigest(),
        )
        tasks.append(q)
    answers = load(release / "answer-review-round2/answers.json")
    # Six auxiliary answer questions, not alterations of the 238-task benchmark.
    answer_tasks = {}
    for answer in answers:
        key = "answer:" + hashlib.sha256(answer["question"].encode()).hexdigest()[:20]
        if key not in answer_tasks:
            cited = {
                x.get("document_id", "").removeprefix("source-version:")
                for x in answer["citations"]
            }
            # Abstentions have no citations: the frozen answer context supplies access,
            # not a provisional support label.
            if not cited:
                # Match another candidate for same question; otherwise allow the 35 source cohort.
                cited = {
                    x.get("document_id", "").removeprefix("source-version:")
                    for a in answers
                    if a["question"] == answer["question"]
                    for x in a["citations"]
                }
            if not cited:
                cited = set(
                    x["sha256"]
                    for x in load(packet / "source_manifest.json")
                    if any(
                        x["sha256"] in t["allowed_original_sha256"]
                        for t in tasks
                        if t["cohort"] == "new_relations"
                    )
                )
            answer_tasks[key] = {
                "task_id": key,
                "question": answer["question"],
                "allowed_original_sha256": sorted(cited),
                "cohort": "auxiliary_answer_development",
                "split": "development",
                "effective_split": "DEVELOPMENT_EXPOSED",
                "components": ["answer-question:" + key],
                "pool": [],
            }
    tasks.extend(answer_tasks.values())
    # Exact eight frozen IE foci, without expected values or provisional labels.
    vocabulary = load(release / "instruct-ie01/METHOD_FREEZE.json")["schema"]["properties"]
    for case in lines(release / "model-qualification/cases.jsonl"):
        tasks.append(
            {
                "task_id": "ie:" + case["case_id"],
                "question": case["focus"],
                "allowed_original_sha256": [case["source_sha256"]],
                "cohort": "auxiliary_ie_development",
                "split": "development",
                "effective_split": "DEVELOPMENT_EXPOSED",
                "components": ["source:" + case["source_sha256"]],
                "pool": [],
                "field_options": {
                    key: [v for v in vocabulary[key].get("enum", []) if v is not None]
                    for key in ["role", "status", "amount_kind"]
                },
            }
        )

    # Freeze a region-stratified technical/human calibration before any human labels.
    regions = {
        "1": "Oberbayern",
        "2": "Niederbayern",
        "3": "Oberpfalz",
        "4": "Oberfranken",
        "5": "Mittelfranken",
        "6": "Unterfranken",
        "7": "Schwaben",
    }
    source_records = {x["sha256"]: x for x in load(packet / "source_manifest.json")}
    for t in tasks:
        ref = new.get(t["task_id"].removeprefix("completion:"), {})
        source_ids = [
            evidence[x]["source_sha256"] for x in ref.get("required_evidence", []) if x in evidence
        ]
        primary = source_records.get(source_ids[0], {}) if source_ids else {}
        ags = (primary.get("frame_ags") or [""])[0]
        t["stratum"] = {
            "region": regions.get(ags[2:3], "auxiliary_or_unresolved"),
            "format": "PDF" if primary.get("file", "").endswith(".pdf") else "HTML",
            "family": ref.get("family", "answer"),
            "ags": ags,
        }
    eligible = [t for t in tasks if t["cohort"] == "new_relations"]
    eligible.sort(
        key=lambda t: hashlib.sha256(("studio-calibration-v2" + t["task_id"]).encode()).hexdigest()
    )
    calibration = []
    for region in regions.values():
        candidate = next((t for t in eligible if t["stratum"]["region"] == region), None)
        if candidate:
            calibration.append(candidate["task_id"])
    for t in eligible:
        if len(calibration) >= 10:
            break
        if t["task_id"] not in calibration:
            calibration.append(t["task_id"])
    selection["calibration"] = calibration + sorted(answer_tasks)[:2]
    selection["seed"] = "studio-calibration-v2"
    selection["rule"] = (
        "One exposed relation task per seven administrative regions, hash-ordered fill to ten, plus two exposed answer questions; no old confirmation cases."
    )
    selection["main"] = [t["task_id"] for t in tasks]
    selection["auxiliary_answer_tasks"] = len(answer_tasks)
    selection["auxiliary_ie_tasks"] = 8
    selection["calibration_strata"] = {
        t["task_id"]: t["stratum"] for t in tasks if t["task_id"] in selection["calibration"]
    }
    selection["inputs_sha256"] = {
        str(x.relative_to(ROOT)): sha(x)
        for x in [
            release / "relations02/tasks.jsonl",
            release / "retrieval03/results.jsonl",
            release / "answer-review-round2/answers.json",
            ROOT / "research/bavaria/qualification/results/dependency_split.csv",
        ]
    }
    selection["review_content_sha256"] = hashlib.sha256(
        json.dumps(
            {"tasks": tasks, "answers": answers}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    return {
        "selection": selection,
        "tasks": tasks,
        "sources": load(packet / "source_manifest.json"),
        "answers": load(release / "answer-review-round2/answers.json"),
        "admin_pool_map": admin,
    }
