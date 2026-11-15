"""Read-only bounded audit of structurally possible, source-bound graph chains.

This does not label relevance or derive transitive facts, and reads no question split.
"""

import argparse
import hashlib
import itertools
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from research.bavaria.workspace.core import write_guard

LITERAL_TYPES = {
    "Municipality",
    "Date",
    "Duration",
    "Count",
    "Quantity",
    "Interval",
    "Status",
    "EpistemicStatus",
    "Location",
    "Area",
    "GrantCap",
}


def chains_for(entities, relations, evidence):
    by_entity = defaultdict(list)
    for row in relations:
        if row["subject_id"] not in entities or row["object_id"] not in entities:
            raise ValueError("Relation endpoint missing from bound entity inventory")
        if row["evidence_id"] not in evidence:
            raise ValueError("Relation evidence absent from bound sidecar")
        for key in [row["subject_id"], row["object_id"]]:
            by_entity[key].append(row)
    rejected = Counter()
    chains = []
    for key, rows in sorted(by_entity.items()):
        for a, b in itertools.combinations(sorted(rows, key=lambda r: r["relation_id"]), 2):
            if entities[key]["entity_type"] in LITERAL_TYPES:
                rejected["generic_or_literal_bridge"] += 1
                continue
            scope = set(a["record"].get("claim_scope_ags") or []) & set(
                b["record"].get("claim_scope_ags") or []
            )
            if not scope:
                rejected["no_shared_explicit_claim_scope"] += 1
                continue
            if evidence[a["evidence_id"]]["unit_id"] == evidence[b["evidence_id"]]["unit_id"]:
                rejected["same_source_unit_not_independent_hop"] += 1
                continue
            years = [
                set(
                    re.findall(
                        r"\b(?:18|19|20)\d{2}\b", str(r["record"].get("reference_time") or "")
                    )
                )
                for r in [a, b]
            ]
            if all(years) and not years[0] & years[1]:
                rejected["disjoint_explicit_years"] += 1
                continue
            chains.append(
                dict(
                    bridge_id=key,
                    bridge_label=entities[key]["label"],
                    bridge_type=entities[key]["entity_type"],
                    relation_ids=[a["relation_id"], b["relation_id"]],
                    predicates=[a["predicate"], b["predicate"]],
                    claim_scope_intersection=sorted(scope),
                    source_unit_ids=[evidence[r["evidence_id"]]["unit_id"] for r in [a, b]],
                    evidence_ids=[a["evidence_id"], b["evidence_id"]],
                    quote_sha256=[
                        hashlib.sha256(r["record"]["quote"].encode()).hexdigest() for r in [a, b]
                    ],
                    temporal_alignment="UNKNOWN"
                    if not all(years)
                    else "SHARED_YEAR_MENTION_NOT_VALIDITY",
                )
            )
    return chains, dict(rejected)


def run(manifest, output):
    if output.exists():
        raise FileExistsError("Preserve prior audit; use a fresh output path")
    data = json.loads(manifest.read_text())
    artifact = data["artifacts"]["relations"]
    path = Path(artifact["path"])
    if not path.is_absolute():
        path = manifest.parent / path
    identity = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            identity.update(block)
    if identity.hexdigest() != artifact["sha256"]:
        raise ValueError("Relation sidecar identity differs from explicit manifest")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT count(*) FROM relations").fetchone()[0] > 500:
            raise ValueError(
                "Pair audit bounded to 500 relations; use a separately reviewed strategy"
            )
        entities = {r["entity_id"]: dict(r) for r in conn.execute("SELECT * FROM entities")}
        evidence = {r["evidence_id"]: dict(r) for r in conn.execute("SELECT * FROM evidence")}
        relations = []
        for row in conn.execute("SELECT * FROM relations ORDER BY relation_id"):
            record = dict(row)
            record["record"] = json.loads(record.pop("record_json"))
            relations.append(record)
    chains, rejected = chains_for(entities, relations, evidence)
    report = dict(
        schema="graph-chain-feasibility-v2",
        snapshot_id=data["snapshot_id"],
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        relation_sidecar_sha256=artifact["sha256"],
        relations=len(relations),
        entities=len(entities),
        candidate_chains=len(chains),
        rejected=rejected,
        chains=chains,
        scientific_gold=False,
        question_splits_opened=False,
        limitations=[
            "Structural candidates only; source statements not independently validated",
            "No relevance label or transitive fact; shared year mention is not temporal validity",
            "Unknown temporal alignment stays unknown; no new graph assertions created",
        ],
    )
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    write_guard(output, len(raw.encode()))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(raw)
    print(json.dumps({k: v for k, v in report.items() if k != "chains"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
