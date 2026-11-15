"""HTTP-contract QA in an isolated worker. Hard deadlines, RAM/disk gates and resume."""

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import resource
import shutil
import subprocess
import time
from pathlib import Path

SUITES = {"smoke": 20, "development": 120, "extended": 240, "full": 600}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rss_bytes(pid):
    result = subprocess.run(
        ["/bin/ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    return int(result.stdout.strip()) * 1024


def worker(pipe, manifest):
    from fastapi.testclient import TestClient

    from crawler.web.app import create_app
    from crawler.web.bavaria import create_bavaria_service

    service = create_bavaria_service(manifest)
    # Higher explicit isolated QA budget; shipped local preview remains 12/minute.
    with TestClient(
        create_app(service, query_budget_per_minute=240), raise_server_exceptions=False
    ) as client:
        pipe.send({"ready": True, "snapshot_id": service.database_snapshot_id})
        while True:
            task = pipe.recv()
            if task is None:
                return
            q, method = task
            start = time.monotonic()
            is_path = method == "graph" and q["category"] == "graph_paths"
            if is_path:
                response = client.get(
                    "/api/graph",
                    params={
                        "root": q["origin"]["entity_ids"][0],
                        "target": q["origin"]["entity_ids"][1],
                        "depth": 3,
                    },
                )
            else:
                payload = {
                    "question": q["question"],
                    "mode": method,
                    "municipality_ids": q["municipality_ids"],
                    "top_k": 5,
                    "generate_answer": False,
                    "max_evidence_items": 8,
                    "max_evidence_characters": 12000,
                    "reference_year": q.get("reference_year"),
                }
                payload.update(q.get("request_override", {}))
                response = client.post("/api/query", json=payload)
            try:
                body = response.json()
            except ValueError:
                body = {"error": {"code": "non_json_backend_error"}}
            retrieval = body.get("retrieval_result", {})
            evidence = body.get("evidence", [])
            if is_path:
                evidence = body.get("edges", [])
            citation_ids = {
                c["evidence_id"] for c in body.get("citation_registry", {}).get("citations", [])
            }
            pipe.send(
                {
                    "question_id": q["id"],
                    "method": method,
                    "http_status": response.status_code,
                    "application_status": body.get("error", {}).get("code", "ok"),
                    "retrieval_status": "nonempty"
                    if evidence
                    else "empty"
                    if response.status_code == 200
                    else "error",
                    "generation_status": "disabled",
                    "answer_count": int(body.get("answer_result") is not None),
                    "answer_text": body.get("answer_result", {}).get("answer_text")
                    if body.get("answer_result")
                    else None,
                    "evidence_count": len(evidence),
                    "snapshot_id": body.get("snapshot_id")
                    if is_path
                    else retrieval.get("database_snapshot_id"),
                    "scope": retrieval.get("search_scope"),
                    "latency_seconds": round(time.monotonic() - start, 6),
                    "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "evidence_ids": [e["id"] if is_path else e["evidence_id"] for e in evidence],
                    "source_ids": [e["unit_id"] if is_path else e["source_id"] for e in evidence],
                    "original_versions": [
                        e["source_sha256"] if is_path else e["document"]["document_id"]
                        for e in evidence
                    ],
                    "citation_resolution": None
                    if is_path
                    else all(e["evidence_id"] in citation_ids for e in evidence),
                    "path_relation_ids": body.get("chain") if is_path else None,
                    "expected_http": q.get("expected_http", 200),
                    "technical_error": None
                    if response.status_code == q.get("expected_http", 200)
                    else body.get("error", {}).get("code", "UNEXPECTED_HTTP"),
                    "correctness": "UNASSESSED_NO_HUMAN_QRELS",
                    "parameters": {"top_k": 5, "generation": False},
                }
            )


def load_checkpoints(directory, catalog_sha256, allowed):
    completed = {}
    for path in directory.glob("*.json"):
        row = json.loads(path.read_text())
        key = (row["question_id"], row["method"])
        if row.get("catalog_sha256") != catalog_sha256:
            raise ValueError("Checkpoint catalog mismatch")
        if key not in allowed or path.name != key[0] + "--" + key[1] + ".json":
            raise ValueError("Unknown or misnamed checkpoint")
        if key in completed:
            raise ValueError("Duplicate checkpoint identity")
        completed[key] = row
    return completed


def output_bytes(directory):
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def run(catalog, manifest, output, suite, seconds, max_cases=None, categories_filter=()):
    if shutil.disk_usage(".").free < 15 * 1024**3 + 64 * 1024**2:
        raise RuntimeError("SSD reserve plus output allowance")
    rows = json.loads((catalog / "CATALOG.json").read_text())
    freeze = json.loads((catalog / "FREEZE.json").read_text())
    if (
        digest(catalog / "CATALOG.json") != freeze["catalog_sha256"]
        or digest(manifest) != freeze["manifest_sha256"]
    ):
        raise ValueError("Catalog/manifest identity mismatch")
    output.mkdir(parents=True, exist_ok=True)
    code_files = [
        "crawler/web/bavaria.py",
        "crawler/core/retrieval.py",
        "research/bavaria/workspace/core.py",
        "crawler/web/app.py",
        "crawler/web/service.py",
        "crawler/web/graph_explorer.py",
    ]
    binding = {
        "catalog_sha256": freeze["catalog_sha256"],
        "snapshot_id": freeze["snapshot_id"],
        "runner_sha256": digest(__file__),
        "implementations": {p: digest(p) for p in code_files},
        "per_query_seconds": 18,
        "max_ram_bytes": 512 * 1024**2,
        "minimum_free_bytes": 15 * 1024**3,
        "max_output_bytes": 64 * 1024**2,
        "max_restarts": 3,
        "model_calls": 0,
        "transport": "isolated FastAPI TestClient HTTP",
        "qa_query_budget_per_minute": 240,
        "classification": "TECHNICAL_QA_NOT_SCIENTIFIC_CORRECTNESS",
    }
    lock = output / "BINDING.json"
    if lock.exists() and json.loads(lock.read_text()) != binding:
        raise ValueError("Cannot mix incompatible checkpoints")
    lock.write_text(json.dumps(binding, indent=2) + "\n")
    result_file = output / "results.jsonl"
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    allowed = {(q["id"], method) for q in rows for method in q["allowed_methods"]}
    completed = load_checkpoints(checkpoints, freeze["catalog_sha256"], allowed)
    written_bytes = output_bytes(output)
    # Spread every stage across all ten categories; earlier stages are a stable prefix.
    categories = freeze["categories"]
    ordered = []
    for i in range(60):
        for cat in categories:
            ordered.append(
                next(q for q in rows if q["category"] == cat and q["id"].endswith(f"-{i + 1:03}"))
            )
    count = min(SUITES[suite], max_cases or 600)
    chosen = ordered[:count]
    if categories_filter:
        chosen = [q for q in chosen if q["category"] in categories_filter]
    count = len(chosen)
    if any(q["split"] != "development_diagnostic" for q in chosen):
        raise ValueError("Confirmation execution forbidden")
    jobs = [(q, m) for q in chosen for m in q["allowed_methods"] if (q["id"], m) not in completed]
    start = time.monotonic()
    restarts = 0
    proc = None
    parent = None
    aborted = None
    try:
        for q, method in jobs:
            if time.monotonic() - start + 18 >= seconds:
                aborted = "WALL_BUDGET"
                break
            if shutil.disk_usage(".").free < 15 * 1024**3 + 64 * 1024**2:
                aborted = "SSD_RESERVE"
                break
            if written_bytes > 64 * 1024**2:
                aborted = "OUTPUT_LIMIT"
                break
            if proc is None:
                parent, child = mp.get_context("spawn").Pipe()
                proc = mp.get_context("spawn").Process(target=worker, args=(child, str(manifest)))
                proc.start()
                startup = time.monotonic()
                while not parent.poll(0.2) and proc.is_alive():
                    if time.monotonic() - startup > 45 or rss_bytes(proc.pid) > 512 * 1024**2:
                        break
                if not parent.poll(0):
                    proc.terminate()
                    proc.join()
                    aborted = "STARTUP_NO_PROGRESS"
                    break
                ready = parent.recv()
                if ready["snapshot_id"] != freeze["snapshot_id"]:
                    raise ValueError("Worker snapshot mismatch")
            if time.monotonic() - start + 18 >= seconds:
                aborted = "WALL_BUDGET"
                break
            parent.send((q, method))
            at = time.monotonic()
            result = None
            while not parent.poll(0.1):
                reason = None
                if time.monotonic() - at > 18:
                    reason = "QUERY_DEADLINE"
                elif not proc.is_alive():
                    reason = "WORKER_EXIT"
                elif rss_bytes(proc.pid) > 512 * 1024**2:
                    reason = "RAM_LIMIT"
                if reason:
                    proc.terminate()
                    proc.join()
                    proc = None
                    restarts += 1
                    result = {
                        "question_id": q["id"],
                        "method": method,
                        "http_status": None,
                        "application_status": reason,
                        "technical_error": reason,
                        "latency_seconds": round(time.monotonic() - at, 6),
                        "correctness": "UNASSESSED_NO_HUMAN_QRELS",
                    }
                    break
            if result is None:
                try:
                    result = parent.recv()
                except EOFError:
                    result = {
                        "question_id": q["id"],
                        "method": method,
                        "http_status": None,
                        "technical_error": "WORKER_EXIT",
                    }
                    proc.join()
                    proc = None
                    restarts += 1
            result["catalog_sha256"] = freeze["catalog_sha256"]
            result["recorded_at"] = time.time()
            checkpoint = checkpoints / (q["id"] + "--" + method + ".json")
            pending = checkpoint.with_suffix(".pending")
            serialized = json.dumps(result, ensure_ascii=False).encode("utf-8")
            needed_bytes = 2 * len(serialized) + 1
            if written_bytes + needed_bytes + 4096 > 64 * 1024**2:
                aborted = "OUTPUT_LIMIT"
                break
            if shutil.disk_usage(".").free < 15 * 1024**3 + needed_bytes + 4096:
                aborted = "SSD_RESERVE"
                break
            with pending.open("wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(checkpoint)
            with result_file.open("ab") as stream:
                stream.write(serialized + b"\n")
                stream.flush()
            written_bytes += needed_bytes
            completed[(q["id"], method)] = result
            if restarts >= 3:
                aborted = "RESTART_LIMIT"
                break
            if len(completed) % 50 == 0:
                print(f"{len(completed)} checkpointed rows", flush=True)
    finally:
        if proc is not None:
            proc.terminate()
            proc.join()
    selected = [
        completed[(q["id"], m)]
        for q in chosen
        for m in q["allowed_methods"]
        if (q["id"], m) in completed
    ]
    report = {
        "suite": suite,
        "selected_questions": count,
        "completed_questions": sum(
            all((q["id"], m) in completed for m in q["allowed_methods"]) for q in chosen
        ),
        "rows": len(selected),
        "http_200": sum(r.get("http_status") == 200 for r in selected),
        "nonempty": sum(r.get("evidence_count", 0) > 0 for r in selected),
        "errors": sum(bool(r.get("technical_error")) for r in selected),
        "seconds_this_invocation": round(time.monotonic() - start, 3),
        "aborted": aborted,
        "model_calls": 0,
        "human_gold": False,
        "confirmation_executed": 0,
        "free_bytes": shutil.disk_usage(".").free,
    }
    (output / f"REPORT-{suite}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--suite", choices=SUITES, default="smoke")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--categories", nargs="*", default=[])
    a = p.parse_args()
    run(a.catalog, a.manifest, a.output, a.suite, a.seconds, categories_filter=a.categories)
