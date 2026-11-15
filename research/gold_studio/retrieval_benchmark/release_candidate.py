"""Development-only RC diagnosis. Cue matches are diagnostic candidates, never Qrels."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

from crawler.web.bavaria import _original_bytes, archive_roots
from research.bavaria.federation import source_connection
from research.bavaria.workspace.core import roots
from research.gold_studio.retrieval_benchmark.contract import digest
from research.gold_studio.retrieval_benchmark.preview_readiness import workspace

CUES = {
    "Klimaschutzziele": ["klimaschutz", "klimaneutral", "treibhausgas", "co2", "ziel"],
    "Wärmeplanung": ["wärmeplan", "waermeplan", "wärmenetz", "fernwärme", "wärmeversorgung"],
    "Klimaanpassung": ["anpassung", "hochwasser", "starkregen", "hitze", "entsiegel"],
    "Photovoltaik": ["photovolta", "solar", "pv-anlag"],
    "Mobilität": ["mobil", "verkehr", "radweg", "öpnv", "bus"],
    "Förderprogramme": ["förder", "zuschuss", "finanz", "programm"],
}


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_json(path):
    code = (
        "import json,sys; b=open(sys.argv[1],'rb').read(2*1024**2+1); "
        "assert len(b)<=2*1024**2; print(json.dumps(json.loads(b)))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return {"status": "READ", "metadata": json.loads(result.stdout)}
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"status": "NOT_AVAILABLE_BOUNDED_READ", "path": str(path)}


def run(output, manifest):
    if output.exists():
        raise FileExistsError("Use a new RC evidence directory")
    if shutil.disk_usage(".").free < 18 * 1024**3 + 16 * 1024**2:
        raise RuntimeError("Insufficient measured output headroom")
    release = Path("research/gold_studio/retrieval_benchmark/release/preview")
    frozen = json.loads((release / "FREEZE.json").read_text())
    all_queries = json.loads((release / "QUERIES.json").read_text())
    if digest(all_queries) != frozen["queries_sha256"]:
        raise ValueError("Frozen query identity mismatch")
    # Filter before inspecting source records or persisting diagnostic questions.
    queries = [q for q in all_queries if q["split"] == "development"]
    if len(queries) != 21:
        raise ValueError("Development split mismatch")
    cases_path = Path("generated/gold-studio-bavaria-candidates-v2/cases.json")
    if checksum(cases_path) != frozen["parent_cases_sha256"]:
        raise ValueError("Parent candidate identity mismatch")
    ids = {q["task_id"] for q in queries}
    parents = {t["task_id"]: t for t in json.loads(cases_path.read_text()) if t["task_id"] in ids}
    previous = {
        r["task_id"]: r
        for r in json.loads(
            Path("generated/retrieval-preview-development-v1/RUNS.json").read_text()
        )
    }
    output.mkdir(parents=True)
    contract = {
        "query_sha256": frozen["queries_sha256"],
        "snapshot_id": frozen["snapshot_id"],
        "cases": sorted(ids),
        "cues": CUES,
        "purpose": "DEV_DIAGNOSTICS_NO_RELEVANCE_LABELS",
        "arms": ["frozen-context-bm25-before", "unchanged-context-bm25-current"],
        "methods_ready": False,
        "confirmation_queries_executed": 0,
        "code_sha256": checksum(Path(__file__)),
        "workspace_sha256": checksum(Path("research/bavaria/workspace/core.py")),
    }
    (output / "CONTRACT.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2))
    start = time.monotonic()
    if checksum(manifest) != frozen["manifest_sha256"]:
        raise ValueError("Selected manifest differs from frozen access")
    w = workspace(manifest)
    if w.snapshot_id != frozen["snapshot_id"]:
        raise ValueError("Snapshot mismatch")
    conn = source_connection(w.db, w.source_databases)
    diagnoses = []
    eligibility = []
    eligible_ids = {}
    try:
        for q in queries:
            if shutil.disk_usage(".").free < 18 * 1024**3:
                raise RuntimeError("Write gate reached")
            parent = parents[q["task_id"]]
            rows = list(w._unit_rows([q["ags"]]))
            eligible_ids[q["task_id"]] = {row["unit_id"] for row in rows}
            documents = [
                dict(row)
                for row in conn.execute(
                    "SELECT d.sha256,d.parse_status,count(DISTINCT u.unit_id) text_units "
                    "FROM associations a JOIN documents d USING(sha256) "
                    "LEFT JOIN units u USING(sha256) WHERE a.ags=? "
                    "GROUP BY d.sha256,d.parse_status ORDER BY d.sha256",
                    (q["ags"],),
                )
            ]
            eligibility.append(
                {
                    "task_id": q["task_id"],
                    "ags": q["ags"],
                    "associated_original_versions": len(documents),
                    "originals_without_text": [row for row in documents if not row["text_units"]],
                    "documents": documents,
                }
            )
            at = time.monotonic()
            current = w.discover(q["query"], [q["ags"]], k=10)
            elapsed = time.monotonic() - at
            old = previous[q["task_id"]]
            if old["query_sha256"] != q["query_sha256"] or old["snapshot_id"] != w.snapshot_id:
                raise ValueError("Before run mismatch")
            hits = []
            for row in current["results"]:
                hits.append(
                    {
                        "unit_id": row["unit_id"],
                        "source_sha256": row["sha256"],
                        "ordinal": row["ordinal"],
                        "page": row["page_ref"],
                        "score": row["score"],
                        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                        "matched_query_terms": sorted(
                            set(roots(row["text"])) & set(current["query_terms"])
                        ),
                    }
                )
            cues = []
            for row in rows:
                matches = [word for word in CUES[q["topic"]] if word in row["text"].casefold()]
                if matches:
                    cues.append(
                        {
                            "unit_id": row["unit_id"],
                            "source_sha256": row["sha256"],
                            "ordinal": row["ordinal"],
                            "page": row["page_ref"],
                            "cues": matches,
                            "excerpt": row["text"][:500],
                            "status": "UNJUDGED_DIAGNOSTIC_CUE",
                        }
                    )
            sha = parent["focal_source_sha256"]
            record = conn.execute(
                "SELECT raw_path,raw_bytes FROM documents WHERE sha256=?", (sha,)
            ).fetchone()
            try:
                data = _original_bytes(record, sha, archive_roots())
                original = {
                    "status": "BYTES_VERIFIED",
                    "sha256": sha,
                    "bytes": len(data),
                    "format": "PDF" if data.startswith(b"%PDF") else "HTML",
                    "semantic_review": "NOT_HUMAN_REVIEWED",
                }
            except (OSError, ValueError, RuntimeError) as exc:
                original = {"status": "NOT_CHECKABLE", "sha256": sha, "reason": type(exc).__name__}
            focal_present = any(r["sha256"] == sha for r in rows)
            before_ids = [c["evidence_id"] for c in old["candidates"]]
            diagnoses.append(
                {
                    "task_id": q["task_id"],
                    "query": q["query"],
                    "query_sha256": q["query_sha256"],
                    "ags": q["ags"],
                    "region": q["region"],
                    "topic": q["topic"],
                    "eligible_units": len(rows),
                    "eligible_originals": len({r["sha256"] for r in rows}),
                    "effective_scope": current["scope_ags"],
                    "query_terms": current["query_terms"],
                    "before_seconds": old["seconds"],
                    "before_ids": before_ids,
                    "after_seconds": round(elapsed, 6),
                    "after_hits": hits,
                    "rank_identity_equal": before_ids == [r["unit_id"] for r in current["results"]],
                    "focal_original_in_search_universe": focal_present,
                    "original": original,
                    "topical_cue_units": len(cues),
                    "cue_candidates": cues[:3],
                    "technical_scope_or_identity_failure": not focal_present
                    or current["scope_ags"] != [q["ags"]],
                    "actor_filter": "NONE",
                    "temporal_filter": "NONE",
                    "answerability": "POTENTIAL_TEXT_REQUIRES_ORIGINAL_REVIEW"
                    if cues
                    else "NOT_ESTABLISHED_IN_CHECKED_TEXT",
                    "cause": "EXACT_QUERY_TERMS_ABSENT_CUES_PRESENT"
                    if not hits and cues
                    else "NO_QUERY_TERM_OR_BROAD_TOPIC_CUE_FOUND"
                    if not hits
                    else "NONEMPTY_UNJUDGED",
                    "missing_methods": [
                        "compatible_dense",
                        "hybrid",
                        "equal_information_structured",
                    ],
                    "human_relevance": None,
                }
            )
    finally:
        conn.close()
    (output / "DEVELOPMENT_DIAGNOSIS.json").write_text(
        json.dumps(diagnoses, ensure_ascii=False, indent=2)
    )
    inventory = {}
    for name in ["embedding-manifest.json", "METHOD_FREEZE.json", "RUN.json"]:
        inventory[name] = bounded_json(Path("generated/bavaria/completion/retrieval03") / name)
    (output / "INDEX_INVENTORY.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2)
    )
    (output / "SOURCE_ELIGIBILITY.json").write_text(json.dumps(eligibility, indent=2))
    indexed = inventory["embedding-manifest.json"].get("metadata", {})
    index_ids = set(indexed.get("input_ids", []))
    coverage = [
        {
            "task_id": qid,
            "eligible_units": len(ids),
            "dense_id_overlap": len(ids & index_ids) if index_ids else None,
            "coverage_complete": ids <= index_ids if index_ids else None,
            "identity_status": "ID_OVERLAP_ONLY_NOT_VERIFIED_TEXT_MAPPING",
        }
        for qid, ids in eligible_ids.items()
    ]
    dense_report = {
        "indexed_units": len(index_ids),
        "cached_query_vectors": indexed.get("queries"),
        "dimensions": indexed.get("dimensions"),
        "cases": coverage,
        "compatible_cases": sum(row["coverage_complete"] is True for row in coverage),
        "result": "INCOMPATIBLE_FULL_ACCESS; text mapping and new query vectors also required"
        if index_ids
        else "NOT_AVAILABLE_BOUNDED_READ",
    }
    (output / "DENSE_ACCESS_COVERAGE.json").write_text(json.dumps(dense_report, indent=2))
    receipt = {
        "cases": len(diagnoses),
        "nonempty": sum(bool(d["after_hits"]) for d in diagnoses),
        "rank_equal": sum(d["rank_identity_equal"] for d in diagnoses),
        "originals_verified": sum(d["original"]["status"] == "BYTES_VERIFIED" for d in diagnoses),
        "empty_with_cues": sum(
            not d["after_hits"] and d["topical_cue_units"] > 0 for d in diagnoses
        ),
        "seconds": round(time.monotonic() - start, 3),
        "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "free_bytes": shutil.disk_usage(".").free,
        "human_qrels": 0,
        "confirmation_queries_executed": 0,
    }
    (output / "RECEIPT.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.manifest)
