"""Reproducible claim/graph projections; no independent-gold claims."""

import csv
import gzip
import hashlib
import json
import re
import sqlite3
import time
import types
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from research.climate_policy import store
from research.climate_policy.retrieval import lexical_rank

from .acquisition import TOPIC, connect


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def import_e2(db, export):
    """Retain original source-order units and point to existing bytes; never copy them."""
    export = Path(export)
    sources = [json.loads(line) for line in (export / "catalog.jsonl").read_text().splitlines()]
    # Check every original before beginning any mutation.
    for source in sources:
        raw = export / "raw" / source["body_sha256"]
        if hashlib.sha256(raw.read_bytes()).hexdigest() != source["body_sha256"]:
            raise ValueError("Historical original integrity failure")
    with connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inherited_sources(sha256 TEXT PRIMARY KEY,record TEXT)"
        )
        for source in sources:
            sha = source["body_sha256"]
            raw = export / "raw" / sha
            conn.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
                (
                    sha,
                    str(raw.resolve()),
                    raw.stat().st_size,
                    raw.stat().st_size,
                    "INHERITED_E2_SOURCE_ORDER",
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                (sha, source["ags"], source["url"], source["provenance_status"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO inherited_sources VALUES (?,?)",
                (sha, json.dumps(source, ensure_ascii=False)),
            )
        for line in (export / "corpus.jsonl").read_text().splitlines():
            unit = json.loads(line)
            conn.execute(
                "INSERT OR IGNORE INTO units VALUES (?,?,?,?,?,?,?)",
                (
                    unit["unit_id"],
                    unit["raw_hashes"][0],
                    unit["origins"][0]["segment_order"],
                    unit["text"],
                    unit["heading_context"],
                    unit["page_ref"],
                    "inherited_source_order",
                ),
            )


def build(db, output):
    """One fresh, explicitly selected projection database; original store is read-only."""
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh projection path; historical projections are immutable")
    started = time.monotonic()
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        registry = {
            r["ags"]: json.loads(r["record"]) for r in conn.execute("SELECT * FROM municipalities")
        }
        units = [dict(r) for r in conn.execute("SELECT * FROM units ORDER BY unit_id")]
        associations = [
            dict(r) for r in conn.execute("SELECT * FROM associations ORDER BY sha256,ags,url")
        ]
        originals = [dict(r) for r in conn.execute("SELECT * FROM documents")]
    for original in originals:
        path = Path(original["raw_path"])
        body = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        if hashlib.sha256(body).hexdigest() != original["sha256"]:
            raise ValueError("Original integrity failure before graph projection")
    names = {ags: r["name"] for ags, r in registry.items()}
    # Same isolated namespace adaptation as existing lexical research adapter.
    # The frozen extractor and historical fingerprints remain byte-identical.
    extract = types.FunctionType(
        store.extract.__code__, {**store.__dict__, "MUNICIPALITIES": names}, "extract"
    )
    by_source = defaultdict(list)
    for association in associations:
        by_source[association["sha256"]].append(association)
    snapshot = digest({"units": units, "associations": associations, "registry": registry})
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    with sqlite3.connect(output) as conn:
        conn.executescript("""PRAGMA foreign_keys=ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE evidence(unit_id TEXT PRIMARY KEY,source_sha256 TEXT,text TEXT,
          heading TEXT,page_ref TEXT,source_frame_ags TEXT,source_url TEXT);
        CREATE TABLE claims(claim_id TEXT PRIMARY KEY,unit_id TEXT REFERENCES evidence(unit_id),
          field TEXT,value TEXT,record_json TEXT);
        CREATE INDEX claim_field ON claims(field);
        CREATE TABLE nodes(id TEXT PRIMARY KEY,type TEXT,label TEXT,status TEXT);
        CREATE TABLE edges(id TEXT PRIMARY KEY,subject TEXT REFERENCES nodes(id),
          predicate TEXT,object TEXT REFERENCES nodes(id),evidence_id TEXT,
          representation TEXT,status TEXT,record_json TEXT);
        CREATE INDEX graph_subject ON edges(subject);
        CREATE INDEX graph_object ON edges(object);
        """)
        conn.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("schema", "bavaria-projection-v1"),
                ("snapshot", snapshot),
                ("source_database", str(Path(db).resolve())),
                ("extractor_sha256", store.sha(Path(store.__file__))),
            ],
        )

        def node(identifier, kind, label, status="STRUCTURAL"):
            conn.execute(
                "INSERT OR IGNORE INTO nodes VALUES (?,?,?,?)", (identifier, kind, label, status)
            )

        def edge(
            subject,
            predicate,
            obj,
            evidence=None,
            representation="G1",
            status="STRUCTURAL",
            record=None,
        ):
            value = [subject, predicate, obj, evidence, representation, status]
            conn.execute(
                "INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?,?,?)",
                (
                    digest(value),
                    subject,
                    predicate,
                    obj,
                    evidence,
                    representation,
                    status,
                    json.dumps(record or {}, ensure_ascii=False),
                ),
            )

        for ags, r in registry.items():
            node("municipality:" + ags, "Municipality", r["official_name"])
        for a in associations:
            source = "source:" + a["sha256"]
            node(source, "SourceVersion", a["url"])
            edge(source, "ACQUIRED_UNDER_FRAME", "municipality:" + a["ags"])
        for unit in units:
            candidates = by_source[unit["sha256"]]
            # Preserve all frame associations in G1; never select one as claim jurisdiction.
            a = candidates[0]
            conn.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
                (
                    unit["unit_id"],
                    unit["sha256"],
                    unit["text"],
                    unit["heading"],
                    unit["page_ref"],
                    a["ags"],
                    a["url"],
                ),
            )
            node(unit["unit_id"], "Evidence", unit["text"][:180])
            edge(
                unit["unit_id"], "FROM_SOURCE_VERSION", "source:" + unit["sha256"], unit["unit_id"]
            )
            if not TOPIC.search(unit["text"] + " " + (unit["heading"] or "")):
                continue
            input_unit = {
                "unit_id": unit["unit_id"],
                "text": unit["text"],
                "heading_context": unit["heading"],
                "page_ref": unit["page_ref"],
                "raw_hashes": [unit["sha256"]],
                "source_url": a["url"],
                "acquisition_ags": a["ags"],
            }
            for claim in extract(input_unit):
                if claim["field"] == "SOURCE_STATEMENT":
                    continue
                # No name occurrence or publisher is enough to validate claim scope.
                claim["municipality_ags"] = None
                claim["scope_status"] = "REQUIRES_CLAIM_SCOPE_REVIEW"
                claim["claim_id"] = digest({k: v for k, v in claim.items() if k != "claim_id"})
                assert unit["text"][claim["start_char"] : claim["end_char"]] == claim["quote"]
                conn.execute(
                    "INSERT OR IGNORE INTO claims VALUES (?,?,?,?,?)",
                    (
                        claim["claim_id"],
                        unit["unit_id"],
                        claim["field"],
                        claim["value"],
                        json.dumps(claim, ensure_ascii=False),
                    ),
                )
                counts[claim["field"]] += 1
                cid = "claim:" + claim["claim_id"]
                node(cid, "Claim", claim["field"] + ": " + str(claim["value"]), "PROVISIONAL")
                edge(
                    cid,
                    "SUPPORTED_BY",
                    unit["unit_id"],
                    unit["unit_id"],
                    "G2",
                    "RULE_EXTRACTED_UNVALIDATED",
                    claim,
                )
                field = "field:" + claim["field"]
                node(field, "FieldType", claim["field"])
                edge(
                    cid,
                    "HAS_FIELD_TYPE",
                    field,
                    unit["unit_id"],
                    "G2",
                    "RULE_EXTRACTED_UNVALIDATED",
                )
                if claim.get("actor"):
                    actor = "actor:" + digest(claim["actor"].casefold().strip())
                    node(actor, "ActorMention", claim["actor"], "UNRESOLVED_IDENTITY")
                    edge(
                        cid,
                        "MENTIONS_ACTOR",
                        actor,
                        unit["unit_id"],
                        "G2",
                        "NAME_MATCH_NOT_CONFIRMED_IDENTITY",
                        claim,
                    )
        report = {
            "schema": "bavaria-projection-v1",
            "snapshot": snapshot,
            "units": len(units),
            "claims_by_field": dict(counts),
            "nodes": conn.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "edges_by_representation": dict(
                conn.execute("SELECT representation,count(*) FROM edges GROUP BY representation")
            ),
            "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
            "independent_relation_accuracy": None,
            "claim_scope": "ALL_UNVALIDATED",
            "seconds": time.monotonic() - started,
        }
    report["bytes"] = output.stat().st_size
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2))
    return report


