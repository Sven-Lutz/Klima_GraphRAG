# ruff: noqa: E501
"""Materialize a source-reviewed relational sidecar and freeze task components."""

import argparse
import json
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research.bavaria.federation import source_connection

from .annotations import ENTITIES, RELATIONS, TASKS
from .prepare import RESERVE, ident, read_case, sha


class Components:
    def __init__(self):
        self.parent = {}

    def find(self, value):
        if value not in self.parent:
            self.parent[value] = value
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, *values):
        root = self.find(values[0])
        for value in values[1:]:
            other = self.find(value)
            self.parent[other] = root


def build(db, output, specification=None):
    custom = json.loads(Path(specification).read_text()) if specification else None
    entities = custom["entities"] if custom else ENTITIES
    relation_specs = custom["relations"] if custom else RELATIONS
    task_specs = custom["tasks"] if custom else TASKS
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh output directory")
    if shutil.disk_usage(output.parent).free < RESERVE + 15 * 1024**2:
        raise RuntimeError("Storage reserve plus sidecar allowance not available")
    started = time.perf_counter()
    extra_databases = custom.get("extra_databases", []) if custom else []
    source = source_connection(db, extra_databases)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    cases, records = {}, []
    components = Components()
    for spec in relation_specs:
        rid, subj, pred, obj, prefix, excerpt, scope, modality, reference_time, contexts = spec
        case = read_case(source, prefix, excerpt)
        cases[case["evidence_id"]] = case
        context_ids = []
        for context in contexts:
            context_case = read_case(source, context)
            cases[context_case["evidence_id"]] = context_case
            context_ids.append(context_case["evidence_id"])
        # Scope is explicitly original-first assessed, never inherited from frame.
        record = {
            **case,
            "relation_id": rid,
            "subject_id": subj,
            "predicate": pred,
            "object_id": obj,
            "claim_scope_ags": scope,
            "scope_basis": "Named locality/project in original heading, sentence, or cited context",
            "modality": modality,
            "reference_time": reference_time,
            "context_evidence_ids": context_ids,
            "extraction_model": "agent-original-review",
            "prompt_version": "relational-review-v1",
            "status": "PROVISIONAL_AGENT_SOURCE_REVIEWED",
            "uncertainty": (
                "No independent human validation, legal mandate, disbursement or "
                "present-day truth implied."
            ),
        }
        if custom and rid in custom.get("relation_notes", {}):
            record["review_notes"] = custom["relation_notes"][rid]
        records.append(record)
        values = [subj, obj, "source:" + case["source_sha256"]]
        values += ["municipality:" + ags for ags in scope]
        if custom:
            # Shared publishing frame is a dependence link, never a factual claim-scope edge.
            values += ["municipality:" + ags for ags in case["source_frame_ags"]]
        if custom:
            # Dates, status labels and numbers are ontology constants, not leakage links.
            values = [
                v
                for v in values
                if v.startswith(("source:", "municipality:"))
                or entities.get(v, [None, None])[1] in custom["dependency_entity_types"]
            ]
        components.union(*values)
    # Conservative leakage link only: academic IfE / GmbH may be related; do not
    # convert this uncertainty into a factual organization-identity relation.
    if not custom:
        components.union("org:ife_oth", "org:ife_gmbh")
    else:
        for link in custom.get("dependency_links", []):
            components.union(*link)
    groups = defaultdict(list)
    for value in components.parent:
        groups[components.find(value)].append(value)
    group_ids = {value: ident(sorted(group)) for group in groups.values() for value in group}
    dev_roots = (
        {group_ids[v] for v in custom.get("development_entities", [])}
        if custom
        else {group_ids["org:etz"], group_ids["municipality:09174115"]}
    )
    record_map = {r["relation_id"]: r for r in records}
    tasks = []
    for spec in task_specs:
        qid, family, question, patterns, expected, *abstain = spec
        roots = {
            group_ids[value]
            for pattern in patterns
            for value in (pattern[0], pattern[2])
            if not value.startswith("?") and value in group_ids
        }
        if not roots and custom:
            roots = {
                group_ids["source:" + digest]
                for digest in custom.get("task_dependency_sources", {}).get(qid, [])
            }
        if len(roots) != 1:
            split = "quarantine_cross_component"
        elif roots & dev_roots:
            split = "development"
        else:
            split = "evaluation_provisional"
        evidence = sorted(
            {
                eid
                for rid in expected
                for eid in [
                    record_map[rid]["evidence_id"],
                    *record_map[rid]["context_evidence_ids"],
                ]
            }
        )
        tasks.append(
            {
                "task_id": qid,
                "family": family,
                "question": question,
                "patterns": patterns,
                "required_relations": expected,
                "required_evidence": evidence,
                "split": split,
                "component_ids": sorted(roots),
                "expected_disposition": abstain[0] if abstain else "SOURCE_BOUNDED_ANSWER",
                "reference_status": "PROVISIONAL_AGENT_ORIGINAL_REVIEW_NOT_HUMAN_GOLD",
                "exposure": "AGENT_REFERENCE_PREPARATION_BEFORE_METHOD_OUTPUTS",
            }
        )
    source_hashes = sorted(
        {r["source_sha256"] for r in cases.values()}
        | set(custom.get("permitted_sources", []) if custom else [])
    )
    # All original units from permitted source versions, not just reference spans.
    all_units = []
    for digest in source_hashes:
        associations = [
            dict(r)
            for r in source.execute(
                "SELECT * FROM associations WHERE sha256=? ORDER BY url,ags", (digest,)
            )
        ]
        for row in source.execute("SELECT * FROM units WHERE sha256=? ORDER BY ordinal", (digest,)):
            item = dict(row)
            item.update(
                source_url=associations[0]["url"],
                source_frame_ags=sorted({a["ags"] for a in associations}),
            )
            all_units.append(item)
    source.commit()
    original_paths = {
        r["sha256"]: r["raw_path"] for r in source.execute("SELECT sha256,raw_path FROM documents")
    }
    output.mkdir(parents=True)
    corpus = ident(
        [{"id": u["unit_id"], "text": u["text"], "sha256": u["sha256"]} for u in all_units]
    )
    sidecar = output / "evidence.sqlite"
    with sqlite3.connect(sidecar) as conn:
        conn.executescript("""PRAGMA foreign_keys=ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE sources(source_sha256 TEXT PRIMARY KEY,source_url TEXT,raw_path TEXT,
          source_type TEXT,source_frame_ags_json TEXT,original_verified INTEGER);
        CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY,source_sha256 TEXT REFERENCES sources,
          unit_id TEXT,text TEXT,heading TEXT,page_ref TEXT,locator_json TEXT);
        CREATE TABLE entities(entity_id TEXT PRIMARY KEY,label TEXT,entity_type TEXT,
          identity_basis TEXT);
        CREATE TABLE relations(relation_id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entities,
          predicate TEXT,object_id TEXT REFERENCES entities,evidence_id TEXT REFERENCES evidence,
          record_json TEXT);
        CREATE INDEX relation_subject ON relations(subject_id);
        CREATE INDEX relation_object ON relations(object_id);
        CREATE INDEX relation_predicate ON relations(predicate);
        """)
        conn.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("schema", "bavaria-relational-v1"),
                ("corpus_id", corpus),
                ("source_database", str(Path(db).resolve())),
                ("manifest_id", "relational-snapshot:" + corpus),
                ("scientific_status", "PROVISIONAL_AGENT_REFERENCES"),
            ],
        )
        for digest in source_hashes:
            case = next((c for c in cases.values() if c["source_sha256"] == digest), None)
            if case is None:
                # Negative/source-quality controls retain identical text access for every arm.
                row = next(u for u in all_units if u["sha256"] == digest)
                raw_path = original_paths[digest]
                import gzip

                body = Path(raw_path).read_bytes()
                body = gzip.decompress(body) if str(raw_path).endswith(".gz") else body
                if sha(body) != digest:
                    raise ValueError("Original mismatch for negative control")
                case = {
                    "source_url": row["source_url"],
                    "raw_path": raw_path,
                    "native_source_type": "pdf" if body.startswith(b"%PDF") else "html",
                    "source_frame_ags": row["source_frame_ags"],
                }
            conn.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?)",
                (
                    digest,
                    case["source_url"],
                    case["raw_path"],
                    case["native_source_type"],
                    json.dumps(case["source_frame_ags"]),
                    1,
                ),
            )
        for unit in all_units:
            known = cases.get(unit["unit_id"])
            locator = (
                known["original_locator"]
                if known
                else {
                    "type": "parser_unit_with_original_available",
                    "ordinal": unit["ordinal"],
                    "page_ref": unit["page_ref"],
                    "reference_review": "NOT_INDEPENDENTLY_REVIEWED",
                }
            )
            conn.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
                (
                    unit["unit_id"],
                    unit["sha256"],
                    unit["unit_id"],
                    unit["text"],
                    unit["heading"],
                    unit["page_ref"],
                    json.dumps(locator),
                ),
            )
        for eid, (label, kind) in entities.items():
            if eid == "org:uez":
                basis = (
                    "Compatible explicit surface name; eG suffix/legal-identity acceptance "
                    "still provisional"
                )
            elif eid in ("org:ife_gmbh", "org:ife_oth"):
                basis = (
                    "Kept distinct; possible affiliation links dependence groups only, not "
                    "factual identity"
                )
            else:
                basis = (
                    "Explicit original name or document-specific intervention; "
                    "independently unverified"
                )
            conn.execute("INSERT INTO entities VALUES (?,?,?,?)", (eid, label, kind, basis))
        for record in records:
            conn.execute(
                "INSERT INTO relations VALUES (?,?,?,?,?,?)",
                (
                    record["relation_id"],
                    record["subject_id"],
                    record["predicate"],
                    record["object_id"],
                    record["evidence_id"],
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                ),
            )
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    for name, rows in [("relations.jsonl", records), ("tasks.jsonl", tasks)]:
        (output / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        )
    freeze = {
        "schema": "bavaria-relational-study-freeze-v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_BEFORE_RETRIEVAL_METHOD_OUTPUTS",
        "source_database_sha256": sha(Path(db).read_bytes()),
        "source_databases_sha256": {
            str(Path(p).resolve()): sha(Path(p).read_bytes()) for p in [db, *extra_databases]
        },
        "corpus_id": corpus,
        "sources": len(source_hashes),
        "evidence_units": len(all_units),
        "relations": len(records),
        "entities": len(entities),
        "include_relation_cards": bool(custom),
        "dependency_note": "Semantic dates/status/counts excluded only in explicit custom study; old default unchanged.",
        "specification_sha256": sha(Path(specification).read_bytes()) if specification else None,
        "tasks": len(tasks),
        "split_counts": dict(Counter(t["split"] for t in tasks)),
        "dependency_components": [sorted(g) for g in groups.values()],
        "reference_labels": "PROVISIONAL_AGENT_SOURCE_REVIEW_NOT_INDEPENDENT_GOLD",
        "query_planning": (
            "Structured logical patterns explicitly supplied to every method; NL "
            "planning not evaluated"
        ),
        "budgets": {
            "max_evidence_items": 8,
            "max_context_characters": 12000,
            "max_pattern_hops": custom.get("budgets", {}).get("max_pattern_hops", 3)
            if custom
            else 3,
            "top_candidates": 50,
            "rrf_constant": 60,
        },
        "hypothesis": (
            "Evaluate evidence bundle assembly and representation equivalence on "
            "genuine policy relations; no graph-specific gain assumed"
        ),
        "prepared_before_outputs": True,
        "graph_qa": {
            "foreign_key_violations": fk,
            "exact_spans_validated": len(records),
            "originals_verified": len(source_hashes),
            "independent_relation_precision": None,
        },
        "build_seconds": time.perf_counter() - started,
        "database_bytes": sidecar.stat().st_size,
        "files_sha256": {
            name: sha((output / name).read_bytes())
            for name in ["evidence.sqlite", "relations.jsonl", "tasks.jsonl"]
        },
        "code_sha256": {
            str(p): sha(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    (output / "FREEZE.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2))
    return freeze


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--specification", help="Frozen explicit source-reviewed JSON specification"
    )
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.output, args.specification), ensure_ascii=False, indent=2))
