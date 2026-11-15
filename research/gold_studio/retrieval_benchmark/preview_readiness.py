"""Prospective neutral queries and bounded development-only lexical pool pilot.

No original downloads, embeddings, generation, human labels, or historical mutations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import time
from pathlib import Path

from research.bavaria.federation import source_connection
from research.bavaria.workspace.core import Workspace
from research.gold_studio.retrieval_benchmark.contract import (
    digest,
    evidence_key,
    reviewer_candidates,
    union_pool,
)

ROOT = Path(__file__).resolve().parents[3]
TOPICS = (
    "Klimaschutzziele",
    "Wärmeplanung",
    "Klimaanpassung",
    "Photovoltaik",
    "Mobilität",
    "Förderprogramme",
)


def guard():
    if shutil.disk_usage(ROOT).free < 18 * 1024**3 + 16 * 1024**2:
        raise RuntimeError("18 GiB plus 16 MiB output allowance required")


def write(path, value):
    guard()
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(raw.encode()) > 4 * 1024**2:
        raise RuntimeError("4 MiB per artifact cap")
    path.write_text(raw)


def workspace(manifest):
    binding = json.loads(manifest.read_text())
    return Workspace(
        binding["artifacts"]["ledger"]["path"],
        relations_db=binding["artifacts"]["relations"]["path"],
        manifest=manifest,
        source_databases=binding["source_ledger_order"],
        disposition_db=binding["disposition_ledger"],
    )


def prepare(study, manifest, output):
    guard()
    if output.exists():
        raise FileExistsError("Freeze outputs are immutable; choose a fresh directory")
    cases_path = study / "cases.json"
    cases = json.loads(cases_path.read_text())
    cases = sorted(
        (t for t in cases if t["primary_purpose"] == "retrieval"), key=lambda t: t["task_id"]
    )
    if len(cases) != 90:
        raise ValueError("Expected frozen 90 parent retrieval candidates")
    w = workspace(manifest)
    conn = source_connection(w.db, w.source_databases)
    try:
        shas = [r[0] for r in conn.execute("SELECT sha256 FROM documents ORDER BY sha256")]
    finally:
        conn.close()
    # Metadata-only component union: source, URL, text-span, and municipality.
    # Project/institution identities remain unresolved, explicitly not certified.
    groups = [{*t["components"], "municipality:" + t["ags"]} for t in cases]
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i):
                if groups[i] & groups[j] and groups[i] != groups[j]:
                    merged = groups[i] | groups[j]
                    groups[i] = groups[j] = merged
                    changed = True
    queries = []
    for task, group in zip(cases, groups, strict=True):
        component = digest(sorted(group))
        topic = TOPICS[int(digest([task["ags"], task["task_id"]])[:8], 16) % len(TOPICS)]
        name = task["source_record"]["name"]
        query = f"Welche Originalpassagen beschreiben {topic} in {name}?"
        queries.append(
            {
                "task_id": task["task_id"],
                "query_revision": "neutral-needs-v1",
                "query": query,
                "query_sha256": digest(query),
                "query_frozen": True,
                "ags": task["ags"],
                "region": task["region"],
                "topic": topic,
                "retrieval_class": "passage",
                "component_id": component,
                "split": "development"
                if int(component[:8], 16) % 4 == 0
                else "quarantined_prospective",
                "human_relevance": None,
                "project_disjointness": "UNRESOLVED",
            }
        )
    access = {
        "snapshot_id": w.snapshot_id,
        "allowed_original_sha256": shas,
        "frame_filter": "Explicit AGS, equally supplied to every method; not claim scope",
        "text_access": "All archived units within associated frame; no hand-selected answers",
        "structured_information": "Extra curated relations require separate information-effect arm",
        "source_rights": "LOCAL_ONLY_UNCLEARED",
    }
    output.mkdir(parents=True)
    write(output / "ACCESS.json", access)
    write(output / "QUERIES.json", queries)
    freeze = {
        "schema": "preview-retrieval-freeze-v1",
        "parent_cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "snapshot_id": w.snapshot_id,
        "access_sha256": digest(access),
        "queries_sha256": digest(queries),
        "query_count": len(queries),
        "known_components": len({q["component_id"] for q in queries}),
        "development": sum(q["split"] == "development" for q in queries),
        "confirmation_release": "BLOCKED_EXPOSURE_AND_PROJECT_AUDIT_AND_HUMAN_QRELS",
        "selection": "Purposive source-conditioned parent frame, not population sampling",
        "hypothesis": (
            "Metadata-context BM25 supplies unjudged passages for neutral information needs"
        ),
        "top_k": 10,
        "pilot_query_limit": 21,
        "wall_budget_seconds": 60,
        "maximum_output_bytes": 16 * 1024**2,
        "software_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method_implementation_sha256": hashlib.sha256(
            (ROOT / "research/bavaria/workspace/core.py").read_bytes()
        ).hexdigest(),
        "all_reference_labels": "MISSING_NOT_NEGATIVE",
    }
    write(output / "FREEZE.json", freeze)
    return freeze


def pilot(manifest, frozen, output, prior=None):
    guard()
    if output.exists():
        raise FileExistsError("Pilot receipts are append-only new run directories")
    access = json.loads((frozen / "ACCESS.json").read_text())
    queries = json.loads((frozen / "QUERIES.json").read_text())
    freeze = json.loads((frozen / "FREEZE.json").read_text())
    if digest(access) != freeze["access_sha256"] or digest(queries) != freeze["queries_sha256"]:
        raise ValueError("Freeze identity mismatch")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != freeze["software_sha256"]:
        raise ValueError("Runner changed after freeze")
    start = time.monotonic()
    implementation = ROOT / "research/bavaria/workspace/core.py"
    if (
        hashlib.sha256(implementation.read_bytes()).hexdigest()
        != freeze["method_implementation_sha256"]
    ):
        raise ValueError("Method implementation changed after freeze")
    w = workspace(manifest)
    if w.snapshot_id != freeze["snapshot_id"]:
        raise ValueError("Wrong snapshot")
    output.mkdir(parents=True)
    prior_runs = {}
    if prior:
        prior_runs = {r["task_id"]: r for r in json.loads((prior / "RUNS.json").read_text())}
    runs = []
    reused = 0
    pools = []
    verified = []
    conn = source_connection(w.db, w.source_databases)
    try:
        for q in [q for q in queries if q["split"] == "development"][: freeze["pilot_query_limit"]]:
            guard()
            if time.monotonic() - start > freeze["wall_budget_seconds"]:
                break
            at = time.monotonic()
            previous = prior_runs.get(q["task_id"])
            if previous:
                for key, expected in [
                    ("query_sha256", q["query_sha256"]),
                    ("snapshot_id", w.snapshot_id),
                    ("evidence_access_sha256", freeze["access_sha256"]),
                ]:
                    if previous[key] != expected:
                        raise ValueError("Prior run identity mismatch: " + key)
                if (
                    previous["method_config"]
                    != {
                        "k": freeze["top_k"],
                        "implementation_sha256": freeze["method_implementation_sha256"],
                    }
                    or previous["candidates"]
                ):
                    raise ValueError("Only exact empty pilot runs are reused by this continuation")
                results = []
                reused += 1
            else:
                results = w.discover(q["query"], [q["ags"]], k=freeze["top_k"])["results"]
            candidates = []
            for item in results:
                row = conn.execute(
                    "SELECT * FROM units WHERE unit_id=?", (item["unit_id"],)
                ).fetchone()
                if not row or row["sha256"] != item["sha256"] or row["text"] != item["text"]:
                    raise ValueError("Independent ledger mapping failed")
                candidate = {
                    "evidence_id": row["unit_id"],
                    "source_sha256": row["sha256"],
                    "text": row["text"],
                    "source_url": item["source_url"],
                    "locator": {
                        "ordinal": row["ordinal"],
                        "page": row["page_ref"],
                        "heading": row["heading"],
                    },
                }
                candidates.append(candidate)
                verified.append(
                    {
                        "evidence_key": evidence_key(candidate),
                        "unit_id": row["unit_id"],
                        "source_sha256": row["sha256"],
                        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                    }
                )
            run = {
                "task_id": q["task_id"],
                "query_sha256": q["query_sha256"],
                "snapshot_id": w.snapshot_id,
                "evidence_access_sha256": freeze["access_sha256"],
                "retrieval_class": "passage",
                "method": "context_bm25",
                "run_id": "preview-dev-bm25-v1:" + q["task_id"],
                "method_config": {
                    "k": 10,
                    "implementation_sha256": freeze["method_implementation_sha256"],
                },
                "information_condition": (
                    "question+explicit-AGS+all-associated-text; no curated relations"
                ),
                "seconds": round(time.monotonic() - at, 6),
                "candidates": candidates,
            }
            if previous:
                run = previous
            task = {
                **q,
                "snapshot_id": w.snapshot_id,
                "evidence_access_sha256": freeze["access_sha256"],
                "allowed_original_sha256": access["allowed_original_sha256"],
                "verified_evidence_ids": [evidence_key(c) for c in candidates],
            }
            # No focal answer seed is presumed relevant; pilot includes only actual run candidates.
            if candidates:
                pool = union_pool(task, [], [run])
            else:
                pool = {
                    "task_id": q["task_id"],
                    "candidates": [],
                    "human_qrels": 0,
                    "status": "EMPTY_UNJUDGED_RUN",
                    "method_count": 1,
                }
            pools.append(pool)
            runs.append(run)
    finally:
        conn.close()
    write(output / "RUNS.json", runs)
    write(output / "POOLS_INTERNAL.json", pools)
    write(
        output / "POOLS_BLIND.json",
        [{"task_id": p["task_id"], "pool": reviewer_candidates(p)} for p in pools],
    )
    write(output / "VERIFIED_MAPPING.json", verified)
    receipt = {
        "query_results": len(runs),
        "new_queries_executed": len(runs) - reused,
        "reused_empty_pilot_runs": reused,
        "human_qrels": 0,
        "method_families": 1,
        "seconds": round(time.monotonic() - start, 3),
        "process_maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "remaining_free_bytes": shutil.disk_usage(ROOT).free,
        "confirmation_queries_executed": 0,
        "full_multi_method_pools": 0,
        "candidate_count": sum(len(p["candidates"]) for p in pools),
        "metrics": "NOT_IDENTIFIABLE_WITHOUT_HUMAN_QRELS",
        "budget_note": "60s checked between calls; per-call interruption is not claimed",
    }
    write(output / "RECEIPT.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "pilot"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--study", type=Path, default=Path("generated/gold-studio-bavaria-candidates-v2")
    )
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.study, args.manifest, args.output)
            if args.action == "prepare"
            else pilot(args.manifest, args.frozen, args.output, args.prior),
            ensure_ascii=False,
            indent=2,
        )
    )
