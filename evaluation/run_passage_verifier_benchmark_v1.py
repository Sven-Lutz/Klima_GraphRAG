from __future__ import annotations

import csv
import json
import urllib.request
from collections import Counter
from pathlib import Path

import evaluation.run_answer_benchmark_v4_replay as base
from crawler.core.evidence_verification import aggregate_evidence_assessments
from crawler.core.passage_verifier import PassageVerificationRequest, build_passage_verifier_prompt, parse_passage_verifier_response

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1:8b"
OUTPUT_JSONL = Path("evaluation/passage_verifier_v1_45case_20261102.jsonl")
SUMMARY_CSV = Path("evaluation/passage_verifier_v1_45case_20261102_summary.csv")


def verify(request: PassageVerificationRequest):
    payload = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "seed": 42},
        "messages": [
            {"role": "system", "content": build_passage_verifier_prompt(request)},
            {"role": "user", "content": "Return the extraction JSON now."},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    http_request = urllib.request.Request(OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(http_request, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))
    return parse_passage_verifier_response(request, result["message"]["content"])


def load_completed():
    completed = {}
    if not OUTPUT_JSONL.exists():
        return completed
    for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            completed[row["benchmark_id"]] = row
    return completed


def predicted_label(verdict: str) -> str:
    return {"YES": "Yes", "NO": "No", "INSUFFICIENT_EVIDENCE": "Unknown"}[verdict]


def main():
    with base.BENCHMARK.open("r", encoding="utf-8-sig", newline="") as handle:
        benchmark_rows = list(csv.DictReader(handle))
    if len(benchmark_rows) != 45:
        raise RuntimeError(f"Expected 45 benchmark rows, found {len(benchmark_rows)}")

    replay = base.load_replay_results()
    completed = load_completed()
    print(f"Benchmark rows: {len(benchmark_rows)}")
    print(f"Already completed: {len(completed)}")
    print(f"Remaining: {len(benchmark_rows) - len(completed)}")

    for index, benchmark in enumerate(benchmark_rows, start=1):
        benchmark_id = benchmark["benchmark_id"]
        if benchmark_id in completed:
            print(f"[{index:02d}/45] {benchmark_id} SKIP")
            continue

        question = benchmark["question"]
        municipality_id = benchmark["municipality_id"]
        retrieval = replay[(question, municipality_id)]
        assessments = []
        passage_records = []

        try:
            for item in retrieval.items:
                request = PassageVerificationRequest(question=question, policy_id=benchmark["policy_id"], evidence_id=item.evidence_id, passage=item.evidence_text)
                assessment = verify(request)
                assessments.append(assessment)
                passage_records.append({
                    "rank": item.rank,
                    "evidence_id": item.evidence_id,
                    "verdict": assessment.verdict.value,
                    "rationale": assessment.rationale,
                })

            aggregate = aggregate_evidence_assessments(tuple(assessments))
            prediction = predicted_label(aggregate.verdict.value)
            error = None
        except Exception as exc:
            aggregate = None
            prediction = "ERROR"
            error = repr(exc)

        correct = prediction == benchmark["human_label"]
        record = {
            "benchmark_id": benchmark_id,
            "municipality_id": municipality_id,
            "municipality": benchmark["municipality"],
            "policy_id": benchmark["policy_id"],
            "question": question,
            "human_label": benchmark["human_label"],
            "predicted_label": prediction,
            "correct": correct,
            "passages": passage_records,
            "aggregate_verdict": aggregate.verdict.value if aggregate else None,
            "supporting_evidence_ids": list(aggregate.supporting_evidence_ids) if aggregate else [],
            "conflicting_evidence_ids": list(aggregate.conflicting_evidence_ids) if aggregate else [],
            "error": error,
        }
        with OUTPUT_JSONL.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        completed[benchmark_id] = record
        print(f"[{index:02d}/45] {benchmark_id} gold={benchmark['human_label']} pred={prediction} {'OK' if correct else 'MISS'}")

    ordered = [completed[row["benchmark_id"]] for row in benchmark_rows if row["benchmark_id"] in completed]
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["benchmark_id", "human_label", "predicted_label", "correct"])
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: row[k] for k in writer.fieldnames})

    correct_count = sum(1 for row in ordered if row["correct"])
    predictions = Counter(row["predicted_label"] for row in ordered)
    print("---")
    print(f"Completed: {len(ordered)}/45")
    print(f"Correct: {correct_count}/{len(ordered)}")
    print("Predictions:", dict(predictions))
    print(f"Output: {OUTPUT_JSONL}")
    print(f"Summary: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
