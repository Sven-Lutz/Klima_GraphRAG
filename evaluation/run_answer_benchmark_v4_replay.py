"""Frozen 45-case, four-method municipal-policy QA benchmark.

This is a minimal replacement for evaluation/run_answer_benchmark_v4_replay.py,
NOT a new policy extractor. It reads the 2026-10-15 frozen SQLite snapshot,
uses the existing canonical retriever/answer generator, and preserves earlier
historical benchmark outputs by using a distinct output prefix.

From the project root (PowerShell):
  $env:PYTHONPATH=(Get-Location).Path
  python -m evaluation.run_answer_benchmark_v4_replay --preflight --method graph --case MPB-006
  python -m evaluation.run_answer_benchmark_v4_replay --preflight --method all --case MPB-006
  $env:KLIMA_POLICY_CLAIMS_V1="1"
  python -m evaluation.run_answer_benchmark_v4_replay --method all --case MPB-006 --limit 4
  python -m evaluation.run_answer_benchmark_v4_replay --method all --limit 0

Methods:
  plain    Source-free LLM (no canonical citation claim; separate JSON contract).
  semantic Frozen-corpus embedding retrieval, canonical citation/claim checks.
  graph    Structural SQLite topic/provenance retrieval, NOT reviewed policy facts.
  hybrid   RRF fusion of semantic and structural SQLite retrieval.

A source-topic graph edge is NOT a verified policy relation. Answers need
independent citation and temporal review regardless of their label match.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import date
import sqlite3
from typing import Any
from urllib.request import Request, urlopen

import numpy as np

from crawler.core.answer_context import EvidenceBudget, POLICY_CLASSIFICATION_PROMPT_VERSION_V4
from crawler.core.answer_generation import (
    AnswerRequest,
    CanonicalAnswerGenerator,
    GenerationParameters,
    JsonlAnswerRunRecorder,
    OllamaGenerationProvider,
    render_answer_json,
)
from crawler.core.retrieval import (
    CanonicalRetriever,
    EmbeddingIdentity,
    RetrievalMode,
    RetrievalRequest,
)
from crawler.core.snapshots import validate_snapshot_manifest

ROOT = Path(__file__).resolve().parents[1]
import os

BENCHMARK_SET = os.environ.get("KLIMA_BENCHMARK_SET", "pilot")

if BENCHMARK_SET == "heldout":
    BENCHMARK = ROOT / "evaluation/heldout_policy_benchmark_20261015.csv"
    BENCHMARK_SHA256 = "005dcb4350e1134681131fe4ee09bd9bf7681283fc410d3f920150b36a4d8f49"
    EXPECTED_GOLD = {"Yes": 15, "No": 3, "Unknown": 27}
elif BENCHMARK_SET == "pilot":
    BENCHMARK = ROOT / "evaluation/multi_policy_benchmark_v1_20261015.csv"
    BENCHMARK_SHA256 = "a0b62f9a07c8eeb69c644260c2eb9a7fa107e70b18795d58590ae8fa7d578491"
    EXPECTED_GOLD = {"Yes": 20, "No": 10, "Unknown": 15}
else:
    raise RuntimeError("Invalid benchmark selection")

DATABASE = (ROOT / "crawler/data/db/FINAL_V3_GRAPH_PILOT_SNAPSHOT_20261015.sqlite").resolve()
MANIFEST = (ROOT / "crawler/data/db/FINAL_V3_GRAPH_PILOT_SNAPSHOT_20261015.sqlite.manifest.json").resolve()
STRUCTURAL_DATABASE = (ROOT / "crawler/data/db/FINAL_V3_GRAPH_COMPARISON_WORK.sqlite").resolve()
DEFAULT_OUTPUT_PREFIX = ROOT / "evaluation/multi_policy_four_methods_frozen_20261015_refdate"
RUNNER_VERSION = "four-method-graph-snapshot-2-reference-locked"
REFERENCE_DATE = "2026-10-15"  # Frozen benchmark reference date, not wall-clock time.
REFERENCE_DAY = date.fromisoformat(REFERENCE_DATE)
METHODS = ("plain", "semantic", "graph", "hybrid")
TOP_K = 8
MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434"
GENERATION_PARAMETERS = GenerationParameters(
    temperature=0.0, max_output_tokens=768, seed=42, timeout_seconds=360.0,
)
EVIDENCE_BUDGET = EvidenceBudget(max_items=8, max_characters=24_000)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=(*METHODS, "all"), default="all")
    parser.add_argument("--limit", type=int, default=2,
                        help="Number of new (method,question) evaluations; 0 = all remaining")
    parser.add_argument("--case", metavar="MPB-006", help="Limit to one frozen benchmark case")
    parser.add_argument("--preflight", action="store_true",
                        help="Read-only retrieval and identity tests; NEVER calls Ollama or writes results")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    return args


def _load_benchmark() -> list[dict[str, str]]:
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    if digest != BENCHMARK_SHA256:
        raise RuntimeError("Frozen benchmark SHA256 mismatch; refuse to evaluate")
    with BENCHMARK.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 45 or len({r["benchmark_id"] for r in rows}) != 45:
        raise RuntimeError("Frozen benchmark must contain 45 unique cases")
    labels = {"YES": 20, "NO": 10, "UNKNOWN": 15}
    from collections import Counter
    if {label: sum(r["human_label"] == label for r in rows) for label in EXPECTED_GOLD} != EXPECTED_GOLD:
        raise RuntimeError("Frozen benchmark gold distribution changed")
    return rows


def _snapshot_binding():
    for path in (DATABASE, MANIFEST, STRUCTURAL_DATABASE):
        if not path.is_file():
            raise FileNotFoundError(f"Missing original benchmark database: {path}")
    binding = validate_snapshot_manifest(MANIFEST)
    if DATABASE != binding.database.path:
        raise RuntimeError("Frozen graph manifest points to a different SQLite snapshot")
    return binding


def _embedding_metadata() -> dict[str, Any]:
    """Select a single COMPLETE embedding artifact from the frozen corpus; never build one."""
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(embedding_artifacts)")}
        needed = {"artifact_id", "provider", "model", "model_revision", "vector_dimension",
                  "preprocessing_id", "planned_count", "embedded_count", "status"}
        if not needed <= columns:
            raise RuntimeError("Frozen database has no compatible embedding artifact metadata")
        n_chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        candidates = db.execute("""SELECT artifact_id, provider, model, model_revision,
                   vector_dimension, preprocessing_id, planned_count, embedded_count
                   FROM embedding_artifacts WHERE status='completed'
                   AND planned_count=embedded_count AND embedded_count=?""", (n_chunks,)).fetchall()
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one complete frozen-corpus embedding artifact; found {len(candidates)}. "
            "Do not rebuild or silently substitute a different vector collection."
        )
    aid, provider, model, revision, dim, prep, planned, actual = candidates[0]
    return {"artifact_id": aid, "provider": provider, "model": model,
            "model_revision": revision, "vector_dimension": int(dim),
            "preprocessing_id": prep, "planned_count": int(planned), "embedded_count": int(actual)}


class FrozenQueryEmbedder:
    """Reuse only the model already declared by the frozen embedding artifact."""
    def __init__(self, metadata: dict[str, Any]):
        self.identity = EmbeddingIdentity(
            provider=metadata["provider"], model=metadata["model"],
            model_revision=metadata["model_revision"],
            vector_dimension=metadata["vector_dimension"],
            preprocessing_id=metadata["preprocessing_id"],
        )
        self._model = None

    def embed_query(self, text: str) -> np.ndarray:
        if self._model is None:
            # Strictly offline: an evaluation must not silently download model weights.
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("Local sentence-transformers package missing; no fallback allowed") from exc
            try:
                self._model = SentenceTransformer(self.identity.model, device="cpu", local_files_only=True)
            except TypeError:
                # Older sentence-transformers versions lack local_files_only;
                # the offline environment above still prohibits downloads.
                self._model = SentenceTransformer(self.identity.model, device="cpu")
            self._model.max_seq_length = 256
            dim = self._model.get_sentence_embedding_dimension()
            if dim != self.identity.vector_dimension:
                raise RuntimeError("Loaded query model dimension does not match frozen embeddings")
        vector = np.asarray(self._model.encode(text, convert_to_numpy=True), dtype=np.float32)
        if vector.shape != (self.identity.vector_dimension,):
            raise RuntimeError("Query embedding has invalid shape")
        return vector


def _retriever(method: str, metadata: dict[str, Any] | None) -> CanonicalRetriever:
    return CanonicalRetriever(
        embedder=FrozenQueryEmbedder(metadata) if method in {"semantic", "hybrid"} else None,
        structural_graph_database=STRUCTURAL_DATABASE,
    )


def _retrieval_request(row: dict[str, str], method: str) -> RetrievalRequest:
    modes = {"semantic": RetrievalMode.SEMANTIC,
             "graph": RetrievalMode.GRAPH, "hybrid": RetrievalMode.HYBRID}
    return RetrievalRequest(
        query=row["question"], manifest_path=str(MANIFEST),
        mode=modes[method], graph_source="structural_sqlite",
        municipality_ids=(row["municipality_id"],), top_k=TOP_K,
    )


def _hash_code() -> dict[str, str]:
    paths = [
        "evaluation/run_answer_benchmark_v4_replay.py",
        "crawler/core/retrieval.py",
        "crawler/core/answer_generation.py",
        "crawler/core/answer_context.py",
    ]
    result = {}
    for name in paths:
        file = ROOT / name
        if not file.is_file():
            raise FileNotFoundError(f"Missing required implementation file: {file}")
        result[name] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def _ollama_model_digest(model: str) -> str:
    """Pin the installed model revision so resuming never mixes different weights."""
    with urlopen(f"{OLLAMA_URL}/api/tags", timeout=15) as response:
        tags = json.load(response)
    matches = [item for item in tags.get("models", [])
               if item.get("name") == model or item.get("model") == model]
    if len(matches) != 1 or not isinstance(matches[0].get("digest"), str) \
            or len(matches[0]["digest"]) < 32:
        raise RuntimeError(f"Cannot pin installed Ollama model revision: {model}")
    return matches[0]["digest"]


def _signatures(binding, model: str, meta: dict[str, Any] | None,
                model_digest: str) -> dict[str, tuple[dict, str]]:
    config = {
        "runner_version": RUNNER_VERSION,
        "benchmark_sha256": BENCHMARK_SHA256,
        "reference_date": REFERENCE_DATE,
        "snapshot_id": str(binding.database.snapshot_id),
        "manifest_id": str(binding.manifest["manifest_id"]),
        "database_path": str(DATABASE),
        "structural_graph_database": str(STRUCTURAL_DATABASE),
        "structural_graph_size": STRUCTURAL_DATABASE.stat().st_size,
        "structural_graph_mtime_ns": STRUCTURAL_DATABASE.stat().st_mtime_ns,
        "model": model,
        "model_digest": model_digest,
        "retrieval_top_k": TOP_K,
        "generation_parameters": GENERATION_PARAMETERS.to_dict(),
        "evidence_budget": {"max_items": 8, "max_characters": 24000},
        "prompt_version": POLICY_CLASSIFICATION_PROMPT_VERSION_V4,
        "claims_v1": os.environ.get("KLIMA_POLICY_CLAIMS_V1", ""),
        "graph_kind": "structural_topic_provenance_NOT_verified_policy_facts",
        "embedding_artifact": meta,
        "source_code_sha256": _hash_code(),
    }
    out = {}
    for method in METHODS:
        individual = dict(config, method=method, mode=("no_retrieval" if method == "plain" else method))
        digest = hashlib.sha256(json.dumps(individual, sort_keys=True).encode()).hexdigest()
        out[method] = individual, "four-method-config-sha256:" + digest
    return out


def _existing(path: Path, signatures: dict, allowed_ids: set[str]) -> dict[tuple[str, str], dict]:
    records = {}
    if not path.is_file():
        return records
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Corrupt saved evaluation checkpoint at line {number}") from exc
            method = item.get("method")
            bid = item.get("benchmark_id")
            if method not in signatures or bid not in allowed_ids:
                raise RuntimeError(f"Unexpected method or benchmark ID in checkpoint line {number}")
            if item.get("config_signature") != signatures[method][1]:
                raise RuntimeError(
                    f"Existing checkpoint line {number} has a different configuration. "
                    "Choose a distinct --output-prefix; do not mix experimental conditions."
                )
            records[(method, bid)] = item
    return records


def _predicted_label(payload: dict) -> str:
    status = payload.get("status")
    if status == "insufficient_evidence":
        return "Unknown"
    if status != "answered":
        return "ERROR"
    answer = str(payload.get("answer_text") or "").strip()
    if answer.startswith("YES:"):
        return "Yes"
    if answer.startswith("NO:"):
        return "No"
    return "FORMAT_ERROR"


def _plain_response(question: str, model: str) -> dict:
    """True no-retrieval baseline, NOT canonical RAG's forced-UNKNOWN empty evidence path."""
    system = (
        "You have NO retrieved documents, database access or source citations. "
        "For a municipality climate/energy policy question, answer only what can "
        "reasonably be known from your own pretrained knowledge. "
        "The historical reference date is 2026-10-15. Do not claim to have "
        "verified current municipal policies or invent any sources, dates or citations. "
        "If you are uncertain, answer UNKNOWN rather than inferring NO from silence. "
        "Return JSON only: verdict YES, NO or UNKNOWN, and a brief answer."
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": question}],
        "stream": False,
        "format": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["YES", "NO", "UNKNOWN"]},
            "answer": {"type": "string"}},
            "required": ["verdict", "answer"], "additionalProperties": False},
        "options": {"temperature": 0.0, "num_predict": 768, "num_ctx": 8192, "seed": 42},
    }
    req = Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=GENERATION_PARAMETERS.timeout_seconds) as response:
        raw = json.load(response)
    if str(raw.get("model", "")) != model:
        raise RuntimeError("Ollama returned a different model from requested")
    structured = json.loads(raw["message"]["content"])
    if set(structured) != {"verdict", "answer"} or structured["verdict"] not in {"YES", "NO", "UNKNOWN"}:
        raise ValueError("Source-free model returned invalid JSON verdict")
    verdict = structured["verdict"]
    explanation = " ".join(str(structured["answer"]).split())[:2000]
    answer = (verdict + ": " + explanation) if verdict != "UNKNOWN" else explanation
    return {
        "status": "insufficient_evidence" if verdict == "UNKNOWN" else "answered",
        "answer_text": answer,
        "citations": [], "retrieval_result": {"items": []}, "failure": None,
        "warnings": ["PLAIN_BASELINE: no retrieved source evidence; source correctness not established"],
    }


