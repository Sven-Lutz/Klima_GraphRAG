"""Bounded D1 preparation from frozen cases and existing results; no retriever execution."""

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .contract import QREL_CONTRACT, digest, reviewer_candidates, union_pool

ROOT = Path(__file__).resolve().parents[3]


def guard():
    if shutil.disk_usage(ROOT).free < 18 * 1024**3 + 32 * 1024**2:
        raise RuntimeError("D1 write gate: 18 GiB plus 32 MiB required")


def bounded_inventory(path):
    # Cold/offloaded historical files must not block a research preparation indefinitely.
    code = """import hashlib,json,sys
p=sys.argv[1]
with open(p,'rb') as f: raw=f.read(8*1024**2+1)
if len(raw)>8*1024**2: raise ValueError('8 MiB inventory cap')
rows=[json.loads(x) for x in raw.splitlines() if x]
result={'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(rows)}
result['methods']=sorted({str(r['method']) for r in rows if 'method' in r})
result['task_ids']=sorted({str(r.get('task_id',r.get('task')))
 for r in rows if 'task_id' in r or 'task' in r})
print(json.dumps(result))
"""
    try:
        p = subprocess.run(
            [sys.executable, "-B", "-c", code, str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return json.loads(p.stdout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"read_status": "NOT_AVAILABLE_BOUNDED_METADATA_READ_FAILED"}


def build(study, output, release):
    guard()
    if output.exists() or release.exists():
        raise ValueError("Choose fresh D1 directories; preserve existing releases")
    phase_c = ROOT / "research/gold_studio/bavaria_study/phase_c"
    manifest = json.loads((phase_c / "MANIFEST.json").read_text())
    for name, record in manifest["files"].items():
        if hashlib.sha256((phase_c / name).read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Frozen Phase C changed: " + name)
    cases = json.loads((study / "cases.json").read_text())
    if (
        hashlib.sha256((study / "cases.json").read_bytes()).hexdigest()
        != json.loads((phase_c / "AUDIT_RECEIPT.json").read_text())["cases_sha256"]
    ):
        raise ValueError("Frozen case identity mismatch")
    cases = [t for t in cases if t["primary_purpose"] == "retrieval"]
    assert len(cases) == 90
    snapshot = json.loads((study / "REPORT.json").read_text())["snapshot_id"]
    pools, tasks, assessments = [], [], []
    for t in cases:
        focus = t["focus"]
        task = {
            "task_id": t["task_id"],
            "query": None,
            "query_frozen": False,
            "snapshot_id": snapshot,
            "allowed_original_sha256": t["allowed_original_sha256"],
            "evidence_access_sha256": digest(t["allowed_original_sha256"]),
        }
        pool = union_pool(
            task,
            [
                {
                    "source_sha256": t["focal_source_sha256"],
                    "evidence_id": focus["unit_id"],
                    "source_url": t["source_record"]["url"],
                    "text": focus["text"],
                    "locator": {
                        "ordinal": focus["ordinal"],
                        "page": focus.get("page_ref"),
                        "heading": focus.get("heading"),
                    },
                }
            ],
            [],
        )
        pools.append(pool)
        tasks.append(
            {
                **{
                    k: t[k]
                    for k in [
                        "task_id",
                        "question",
                        "allowed_original_sha256",
                        "focus",
                        "focal_source_sha256",
                    ]
                },
                "pool": reviewer_candidates(pool),
                "qrel_contract": QREL_CONTRACT,
                "retrieval_class": "passage_context",
                "required_stages": ["retrieval"],
                "pool_status": "FOCAL_UNIT_ONLY_NOT_MULTI_SYSTEM_POOL",
            }
        )
        assessments.append(
            {
                "task_id": t["task_id"],
                "ags": t["ags"],
                "region": t["region"],
                "current_question": t["question"],
                "eligible_class": "passage_context_review",
                "independent_information_need": False,
                "document_query_ready": False,
                "passage_query_ready": False,
                "bundle_ready": False,
                "fact_ready": False,
                "multi_hop_ready": False,
                "pool_candidates": 1,
                "method_runs": 0,
                "human_qrels": 0,
                "suitable_comparison_pool": False,
                "reason": "FOCAL_SELF_REFERENCE;INDEPENDENT_QUERY_AND_EVIDENCE_ACCESS_NOT_FROZEN",
            }
        )
    paths: set[Path] = set()
    for parent in [
        ROOT / "research/bavaria/release",
        ROOT / "research/bavaria/continuation/release",
        ROOT / "research/bavaria/completion/release",
    ]:
        paths.update(parent.glob("**/results.jsonl"))
    # Inspect known local mirrors, not raw payloads, models, or the entire filesystem.
    for parent in [
        ROOT / "generated/bavaria/study01",
        ROOT / "generated/bavaria/study03",
        ROOT / "generated/bavaria/completion/retrieval03",
        ROOT / "generated/bavaria/continuation/relational",
    ]:
        paths.update(parent.glob("**/results.jsonl"))
    inventory = []
    for path in sorted(paths):
        guard()
        data = bounded_inventory(path)
        methods = data.get("methods", [])
        if methods and not any(
            any(
                x in m.lower()
                for x in ["bm25", "semantic", "hybrid", "sql", "graph", "rrf", "tool", "structured"]
            )
            for m in methods
        ):
            continue
        inventory.append(
            {
                "path": str(path.relative_to(ROOT)),
                **data,
                "classification": "NOT_AVAILABLE" if "read_status" in data else "INCOMPATIBLE",
                "reason": "NO_VERIFIED_EXACT_QUERY_SNAPSHOT_ACCESS_MAPPING_TO_NEW_90",
                "exact_new_task_id_overlap": len(
                    set(data.get("task_ids", [])) & {t["task_id"] for t in cases}
                ),
                "query_snapshot_mapping_verified": False,
            }
        )
        print("inventoried", path.relative_to(ROOT), flush=True)
    for family in ["BM25", "DENSE", "HYBRID_RRF", "SQL", "GRAPH", "STRUCTURED_EVIDENCE_CARDS"]:
        inventory.append(
            {
                "path": "expected:new90:" + family,
                "classification": "NOT_AVAILABLE",
                "reason": "NO_FROZEN_INDEPENDENT_QUERY_RUN_ON_THIS_ACCESS_FRAME",
            }
        )
    inventory.extend(
        [
            {
                "path": str(study.relative_to(ROOT)) + "/cases.json",
                "classification": "DIRECTLY_REUSABLE",
                "reason": "FROZEN_FOCAL_CANDIDATES_ONLY_NOT_METHOD_RESULTS",
            },
            {
                "path": "research/bavaria/completion/release/federated-workspace.json",
                "classification": "REUSABLE_AFTER_VERIFIED_MAPPING",
                "reason": "EXECUTION_RESOURCE;VERIFY_SNAPSHOT_ACCESS_MAPPING",
            },
            {
                "path": "research/bavaria/completion/release/retrieval03/embedding-manifest.json",
                "classification": "INCOMPATIBLE",
                "reason": "PHASE_C_ZERO_INPUT_OVERLAP;OLD_38_QUERIES_ONLY",
            },
            {
                "path": "generated/bavaria/completion/relations02/evidence.sqlite",
                "classification": "INCOMPATIBLE",
                "reason": "PHASE_C_ZERO_SOURCE_OVERLAP;PROVISIONAL_CURATED_INFORMATION",
            },
        ]
    )
    guard()
    output.mkdir(parents=True)
    release.mkdir(parents=True)
    (output / "admin_pools.json").write_text(json.dumps(pools, ensure_ascii=False, indent=2))
    (output / "reviewer_tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2))
    with (release / "TASK_SUITABILITY.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(assessments[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(assessments)
    (release / "METHOD_COMPATIBILITY.json").write_text(json.dumps(inventory, indent=2))
    with (release / "METHOD_COMPATIBILITY.csv").open("w", newline="") as f:
        columns = ["path", "classification", "methods", "rows", "sha256", "reason"]
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {
                key: ",".join(row.get(key, [])) if key == "methods" else row.get(key, "")
                for key in columns
            }
            for row in inventory
        )
    public_pools = [
        {**p, "candidates": [{k: v for k, v in r.items() if k != "text"} for r in p["candidates"]]}
        for p in pools
    ]
    result = {
        "schema": "d1-candidate-pool-manifest-v1",
        "snapshot_id": snapshot,
        "phase_c_manifest_sha256": hashlib.sha256(
            (phase_c / "MANIFEST.json").read_bytes()
        ).hexdigest(),
        "cases": 90,
        "focal_only": 90,
        "suitable_comparison_pools": 0,
        "independent_queries_frozen": 0,
        "human_qrels": 0,
        "private_reviewer_tasks_sha256": digest(tasks),
        "private_admin_pools_sha256": digest(pools),
        "query_execution": "NOT_STARTED",
        "pooling_seed": "d1-blind-v1",
        "pools": public_pools,
    }
    (release / "CANDIDATE_POOL_MANIFEST.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print("D1 prepared: 90 focal pools, 0 suitable method comparison pools, 0 Human Qrels")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    args = parser.parse_args()
    build(args.study.resolve(), args.output.resolve(), args.release.resolve())
