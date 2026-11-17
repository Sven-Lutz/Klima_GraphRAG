"""Independent supervised benchmark for municipality policy existence decisions.

Requires *human-adjudicated* gold labels. Does not call the evaluated LLM and
never uses benchmark answers as live retrieval features.

Run: python -m crawler.scripts.evaluate_policy_decisions --gold evaluation/gold.csv \
     --predictions evaluation/predictions.json --report evaluation/report.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from typing import Any

GOLD_LABELS = {"yes", "no", "unknown"}
PRED_LABELS = GOLD_LABELS | {"conflict"}


def load_gold(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        if not {"question_id", "municipality_id", "policy_id", "label"}.issubset(reader.fieldnames or []):
            raise ValueError("Gold CSV needs question_id, municipality_id, policy_id, label")
        records = list(reader)
    for r in records:
        r["label"] = r["label"].strip().lower()
        if r["label"] not in GOLD_LABELS:
            raise ValueError(f"bad gold label {r['label']!r} in {r['question_id']}")
    return records


def load_predictions(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("predictions file empty")
    if path.suffix.lower() == ".jsonl":
        preds = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        preds = json.loads(text)
        if isinstance(preds, dict):
            preds = [preds]
    if not isinstance(preds, list):
        raise ValueError("predictions must be a JSON list or JSONL lines")
    return preds


def evaluate(gold: list[dict[str, str]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    if not gold:
        raise ValueError("gold fixture is empty")
    by_key: dict[str, dict[str, Any]] = {}
    for r in predictions:
        key = str(r.get("question_id", ""))
        if not key or key in by_key:
            raise ValueError("missing or duplicate prediction question_id")
        if r.get("label") not in PRED_LABELS:
            raise ValueError(f"invalid prediction label for {key}")
        by_key[key] = r
    seen: set[str] = set()
    confusion: dict[str, Counter] = defaultdict(Counter)
    by_municipality: dict[str, Counter] = defaultdict(Counter)
    by_policy: dict[str, Counter] = defaultdict(Counter)
    false_yes = false_no = mistakes = abstained = exact = decided = 0
    details: list[dict[str, Any]] = []
    for row in gold:
        key = row["question_id"].strip()
        if not key or key in seen:
            raise ValueError(f"empty or duplicate gold question_id {key!r}")
        seen.add(key)
        if key not in by_key:
            raise ValueError(f"missing prediction for {key}")
        pred = by_key[key]
        for field in ("municipality_id", "policy_id"):
            if str(pred.get(field)) != row[field]:
                raise ValueError(f"{field} mismatch for {key}: gold={row[field]} prediction={pred.get(field)}")
        prediction, actual = pred["label"], row["label"]
        confusion[actual][prediction] += 1
        is_abstain = prediction in ("unknown", "conflict")
        correct = prediction == actual
        if is_abstain:
            abstained += 1
        else:
            decided += 1
            if not correct:
                mistakes += 1
        if correct:
            exact += 1
        if prediction == "yes" and actual != "yes":
            false_yes += 1
        if prediction == "no" and actual != "no":
            false_no += 1
        for bucket, name in ((by_municipality, row["municipality_id"]), (by_policy, row["policy_id"])):
            bucket[name]["total"] += 1
            bucket[name]["exact"] += int(correct)
            bucket[name]["false_yes"] += int(prediction == "yes" and actual != "yes")
            bucket[name]["false_no"] += int(prediction == "no" and actual != "no")
            bucket[name]["abstained"] += int(is_abstain)
        details.append({"question_id": key, "gold": actual, "predicted": prediction, "correct": correct})
    extra = sorted(set(by_key) - seen)
    if extra:
        raise ValueError(f"predictions outside gold fixture: {extra[:10]}")
    n = len(gold)
    return {
        "n_questions": n,
        "exact_accuracy": exact / n,
        "decision_coverage": decided / n,
        "selective_accuracy": (decided - mistakes) / decided if decided else None,
        "false_yes": false_yes,
        "false_no": false_no,
        "abstained": abstained,
        "confusion": {name: dict(cnt) for name, cnt in sorted(confusion.items())},
        "by_municipality": {name: dict(cnt) for name, cnt in sorted(by_municipality.items())},
        "by_policy": {name: dict(cnt) for name, cnt in sorted(by_policy.items())},
        "details": details,
        "methodology": "Human gold vs frozen system predictions; abstentions count toward lack of coverage, not as definitive errors.",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Pilot existence-answer benchmark (human gold required)")
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--max-false-definitive", type=int, default=0)
    ap.add_argument("--min-selective-accuracy", type=float, default=0.95)
    ap.add_argument("--min-decision-coverage", type=float, default=0.5)
    args = ap.parse_args()
    report = evaluate(load_gold(args.gold), load_predictions(args.predictions))
    report["gate"] = {
        "passed": (
            report["false_yes"] + report["false_no"] <= args.max_false_definitive
            and report["selective_accuracy"] is not None
            and report["selective_accuracy"] >= args.min_selective_accuracy
            and report["decision_coverage"] >= args.min_decision_coverage
        ),
        "thresholds": {
            "max_false_definitive": args.max_false_definitive,
            "min_selective_accuracy": args.min_selective_accuracy,
            "min_decision_coverage": args.min_decision_coverage,
        },
    }
    s = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(s, encoding="utf-8")
    print(s)
    if not report["gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