def coverage(db, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with connect(db) as conn:
        records = []
        for row in conn.execute("SELECT * FROM municipalities ORDER BY ags"):
            record = json.loads(row["record"])
            record.update(
                coverage_status=row["status"], checked_at=row["checked_at"], error=row["error"]
            )
            record["source_versions"] = conn.execute(
                "SELECT count(DISTINCT sha256) FROM associations WHERE ags=?", (row["ags"],)
            ).fetchone()[0]
            record["parsed_versions"] = conn.execute(
                "SELECT count(DISTINCT a.sha256) FROM associations a JOIN units u "
                "ON a.sha256=u.sha256 WHERE a.ags=?",
                (row["ags"],),
            ).fetchone()[0]
            records.append(record)
        (output / "coverage.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        )
        report = {
            "municipalities": len(records),
            "dispositions": sum(r["coverage_status"] != "NOT_CHECKED" for r in records),
            "checked": sum(r["checked_at"] is not None for r in records),
            "checked_definition": (
                "completed municipality crawl dispositions; resource-only deferrals excluded"
            ),
            "candidate_domains": len(
                {
                    urlsplit(r["homepage_candidate"]).hostname
                    for r in records
                    if r["homepage_candidate"]
                }
            ),
            "independently_verified_domains": 0,
            "statuses": dict(Counter(r["coverage_status"] for r in records)),
            "versions": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
            "units": conn.execute("SELECT count(*) FROM units").fetchone()[0],
            "versions_by_parse_status": dict(
                conn.execute("SELECT parse_status,count(*) FROM documents GROUP BY parse_status")
            ),
            "requests": conn.execute("SELECT count(*) FROM requests").fetchone()[0],
            "transport_body_bytes": conn.execute("SELECT sum(bytes) FROM requests").fetchone()[0],
            "storage": dict(
                conn.execute(
                    "SELECT sum(raw_bytes) raw_bytes,"
                    "sum(compressed_bytes) stored_payload_bytes FROM documents"
                ).fetchone()
            ),
            "municipalities_with_sources": sum(r["source_versions"] > 0 for r in records),
            "municipalities_with_text": sum(r["parsed_versions"] > 0 for r in records),
            "verified_climate_policy_coverage": None,
            "regions": {
                region: dict(
                    Counter(r["coverage_status"] for r in records if r["region"] == region)
                )
                for region in sorted({r["region"] for r in records})
            },
        }
        (output / "coverage-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
    return report


def search(db, question, ags=None, k=5):
    """Evidence discovery only: no generated factual answer or scope inference."""
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "evidence" in tables:
            sql = "SELECT * FROM evidence" + (" WHERE source_frame_ags=?" if ags else "")
        else:
            sql = (
                "SELECT u.unit_id,u.sha256 source_sha256,u.text,u.heading,u.page_ref,"
                "group_concat(DISTINCT a.ags) source_frame_ags,min(a.url) source_url "
                "FROM units u JOIN associations a USING(sha256) "
                + ("WHERE a.ags=? " if ags else "")
                + "GROUP BY u.unit_id"
            )
        rows = [dict(r) for r in conn.execute(sql, (ags,) if ags else ())]
    units = [
        dict(
            unit_id=r["unit_id"],
            text=r["text"],
            heading_context=r["heading"],
            acquisition_ags=r["source_frame_ags"],
            page_ref=r["page_ref"],
            source_url=r["source_url"],
            raw_hashes=[r["source_sha256"]],
        )
        for r in rows
    ]
    scores = lexical_rank(units, question, german=True)
    by_id = {r["unit_id"]: r for r in rows}
    return {
        "status": "EVIDENCE_DISCOVERY_NOT_A_COMPLETE_ANSWER",
        "warning": "Source frame is not claim jurisdiction; missing evidence is not zero policy.",
        "results": [{**by_id[uid], "score": score} for uid, score in scores[:k]],
    }


def municipality_dossiers(db, ags_list):
    dossiers = []
    with connect(db) as conn:
        for ags in ags_list:
            row = conn.execute("SELECT * FROM municipalities WHERE ags=?", (ags,)).fetchone()
            if row is None:
                raise ValueError("AGS outside the official Bavaria frame: " + ags)
            record = json.loads(row["record"])
            sources = [
                dict(r)
                for r in conn.execute(
                    "SELECT a.*,d.parse_status,d.raw_path FROM associations a "
                    "JOIN documents d USING(sha256) WHERE a.ags=? ORDER BY a.sha256",
                    (ags,),
                )
            ]
            dossiers.append(
                {
                    "ags": ags,
                    "name": record["official_name"],
                    "region": record["region"],
                    "population": record["population"],
                    "population_date": record["population_date"],
                    "coverage_status": row["status"],
                    "checked_at": row["checked_at"],
                    "source_versions": sources,
                    "policy_count": None,
                    "verified_implementation_count": None,
                    "interpretation": "Source availability only; no policy-performance comparison.",
                }
            )
    return {
        "municipalities": dossiers,
        "comparison_definition": "identical source-coverage fields",
        "warning": "No sources does not mean no climate policy; source frame is not claim scope.",
    }


def benchmark_candidates(db, output, minimum_municipalities=100):
    """Source-led question candidates, never automatically label them human gold."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT u.*,a.ags,a.url,m.record FROM units u JOIN associations a USING(sha256) "
            "JOIN municipalities m ON m.ags=a.ags ORDER BY a.ags,u.unit_id"
        )
    chosen = defaultdict(list)
    for row in rows:
        record = json.loads(row["record"])
        text = row["text"]
        if len(text) < 100 or not TOPIC.search(text) or len(chosen[row["ags"]]) >= 2:
            continue
        if re.search(r"cookie|datenschutz|javascript|Google Translate", text, re.I):
            continue
        patterns = [
            (
                "finance",
                r"\d[\d.,]*\s*(?:€|Euro)",
                "Welche Geldbeträge nennt die Quelle, mit welcher Kosten-/Förderrolle und Einheit?",
            ),
            (
                "target_or_outcome",
                r"\d[\d.,]*\s*(?:kWh|MW|kWp|%|Tonnen|t CO)",
                "Welche quantifizierten Ziele oder berichteten Wirkungen nennt die Quelle? "
                "Trennen Sie Prognose und Messung.",
            ),
            (
                "temporal_status",
                r"geplant|beschlossen|umgesetzt|errichtet|installiert",
                "Welchen Maßnahmenstatus belegt die Quelle, und zu welchem Zeitpunkt?",
            ),
            (
                "date",
                r"20\d\d",
                "Welche Bezugsjahre nennt die Quelle? Trennen Sie Zieljahr, Berichtsjahr "
                "und Veröffentlichungsdatum.",
            ),
            (
                "source_attribution",
                r"Stadtwerke|Zweckverband|Landkreis|Gemeinderat|Stadtrat",
                "Welche Institution wird in welcher Rolle genannt, und ist kommunale "
                "Zuständigkeit tatsächlich belegt?",
            ),
        ]
        used = {t["family"] for t in chosen[row["ags"]]}
        match = next(
            (
                (family, re.search(pattern, text, re.I), prompt)
                for family, pattern, prompt in patterns
                if family not in used and re.search(pattern, text, re.I)
            ),
            None,
        )
        if match is None:
            continue
        family, cue, prompt = match
        heading = (row["heading"] or "")[:180]
        chosen[row["ags"]].append(
            {
                "id": digest([row["ags"], row["unit_id"], family]),
                "ags": row["ags"],
                "region": record["region"],
                "family": family,
                "question": (
                    f"{prompt} Erfassungsrahmen: {record['official_name']}. "
                    f"Quellenabschnitt: {heading or row['url']}."
                ),
                "candidate_cue_span": [cue.start(), cue.end()],
                "candidate_source": row["sha256"],
                "candidate_evidence": row["unit_id"],
                "original_url": row["url"],
                "page_ref": row["page_ref"],
                "reference_answer": None,
                "qrels": None,
                "human_review": "PENDING",
                "split": "QUARANTINE_PENDING_SOURCE_PROJECT_DEPENDENCY_AUDIT",
                "source_dependency": row["sha256"],
                "municipality_dependency": row["ags"],
                "exposure": "SOURCE_LED_AGENT_PREPARATION",
            }
        )
    chosen = {ags: group for ags, group in chosen.items() if group}
    tasks = [task for group in chosen.values() for task in group]
    (output / "candidates.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks)
    )
    for reviewer in ("A", "B"):
        with (output / f"reviewer-{reviewer}.csv").open("w") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "task_id",
                    "reviewer_identity",
                    "original_seen_at",
                    "answerability",
                    "claim_scope_ags",
                    "publication_date",
                    "status",
                    "evidence_spans",
                    "answer",
                    "uncertainty",
                    "exposure",
                ]
            )
            for task in sorted(tasks, key=lambda t: digest([reviewer, t["id"]])):
                writer.writerow([task["id"]] + [""] * 10)
    report = {
        "tasks": len(tasks),
        "municipalities": len(chosen),
        "regions": dict(Counter(t["region"] for t in tasks)),
        "target_municipalities": minimum_municipalities,
        "human_gold": 0,
        "distinct_task_template_families": len({t["family"] for t in tasks}),
        "family_counts": dict(Counter(t["family"] for t in tasks)),
        "diversity_gate": (
            "CANDIDATES_ONLY_CONFLICT_NEGATION_COMPARISON_MULTIHOP_STILL_REQUIRE_AUTHORING"
        ),
    }
    (output / "candidate-report.json").write_text(json.dumps(report, indent=2))
    return report
