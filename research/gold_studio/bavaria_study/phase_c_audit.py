"""Bounded, descriptive readiness audit. Never creates reference labels or reviews."""

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

from .analysis import dependency_graph
from .inventory import normalized_url
from .reproduce import reproduce

ROOT = Path(__file__).resolve().parents[3]
FLOOR = 18 * 1024**3


def guard():
    if shutil.disk_usage(ROOT).free < FLOOR + 32 * 1024**2:
        raise RuntimeError("Phase C: below 18 GiB plus bounded audit allowance; no audit writes")


def load(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def audit(study, output):
    guard()
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Audit output must be a new directory; preserve prior receipts")
    cases = load(study / "cases.json")
    historical = load(
        ROOT / "generated/bavaria/completion/independent-review-final/source_manifest.json"
    )
    exposure = load(ROOT / "research/gold_studio/bavaria_study/release/v2/HISTORICAL_EXPOSURE.json")
    links = {r["task_id"]: r for r in exposure["links"]}
    results_path = ROOT / "research/bavaria/completion/release/retrieval03/results.jsonl"
    results = [json.loads(line) for line in results_path.read_text().splitlines() if line]
    result_tasks = {r["task_id"] for r in results}
    evidence = {e for r in results for e in r["selected_evidence"]}
    manifest = load(ROOT / "research/bavaria/completion/release/federated-workspace.json")
    ledgers = [manifest["artifacts"]["ledger"]["path"], *manifest["source_ledger_order"]]
    connections = [sqlite3.connect(Path(p).as_uri() + "?mode=ro", uri=True) for p in ledgers]
    frame = {
        ags: json.loads(record)
        for ags, record in connections[0].execute("SELECT ags,record FROM municipalities")
    }
    hist_sha = {r["sha256"] for r in historical}
    dep = dependency_graph(cases)
    component = {tid: key for key, ids in dep["components"].items() for tid in ids}
    rows, url_links = [], []
    originals_read = 0
    for t in cases:
        guard()
        s, f = t["source_record"], t["focus"]
        archive = Path(s["raw_path"])
        status, actual, size = "MISSING", "", 0
        if archive.is_file():
            h = hashlib.sha256()
            try:
                opener = gzip.open if archive.suffix == ".gz" else open
                with opener(archive, "rb") as raw:
                    for chunk in iter(lambda: raw.read(1024**2), b""):
                        h.update(chunk)
                        size += len(chunk)
                        if size > s["raw_bytes"]:
                            raise ValueError("Decompressed original exceeds recorded byte count")
                actual = h.hexdigest()
                status = (
                    "VERIFIED"
                    if actual == t["focal_source_sha256"] and size == s["raw_bytes"]
                    else "MISMATCH"
                )
                originals_read += size
            except (OSError, EOFError, ValueError) as exc:
                status = "READ_ERROR:" + type(exc).__name__
        focus_binding = f["sha256"] == t["focal_source_sha256"] and bool(f.get("text", "").strip())
        stored_units = [
            row
            for conn in connections
            for row in conn.execute(
                "SELECT sha256,text FROM units WHERE unit_id=?", (f["unit_id"],)
            )
        ]
        ledger_binding = bool(stored_units) and all(
            row == (f["sha256"], f["text"]) for row in stored_units
        )
        same_url = [r for r in historical if normalized_url(r["url"]) == normalized_url(s["url"])]
        if same_url:
            url_links.append(
                {
                    "task_id": t["task_id"],
                    "new_url": s["url"],
                    "historical": same_url,
                    "exact_raw_url_match": any(r["url"] == s["url"] for r in same_url),
                }
            )
        reasons = [
            "AUTHORITY_SCOPE_DOCUMENT_DATE_REQUIRE_ORIGINAL_REVIEW",
            "PROJECT_IDENTITY_UNRESOLVED",
            "OTHER_EXPOSURE_UNASSESSED",
            "NO_HUMAN_REFERENCE",
        ]
        if t["primary_purpose"] in ("fields", "relation"):
            reasons.append(
                "OPEN_EXTRACTION_NOT_FIXED_ATOMIC_CLAIM;DEFINE_ANNOTATION_UNIVERSE_BEFORE_RECALL"
            )
        if t["primary_purpose"] == "retrieval":
            reasons.append("FOCAL_ONLY_NO_METHOD_POOL_NO_INDEPENDENT_INFORMATION_NEED")
        if f.get("kind") in ("heading", "inherited_source_order"):
            reasons.append("CONTEXT_LOCATOR_REVIEW_REQUIRED")
        if not focus_binding or not ledger_binding or status != "VERIFIED":
            reasons.append("INTEGRITY_OR_AVAILABILITY_BLOCK")
        loc = f.get("page_ref") or ("unit:" + f["unit_id"] + ";ordinal:" + str(f["ordinal"]))
        rows.append(
            {
                "task_id": t["task_id"],
                "primary_purpose": t["primary_purpose"],
                "question": t["question"],
                "ags": t["ags"],
                "region": t["region"],
                "source_sha256": t["focal_source_sha256"],
                "source_url": s["url"],
                "locator": loc,
                "focus_kind": f["kind"],
                "focus_source_binding": focus_binding,
                "ledger_focus_exact_match": ledger_binding,
                "official_frame_ags_present": t["ags"] in frame,
                "focus_characters": len(f["text"]),
                "semantic_original_fidelity": "REQUIRES_ORIGINAL_REVIEW",
                "source_authority": s["authority"],
                "claim_scope": s["claim_scope"],
                "document_format": s["format"],
                "document_type": s["document_type"],
                "publication_date": s["publication_date"],
                "temporal_cues_not_dates": "|".join(
                    sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", f["text"])))
                ),
                "prior_exposure": s["other_development_exposure"],
                "historical_frame_overlap": links[t["task_id"]]["prior_municipality_frame_overlap"],
                "historical_url_path_overlap": bool(same_url),
                "historical_source_sha_overlap": s["sha256"] in hist_sha,
                "known_link_component": component[t["task_id"]],
                "dependency_keys": "|".join(t["components"]),
                "project_disjointness": "NOT_ESTABLISHED",
                "original_availability": status,
                "recomputed_sha256": actual,
                "read_bytes": size,
                "annotation_readiness": "CANDIDATE_REQUIRES_SCIENTIFIC_QUALIFICATION"
                if status == "VERIFIED" and focus_binding and ledger_binding
                else "BLOCKED_INTEGRITY",
                "review_reason": "|".join(reasons),
                "pool_candidates": len(t["pool"]),
                "saved_same_task_runs": sum(r["task_id"] == t["task_id"] for r in results),
                "saved_focal_evidence_id_match": f["unit_id"] in evidence,
                "saved_retrievers_for_this_task": "NONE"
                if t["task_id"] not in result_tasks
                else "SEE_SAVED_RESULTS",
                "pool_status": t["pool_status"],
                "missing_evidence_groups": "NOT_YET_HUMAN_INVENTORIED",
                "gold_state": "NOT_REVIEWED",
            }
        )
    for conn in connections:
        conn.close()
    reproduction = reproduce(study, ROOT / "generated/bavaria/acquisition.sqlite")
    receipt = {
        "schema": "phase-c-readiness-audit-v1",
        "human_labels_created": 0,
        "study_report_sha256": sha(study / "REPORT.json"),
        "cases_sha256": sha(study / "cases.json"),
        "historical_source_manifest_sha256": sha(
            ROOT / "generated/bavaria/completion/independent-review-final/source_manifest.json"
        ),
        "saved_results_sha256": sha(results_path),
        "reproduction": reproduction,
        "cases": len(rows),
        "municipality_frames": len({r["ags"] for r in rows}),
        "original_versions": len({r["source_sha256"] for r in rows}),
        "original_checks": dict(Counter(r["original_availability"] for r in rows)),
        "exact_focus_ledger_matches": sum(r["ledger_focus_exact_match"] for r in rows),
        "official_frame_ags_matches": sum(r["official_frame_ags_present"] for r in rows),
        "original_uncompressed_bytes_streamed": originals_read,
        "families": dict(Counter(r["primary_purpose"] for r in rows)),
        "regions": dict(Counter(r["region"] for r in rows)),
        "known_link_components": dep["component_count"],
        "host_proxy_components": dependency_graph(cases, True)["component_count"],
        "historical_frame_overlaps": sum(r["historical_frame_overlap"] for r in rows),
        "historical_sha_overlaps": sum(r["historical_source_sha_overlap"] for r in rows),
        "historical_url_links": url_links,
        "saved_results_rows": len(results),
        "saved_result_tasks": len(result_tasks),
        "saved_methods": sorted({r["method"] for r in results}),
        "new_retrieval_task_run_matches": sum(
            r["saved_same_task_runs"] for r in rows if r["primary_purpose"] == "retrieval"
        ),
        "new_retrieval_focal_id_matches": sum(
            r["saved_focal_evidence_id_match"] for r in rows if r["primary_purpose"] == "retrieval"
        ),
        "free_bytes_after": shutil.disk_usage(ROOT).free,
        "semantic_source_entailment": "NOT_CERTIFIED_BY_HASH_OR_LEXICAL_SCREEN",
    }
    guard()
    output.mkdir(parents=True)
    with (output / "TASK_READINESS.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (output / "AUDIT_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    receipt = audit(args.study, args.output)
    print(
        json.dumps(
            {k: v for k, v in receipt.items() if k != "historical_url_links"},
            ensure_ascii=False,
            indent=2,
        )
    )
