"""Deterministic, provenance-preserving pooling; no inferred relevance labels."""

import hashlib
import json
from typing import Any

QREL_CONTRACT = "retrieval-qrels-v1"
LABELS = [
    "sufficient",
    "partial",
    "contextual",
    "irrelevant",
    "contradictory",
    "not_checkable",
    "unclear",
]
SEARCH = ["not_searched", "not_found_in_searched_scope", "found_additional", "unclear"]


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def evidence_key(row: dict) -> str:
    if not row.get("source_sha256") or not row.get("locator") or not row.get("text"):
        raise ValueError("Source version, original locator and actual text required")
    actual = hashlib.sha256(row["text"].encode()).hexdigest()
    if row.get("text_sha256", actual) != actual:
        raise ValueError("Evidence text identity mismatch")
    return "evidence:" + digest([row["source_sha256"], row["locator"], actual])


def union_pool(task: dict, seeds: list[dict], runs: list[dict], seed="d1-blind-v1") -> dict:
    """Only exact query/snapshot/access matches; contributor order cannot affect output."""
    members: dict[str, dict] = {}
    for run in runs:
        if run.get("task_id") != task["task_id"]:
            raise ValueError("Task identity mapping must be verified explicitly")
        if (
            task.get("retrieval_class")
            not in {"document", "passage", "bundle", "fact", "multi_hop"}
            or run.get("retrieval_class") != task["retrieval_class"]
        ):
            raise ValueError("Incompatible retrieval granularity")
        if not task.get("verified_evidence_ids"):
            raise ValueError("A separate verified evidence catalogue is required for method runs")
        if not task.get("query_frozen"):
            raise ValueError("An independent query must be frozen before adding method runs")
        for key in ["query_sha256", "snapshot_id", "evidence_access_sha256"]:
            if not task.get(key) or run.get(key) != task[key]:
                raise ValueError("Incompatible run: " + key)
        if (
            not run.get("method")
            or not run.get("run_id")
            or not run.get("method_config")
            or not run.get("information_condition")
        ):
            raise ValueError("Explicit method/run provenance required")
    inputs: list[tuple[dict, dict[str, Any]]] = [(row, {"kind": "focal_seed"}) for row in seeds]
    for run in runs:
        for rank, row in enumerate(run["candidates"], 1):
            inputs.append(
                (
                    row,
                    {
                        "kind": "retriever",
                        "method": run["method"],
                        "run_id": run["run_id"],
                        "run_sha256": digest(run),
                        "rank": rank,
                        "information_condition": run.get("information_condition", "UNASSESSED"),
                    },
                )
            )
    for row, origin in inputs:
        key = evidence_key(row)
        if origin["kind"] == "retriever" and key not in task["verified_evidence_ids"]:
            raise ValueError("Candidate has no verified source/locator/text identity mapping")
        if row["source_sha256"] not in task["allowed_original_sha256"]:
            raise ValueError("Candidate outside frozen evidence access")
        if key not in members:
            members[key] = {
                "id": key,
                "source_sha256": row["source_sha256"],
                "locator": row["locator"],
                "text": row["text"],
                "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                "aliases": [],
                "source_urls": [],
                "origins": [],
                "qrel": None,
            }
        member = members[key]
        if row.get("source_url") and row["source_url"] not in member["source_urls"]:
            member["source_urls"].append(row["source_url"])
        alias = row.get("evidence_id")
        if alias and alias not in member["aliases"]:
            member["aliases"].append(alias)
        if origin not in member["origins"]:
            member["origins"].append(origin)
    for member in members.values():
        member["origins"].sort(key=digest)
        member["aliases"].sort()
        member["source_urls"].sort()
    ordered = sorted(members.values(), key=lambda row: digest([seed, task["task_id"], row["id"]]))
    return {
        "task_id": task["task_id"],
        "query_sha256": task.get("query_sha256"),
        "snapshot_id": task["snapshot_id"],
        "candidates": ordered,
        "status": "UNJUDGED_UNION" if runs else "FOCAL_ONLY_NOT_METHOD_POOL",
        "method_count": len({r["method"] for r in runs}),
        "human_qrels": 0,
    }


def reviewer_candidates(pool: dict) -> list[dict]:
    """Administrative ranks, contributions, scores and labels never enter reviewer view."""
    return [
        {
            "id": r["id"],
            "source_sha256": r["source_sha256"],
            "text": r["text"],
            "page": r["locator"].get("page"),
            "heading": r["locator"].get("heading"),
            "locator": r["locator"],
            "source_urls": r["source_urls"],
        }
        for r in pool["candidates"]
    ]


def validate_qrels(payload: dict, task: dict) -> None:
    ids = {r["id"] for r in task["pool"]}
    relevance = payload.get("relevance", {})
    if set(relevance) != ids or any(v not in LABELS for v in relevance.values()):
        raise ValueError(
            "All candidates require explicit six-state decisions; missing is not negative"
        )
    if payload.get("search_status") not in SEARCH:
        raise ValueError("Explicit follow-up search status required")
    if (
        any(v in ("not_checkable", "unclear") for v in relevance.values())
        and not payload.get("comment", "").strip()
    ):
        raise ValueError("Nichtprüfbarkeit oder fachliche Unklarheit kurz begründen.")
    if (
        payload["search_status"] != "not_searched"
        and not payload.get("missing_evidence", "").strip()
    ):
        raise ValueError("Record searched scope, locations and findings")
    seen = set()
    for group in payload.get("qrel_groups", []):
        alternatives = group.get("alternatives", [])
        if (
            not group.get("aspect", "").strip()
            or not alternatives
            or len(set(alternatives)) != len(alternatives)
        ):
            raise ValueError("Evidence groups need an aspect and distinct alternatives")
        if not set(alternatives) <= ids or any(
            relevance[x] not in ("sufficient", "partial", "contradictory") for x in alternatives
        ):
            raise ValueError("Groups require reviewable candidates from this frozen pool")
        identity = digest([group["aspect"], sorted(alternatives)])
        if identity in seen:
            raise ValueError("Duplicate necessary evidence group")
        seen.add(identity)
    if (
        task.get("retrieval_class") in ("bundle", "multi_hop")
        and payload.get("complete") == "yes"
        and not seen
    ):
        raise ValueError("Bundle completeness requires explicit necessary evidence groups")