def _write_summary(path: Path, records: dict, rows: list[dict[str, str]]) -> None:
    columns = ("method", "benchmark_id", "municipality_id", "municipality", "policy_id",
               "question", "human_label", "predicted_label", "correct", "status",
               "citation_count", "retrieved_count", "failure_code", "answer")
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            for method in METHODS:
                record = records.get((method, row["benchmark_id"]))
                if record is None:
                    continue
                payload = record["answer_result"]
                failure = payload.get("failure") or {}
                writer.writerow({
                    "method": method,
                    "benchmark_id": row["benchmark_id"],
                    "municipality_id": row["municipality_id"],
                    "municipality": row["municipality"],
                    "policy_id": row["policy_id"],
                    "question": row["question"],
                    "human_label": row["human_label"],
                    "predicted_label": record["predicted_label"],
                    "correct": record["correct"],
                    "status": payload.get("status", ""),
                    "citation_count": len(payload.get("citations") or []),
                    "retrieved_count": len((payload.get("retrieval_result") or {}).get("items") or []),
                    "failure_code": failure.get("code", "") if isinstance(failure, dict) else "",
                    "answer": payload.get("answer_text", ""),
                })
    os.replace(temp, path)


def main() -> None:
    args = _args()
    methods = METHODS if args.method == "all" else (args.method,)
    rows = _load_benchmark()
    if args.case:
        rows = [row for row in rows if row["benchmark_id"] == args.case]
        if not rows:
            raise RuntimeError(f"Unknown benchmark ID: {args.case}")
    binding = _snapshot_binding()
    metadata = _embedding_metadata() if any(m in {"semantic", "hybrid"} for m in methods) else None
    print("FROZEN_BENCHMARK: PASS", BENCHMARK_SHA256, flush=True)
    print("SNAPSHOT:", binding.database.snapshot_id, flush=True)
    print("POLICY_REFERENCE_DATE:", REFERENCE_DATE, "(explicitly passed to verifier)", flush=True)
    print("METHODS:", ",".join(methods), flush=True)
    print("GRAPH_KIND: structural topic/provenance; NOT verified policy assertions", flush=True)
    if metadata is not None:
        print("FROZEN_EMBEDDING_ARTIFACT:", metadata["artifact_id"], flush=True)
        print("FROZEN_EMBEDDING_MODEL:", metadata["model"], flush=True)

    retrievers = {method: _retriever(method, metadata) for method in methods if method != "plain"}
    if args.preflight:
        print("PREFLIGHT: no Ollama calls, no files or database writes", flush=True)
        examined = 0
        for row in rows:
            for method in methods:
                if args.limit and examined >= args.limit:
                    break
                if method == "plain":
                    print(f"{row['benchmark_id']} plain: no source passages by definition", flush=True)
                else:
                    result = retrievers[method].retrieve(_retrieval_request(row, method))
                    print(f"{row['benchmark_id']} {method}: {len(result.items)} chunks; "
                          f"top URL: {result.items[0].document.source_url if result.items else '(none)'}",
                          flush=True)
                    if method in {"graph", "hybrid"} and any(
                        x.assertions or x.reviewed_policy_facts for x in result.items
                    ):
                        raise RuntimeError("Structural retrieval unexpectedly promoted unreviewed topics to assertions")
                examined += 1
            if args.limit and examined >= args.limit:
                break
        print("FOUR_METHOD_PREFLIGHT: PASS", examined, flush=True)
        return

    if os.environ.get("KLIMA_POLICY_CLAIMS_V1") != "1":
        raise RuntimeError("Set $env:KLIMA_POLICY_CLAIMS_V1='1' to retain RAG claim safeguards")

    # Need complete embedding identity for resume signatures even with --method plain/graph.
    if metadata is None:
        metadata = _embedding_metadata()
    model_digest = _ollama_model_digest(args.model)
    print("OLLAMA_MODEL_DIGEST:", model_digest, flush=True)
    signatures = _signatures(binding, args.model, metadata, model_digest)
    prefix = args.output_prefix.expanduser().resolve()
    checkpoint = Path(str(prefix) + ".jsonl")
    summary = Path(str(prefix) + "_summary.csv")
    runs = Path(str(prefix) + "_runs.jsonl")
    completed = _existing(checkpoint, signatures, {r["benchmark_id"] for r in _load_benchmark()})
    # Validate first; do not make a new directory or overwrite historic results on failure.
    prefix.parent.mkdir(parents=True, exist_ok=True)

    if any(m != "plain" for m in methods):
        provider = OllamaGenerationProvider(OLLAMA_URL)
        # One generator per retrieval source; each uses the same audited answer implementation.
        generators = {method: CanonicalAnswerGenerator(
            retriever=retrievers[method], providers={provider.name: provider},
            run_recorder=JsonlAnswerRunRecorder(runs),
        ) for method in methods if method != "plain"}
    else:
        generators = {}

    new_count = 0
    errors_in_a_row = 0
    for row in rows:
        for method in methods:
            key = (method, row["benchmark_id"])
            previous = completed.get(key)
            if previous and not (args.retry_errors and previous["predicted_label"] in {"ERROR", "FORMAT_ERROR"}):
                print(row["benchmark_id"], method, "SKIP", flush=True)
                continue
            if args.limit and new_count >= args.limit:
                break
            try:
                if method == "plain":
                    payload = _plain_response(row["question"], args.model)
                else:
                    result = generators[method].generate(AnswerRequest(
                        question=row["question"],
                        retrieval_request=_retrieval_request(row, method),
                        database_snapshot_id=str(binding.database.snapshot_id),
                        generation_provider="ollama", generation_model=args.model,
                        prompt_version=POLICY_CLASSIFICATION_PROMPT_VERSION_V4,
                        generation_parameters=GENERATION_PARAMETERS,
                        evidence_budget=EVIDENCE_BUDGET,
                        reference_date=REFERENCE_DAY,
                    ))
                    payload = json.loads(render_answer_json(result))
                    if method in {"graph", "hybrid"} and any(
                        item.get("assertions") or item.get("reviewed_policy_facts")
                        for item in ((payload.get("retrieval_result") or {}).get("items") or [])
                    ):
                        raise RuntimeError("Unreviewed structural topic graph returned verified policy assertions")
            except Exception as exc:
                payload = {"status": "generation_failed", "answer_text": "",
                           "citations": [], "retrieval_result": {"items": []},
                           "failure": {"code": "benchmark_runner_exception",
                                       "message": f"{type(exc).__name__}: {str(exc)[:400]}"}}
                print(f"ERROR {row['benchmark_id']} {method}: {type(exc).__name__}: {exc}", flush=True)
            prediction = _predicted_label(payload)
            correct = prediction.casefold() == row["human_label"].strip().casefold()
            record = {"method": method, "config_signature": signatures[method][1],
                      "run_config": signatures[method][0],
                      "benchmark_id": row["benchmark_id"],
                      "municipality_id": row["municipality_id"],
                      "question": row["question"], "human_label": row["human_label"],
                      "predicted_label": prediction, "correct": correct,
                      "answer_result": payload}
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
            completed[key] = record
            new_count += 1
            errors_in_a_row = errors_in_a_row + 1 if prediction in {"ERROR", "FORMAT_ERROR"} else 0
            print(f"{row['benchmark_id']} {method}: gold={row['human_label']} "
                  f"pred={prediction} status={payload.get('status')}", flush=True)
            _write_summary(summary, completed, _load_benchmark())
            if errors_in_a_row >= 2:
                print("STOP: Two consecutive technical/format errors; inspect the failures before continuing")
                break
        if errors_in_a_row >= 2 or (args.limit and new_count >= args.limit):
            break

    _write_summary(summary, completed, _load_benchmark())
    print("NEW_EVALUATIONS:", new_count, flush=True)
    for method in methods:
        evaluated = [completed[(method, row["benchmark_id"])] for row in rows
                     if (method, row["benchmark_id"]) in completed]
        correct = sum(bool(record["correct"]) for record in evaluated)
        technical = sum(record["predicted_label"] in {"ERROR", "FORMAT_ERROR"}
                        for record in evaluated)
        uncited_binary = sum(record["predicted_label"] in {"Yes", "No"}
                             and not (record["answer_result"].get("citations") or [])
                             for record in evaluated)
        print(f"METHOD_SUMMARY: {method} matched={correct}/{len(evaluated)} "
              f"technical_errors={technical} binary_without_citations={uncited_binary}", flush=True)
    print("SUMMARY:", summary, flush=True)
    print("NOTE: label matching does NOT establish correct citations, time validity, or policy truth", flush=True)


if __name__ == "__main__":
    main()
