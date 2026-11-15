"""Opt-in offline E1 passage adapter; never an answer/claim validator.

Returns an evidence mapping with the existing RetrievedEvidence.to_dict field
names plus explicit span/version lineage. No production retrieval call is changed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

CORPUS_SHA = "2c0de7637ce8d24c78ce8343cbb367dc01257d436c83937f892148124312e83e"
FIXED = {
    "L0_union.jsonl": "56be5bd100d0863f797fa608cdd518c9ce039962d38ca31c0b37700ef82ed8a0",
    "L1_union.jsonl": "53d6d4b1e3751d2ed2524c77acbaf61f6d8f8a2ec1582547dbe26d177a91a16a",
    "L2_L3_union.jsonl": "fabcd2937c73ffbe9cf4069caa126d61988496917adf04aac4874838f8c322b7",
    "tasks_instantiated.csv": "8f1b5de3f3e961725974320ef3b91e44fb73da648d38487ec6ae16fd47acf223",
}


class PassageBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class SQLiteSnapshot:
    path: Path
    sha256: str


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bounded_file(path, expected, maximum):
    # Hard bounds apply before parsing or opening source content.
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum or digest(body) != expected:
        raise PassageBoundaryError(f"Snapshot size/hash mismatch: {path.name}")
    return body


def context_record(item):
    """Actual context serialization, including all citation boundary fields."""
    return (
        json.dumps(
            {
                "text": item["evidence_text"],
                "heading": item["document"]["heading_context"],
                "url": item["document"]["source_url"],
                "body_sha256": item["raw_hashes"],
                "acquisition_ags": item["acquisition_ags"],
                "parent_unit_id": item["parent_unit_id"],
                "start_char": item["start_char"],
                "end_char": item["end_char"],
                "page_ref": item["page_ref"],
                "claim_supported": "NOT_ASSESSED",
                "attribution_status": "unknown",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def select_bounded_passages(
    *,
    enabled=False,
    corpus: Path,
    fixtures: Path,
    snapshots: dict[str, SQLiteSnapshot],
    task_id: str,
    acquisition_ags: str,
    first_stage="L0",
    budget_chars=8000,
    authorized_versions=None,
):
    """Select from pinned Top-10 source versions, with exact original substrings.

    Disabled calls do no I/O. Each caller must explicitly supply resolved DB
    paths AND expected snapshot hashes. No implicit DB or corpus fallback.
    authorized_versions, if supplied, must equal the fixed ranking's version set;
    it cannot grant new access. Scoring is the supplied v0.4 spans_role policy.
    """
    if not enabled:
        return dict(
            status="disabled", items=[], context="", context_chars=0, claim_supported="NOT_ASSESSED"
        )
    if type(budget_chars) is not int or not 1 <= budget_chars <= 16000:
        raise PassageBoundaryError("Context budget must be an integer in 1..16000")
    if first_stage not in ("L0", "L1", "L2", "L3"):
        raise PassageBoundaryError("Unknown fixed first-stage ranking")
    from research.experiments.e1_selector.vendor import selector, span_selector

    corpus_bytes = bounded_file(corpus, CORPUS_SHA, 4 * 1024 * 1024)
    for filename, expected in FIXED.items():
        bounded_file(fixtures / filename, expected, 256 * 1024)
    units = [json.loads(line) for line in corpus_bytes.splitlines() if line.strip()]
    if len(units) != 1779:
        raise PassageBoundaryError("Unexpected frozen corpus unit count")
    tasks = selector.load_tasks(fixtures / "tasks_instantiated.csv")
    if task_id not in tasks or acquisition_ags != selector.CASE_AGS[tasks[task_id]["case_id"]]:
        raise PassageBoundaryError("Task/acquisition AGS mismatch")
    task = tasks[task_id]
    rankings = selector.load_rankings(
        *(fixtures / n for n in ("L0_union.jsonl", "L1_union.jsonl", "L2_L3_union.jsonl"))
    )
    top = rankings[first_stage][task_id]["results"]
    by_id, index = selector.make_index(units)
    if len(top) > 10 or len({h["unit_id"] for h in top}) != len(top):
        raise PassageBoundaryError("Invalid fixed ranking")
    keys = {k for h in top for k in selector.version_keys(by_id[h["unit_id"]])}
    if authorized_versions is not None and set(authorized_versions) != keys:
        raise PassageBoundaryError(
            "Source-body versions differ from fixed first-stage authorization"
        )
    pool = selector.source_pool(top, by_id, index, acquisition_ags)
    if len(pool) > 1779 or sum(len(u["text"]) for u in pool) > 2_000_000:
        raise PassageBoundaryError("Source-opening scan cap exceeded")

    provenance = {}
    with ExitStack() as stack:
        connections = {}
        for u in pool:
            valid_origins = []
            if not u.get("origins"):
                raise PassageBoundaryError("Missing parent-unit provenance")
            for origin in u["origins"]:
                run = origin["run"]
                if run not in snapshots:
                    raise PassageBoundaryError(f"Explicit database binding missing: {run}")
                if run not in connections:
                    binding = snapshots[run]
                    bounded_file(binding.path, binding.sha256, 16 * 1024 * 1024)
                    con = sqlite3.connect(
                        Path(binding.path).resolve(strict=True).as_uri() + "?mode=ro", uri=True
                    )
                    stack.callback(con.close)
                    con.row_factory = sqlite3.Row
                    connections[run] = con
                row = (
                    connections[run]
                    .execute(
                        "SELECT c.*, d.url_final, d.raw_hash FROM chunks c "
                        "JOIN documents_raw d USING(document_id) "
                        "WHERE c.chunk_id=?",
                        (origin["chunk_id"],),
                    )
                    .fetchone()
                )
                if row is None or row["document_id"] != origin["document_id"]:
                    raise PassageBoundaryError("Broken chunk/document provenance")
                if (row["municipality_id"], row["url_final"]) != (
                    u["acquisition_ags"],
                    u["source_url"],
                ):
                    raise PassageBoundaryError("Chunk AGS/URL provenance mismatch")
                if row["raw_hash"] not in by_id[u["unit_id"]]["raw_hashes"]:
                    raise PassageBoundaryError("Database body-version mismatch")
                if (
                    row["text"] != u["text"]
                    or (row["heading_context"] or "") != u["heading_context"]
                    or (row["page_ref"] or "") != u["page_ref"]
                ):
                    raise PassageBoundaryError("Parent text/locator differs from frozen corpus")
                segment = (
                    connections[run]
                    .execute(
                        "SELECT document_id FROM segments WHERE segment_id=?", (row["segment_id"],)
                    )
                    .fetchone()
                )
                if not segment or segment[0] != row["document_id"]:
                    raise PassageBoundaryError("Invalid parent segment provenance")
                if row["raw_hash"] in u["raw_hashes"]:
                    valid_origins.append(
                        dict(
                            origin,
                            segment_id=row["segment_id"],
                            raw_hash=row["raw_hash"],
                            database_snapshot_id=snapshots[run].sha256,
                        )
                    )
            if set(o["raw_hash"] for o in valid_origins) != set(u["raw_hashes"]):
                raise PassageBoundaryError("Authorized body version lacks a verified origin")
            provenance[u["unit_id"]] = valid_origins

    selected, proxy_used, spans = span_selector.selector_spans(pool, task, budget_chars, role=True)
    items, context = [], ""
    for pick in selected:
        span = spans[pick["unit_id"]]
        parent = by_id[span["parent_unit_id"]]
        a, b = span["start_char"], span["end_char"]
        if not 0 <= a < b <= len(parent["text"]) or parent["text"][a:b] != span["text"]:
            raise PassageBoundaryError("Invalid exact-span provenance")
        origins = provenance[parent["unit_id"]]
        primary = origins[0]
        item = dict(
            evidence_id="span-sha256:" + pick["unit_id"],
            database_snapshot_id=primary["database_snapshot_id"],
            source_type="chunk",
            source_id=primary["chunk_id"],
            chunk_id=primary["chunk_id"],
            segment_id=primary["segment_id"],
            document=dict(
                document_id=primary["document_id"],
                source_url=parent["source_url"],
                heading_context=parent["heading_context"],
                municipality_ids=[acquisition_ags],
            ),
            evidence_text=span["text"],
            parent_unit_id=parent["unit_id"],
            start_char=a,
            end_char=b,
            raw_hashes=span["raw_hashes"],
            acquisition_ags=acquisition_ags,
            page_ref=parent["page_ref"],
            origins=origins,
            claim_supported="NOT_ASSESSED",
            attribution_status="unknown",
            territorial_scope=[],
            attribution_reason="acquisition_and_document_metadata_are_not_claim_evidence",
            extraction_run_ids=[],
            graph_artifact_ids=[],
            assertions=[],
            retrieval_traces=[],
            retrieval_score=None,
            rank=len(items) + 1,
        )
        block = context_record(item)
        if len(context) + len(block) > budget_chars:
            break
        context += block
        items.append(item)
    return dict(
        status="experimental",
        items=items,
        context=context,
        context_chars=len(context),
        budget_chars=budget_chars,
        kit_proxy_chars=proxy_used,
        corpus_sha256=CORPUS_SHA,
        task_id=task_id,
        first_stage=first_stage,
        scanned_units=len(pool),
        scanned_chars=sum(len(u["text"]) for u in pool),
        authorized_versions=sorted(keys),
        claim_supported="NOT_ASSESSED",
    )
