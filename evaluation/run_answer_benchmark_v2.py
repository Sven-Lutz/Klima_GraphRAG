from __future__ import annotations

import csv
import json
from pathlib import Path

from crawler.core.answer_context import EvidenceBudget, POLICY_CLASSIFICATION_PROMPT_VERSION
from crawler.core.answer_generation import (
    AnswerRequest,
    CanonicalAnswerGenerator,
    GenerationParameters,
    JsonlAnswerRunRecorder,
    OllamaGenerationProvider,
    render_answer_json,
)
from crawler.core.query_embedding import SentenceTransformerQueryEmbedder
from crawler.core.retrieval import CanonicalRetriever, RetrievalMode, RetrievalRequest
from crawler.core.snapshots import validate_snapshot_manifest
from crawler.scripts.generate_embeddings import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    DEFAULT_PREPROCESSING_ID,
    DEFAULT_PROVIDER,
)

BENCHMARK = Path("evaluation/multi_policy_benchmark_v1_20261015.csv")
DATABASE = Path("crawler/data/db/FINAL_V3_RAG_SNAPSHOT_20261012.sqlite").resolve()
MANIFEST = Path("crawler/data/db/FINAL_V3_RAG_SNAPSHOT_20261012.manifest.json").resolve()
LEXICAL_DATABASE = Path("crawler/data/db/FINAL_V3_LEXICAL_FTS_20261015.sqlite").resolve()

OUTPUT_JSONL = Path("evaluation/multi_policy_answer_v2_llama32_20261015.jsonl")
RUN_LOG = Path("evaluation/multi_policy_answer_v2_llama32_20261015_runs.jsonl")
SUMMARY_CSV = Path("evaluation/multi_policy_answer_v2_llama32_20261015_summary.csv")

MODEL = "llama3.2:3b"
TOP_K = 8


def predicted_label(payload: dict) -> str:
    status = str(payload.get("status", ""))
    if status == "insufficient_evidence":
        return "Unknown"
    if status != "answered":
        return "ERROR"
    answer = str(payload.get("answer", "")).strip()
    if answer.startswith("YES:"):
        return "Yes"
    if answer.startswith("NO:"):
        return "No"
    return "FORMAT_ERROR"


def existing_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    if not OUTPUT_JSONL.exists():
        return records
    for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        benchmark_id = str(row.get("benchmark_id", "")).strip()
        if benchmark_id:
            records[benchmark_id] = row
    return records


def write_summary(records: dict[str, dict], benchmark_rows: list[dict[str, str]]) -> None:
    fields = [
        "benchmark_id",
        "municipality_id",
        "municipality",
        "policy_id",
        "question",
        "human_label",
        "predicted_label",
        "correct",
        "status",
        "citation_count",
        "failure_code",
        "answer",
    ]
    with SUMMARY_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for benchmark in benchmark_rows:
            record = records.get(benchmark["benchmark_id"])
            if record is None:
                continue
            payload = record["answer_result"]
            citations = payload.get("citations") or []
            failure = payload.get("failure") or {}
            writer.writerow(
                {
                    "benchmark_id": benchmark["benchmark_id"],
                    "municipality_id": benchmark["municipality_id"],
                    "municipality": benchmark["municipality"],
                    "policy_id": benchmark["policy_id"],
                    "question": benchmark["question"],
                    "human_label": benchmark["human_label"],
                    "predicted_label": record["predicted_label"],
                    "correct": record["correct"],
                    "status": payload.get("status", ""),
                    "citation_count": len(citations),
                    "failure_code": failure.get("code", "") if isinstance(failure, dict) else "",
                    "answer": payload.get("answer", ""),
                }
            )


def main() -> None:
    with BENCHMARK.open("r", encoding="utf-8-sig", newline="") as handle:
        benchmark_rows = list(csv.DictReader(handle))

    if len(benchmark_rows) != 45:
        raise RuntimeError(f"Expected 45 benchmark rows, found {len(benchmark_rows)}")

    binding = validate_snapshot_manifest(MANIFEST)
    if DATABASE != binding.database.path:
        raise RuntimeError("Database path does not match the validated snapshot manifest")

    completed = existing_records()
    print(f"Benchmark rows: {len(benchmark_rows)}")
    print(f"Already completed: {len(completed)}")
    print(f"Remaining: {len(benchmark_rows) - len(completed)}")

    embedder = SentenceTransformerQueryEmbedder(
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL,
        model_revision=DEFAULT_MODEL_REVISION,
        default_model_revision=DEFAULT_MODEL_REVISION,
        vector_dimension=384,
        preprocessing_id=DEFAULT_PREPROCESSING_ID,
    )
    retriever = CanonicalRetriever(
        embedder=embedder,
        graph_backend=None,
        lexical_database=LEXICAL_DATABASE,
    )
    provider = OllamaGenerationProvider("http://localhost:11434")
    generator = CanonicalAnswerGenerator(
        retriever=retriever,
        providers={provider.name: provider},
        run_recorder=JsonlAnswerRunRecorder(RUN_LOG),
    )

    for index, benchmark in enumerate(benchmark_rows, start=1):
        benchmark_id = benchmark["benchmark_id"]
        if benchmark_id in completed:
            print(f"[{index:02d}/45] {benchmark_id} SKIP")
            continue

        question = benchmark["question"]
        municipality_id = benchmark["municipality_id"]
        request = RetrievalRequest(
            query=question,
            manifest_path=str(MANIFEST),
            mode=RetrievalMode.SEMANTIC_LEXICAL,
            top_k=TOP_K,
            projection_id=None,
            extraction_run_ids=(),
            municipality_ids=(municipality_id,),
        )
        answer = generator.generate(
            AnswerRequest(
                question=question,
                retrieval_request=request,
                database_snapshot_id=str(binding.database.snapshot_id),
                generation_provider="ollama",
                generation_model=MODEL,
                prompt_version=POLICY_CLASSIFICATION_PROMPT_VERSION,
                generation_parameters=GenerationParameters(
                    temperature=0.0,
                    max_output_tokens=128,
                    seed=42,
                    timeout_seconds=180.0,
                ),
                evidence_budget=EvidenceBudget(
                    max_items=8,
                    max_characters=24_000,
                ),
            )
        )
        payload = json.loads(render_answer_json(answer))
        prediction = predicted_label(payload)
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
            "answer_result": payload,
        }
        with OUTPUT_JSONL.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        completed[benchmark_id] = record
        print(
            f"[{index:02d}/45] {benchmark_id} "
            f"gold={benchmark['human_label']} pred={prediction} "
            f"{'OK' if correct else 'MISS'} status={payload.get('status')}"
        )

    write_summary(completed, benchmark_rows)

    ordered = [completed[row["benchmark_id"]] for row in benchmark_rows if row["benchmark_id"] in completed]
    correct_count = sum(1 for row in ordered if row["correct"])
    error_count = sum(
        1 for row in ordered if row["predicted_label"] in {"ERROR", "FORMAT_ERROR"}
    )
    print("---")
    print(f"Completed: {len(ordered)}/45")
    print(f"Correct: {correct_count}/{len(ordered)}")
    print(f"Errors: {error_count}")
    print(f"Summary: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
