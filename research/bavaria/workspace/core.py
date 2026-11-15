# ruff: noqa: E501
"""Bounded research queries; production answer semantics remain canonical.

This adapter does not pretend the experimental ledger is a production
SnapshotBinding. A distinct, checksummed workspace manifest binds its read-only
ledger and relation sidecar. Typed relation answers use CanonicalAnswerGenerator;
unqualified text results remain evidence discovery.
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import math
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from crawler.core.answer_context import EvidenceBudget, build_answer_context
from crawler.core.answer_generation import (
    AnswerRequest,
    CanonicalAnswerGenerator,
    GenerationInput,
    GenerationParameters,
    GenerationProvider,
    JsonlAnswerRunRecorder,
    OllamaGenerationProvider,
    ProviderResponse,
    render_answer_markdown,
)
from crawler.core.provenance import evidence_id_for
from crawler.core.retrieval import (
    DocumentSource,
    GraphAssertion,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
    RetrievedEvidence,
)
from research.bavaria.federation import source_connection
from research.experiments.e2_offline_20261024.kit.vendor.lexical import (
    norm,
    term_root,
    tokenise,
)

SCHEMA = "bavaria-workspace-manifest-v1"
RESERVE = 15 * 1024**3
OUTPUT_ALLOWANCE = 8 * 1024**2


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def read_only(path: str | Path) -> sqlite3.Connection:
    selected = Path(path).expanduser().resolve(strict=True)
    conn = sqlite3.connect(selected.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def write_guard(path: str | Path, maximum_bytes: int = OUTPUT_ALLOWANCE) -> None:
    selected = Path(path).expanduser().resolve()
    parent = selected.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < RESERVE + maximum_bytes:
        raise RuntimeError("Workspace output would consume the 15-GiB reserve")


def make_manifest(
    db: str | Path,
    relations_db: str | Path | None,
    output: str | Path,
    source_databases=None,
    disposition_db=None,
) -> dict:
    output = Path(output)
    if output.exists():
        raise FileExistsError("Workspace manifests are immutable; choose a fresh output path")
    write_guard(output, 64 * 1024)
    paths = {"ledger": Path(db).resolve(strict=True)}
    if relations_db:
        paths["relations"] = Path(relations_db).resolve(strict=True)
    extras = [Path(p).resolve(strict=True) for p in (source_databases or [])]
    for i, path in enumerate(extras):
        paths["source_" + str(i + 1)] = path
    if disposition_db and Path(disposition_db).resolve(strict=True) not in extras:
        raise ValueError("Disposition ledger must also be an explicit source ledger")
    payload: dict[str, Any] = {"schema": SCHEMA, "artifacts": {}}
    if extras:
        payload["source_ledger_order"] = [str(p) for p in extras]
        payload["disposition_ledger"] = (
            str(Path(disposition_db).resolve()) if disposition_db else None
        )
    for kind, path in paths.items():
        # A dirty WAL would make a main-file checksum incomplete. Bind only stable files.
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError(f"Close/checkpoint the writer before manifesting {kind}")
        payload["artifacts"][kind] = {"path": str(path), "sha256": file_hash(path)}
    payload["snapshot_id"] = "bavaria-workspace-sha256:" + identity(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def roots(text: str) -> list[str]:
    return [term_root(norm(token)) for token in tokenise(text)]


class Workspace:
    def __init__(
        self,
        db: str | Path,
        relations_db: str | Path | None = None,
        manifest: str | Path | None = None,
        source_databases=None,
        disposition_db=None,
    ):
        self.db = Path(db).resolve(strict=True)
        self.source_databases = [Path(p).resolve(strict=True) for p in (source_databases or [])]
        self.disposition_db = Path(disposition_db).resolve(strict=True) if disposition_db else None
        if self.disposition_db and self.disposition_db not in self.source_databases:
            raise ValueError("Disposition ledger must also be explicitly selected as a source")
        self.relations_db = Path(relations_db).resolve(strict=True) if relations_db else None
        self.manifest_path = Path(manifest).resolve(strict=True) if manifest else None
        with closing(source_connection(self.db, self.source_databases)) as conn:
            if (
                conn.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]
                != "bavaria-acquisition-v1"
            ):
                raise ValueError("Unsupported acquisition schema")
            self.municipalities = {
                r["ags"]: {
                    **json.loads(r["record"]),
                    "crawl_status": r["status"],
                    "crawl_checked_at": r["checked_at"],
                    "crawl_error": r["error"],
                }
                for r in conn.execute("SELECT * FROM municipalities")
            }
        if self.disposition_db:
            with read_only(self.disposition_db) as conn:
                for row in conn.execute("SELECT * FROM municipalities"):
                    if row["status"] in {"NOT_CHECKED", "RESOURCE_DEFERRED", "DEFERRED_STORAGE"}:
                        raise ValueError("Disposition overlay is incomplete")
                    self.municipalities[row["ags"]].update(
                        crawl_status=row["status"],
                        crawl_checked_at=row["checked_at"],
                        crawl_error=row["error"],
                    )
        self.manifest = None
        self.snapshot_id = "UNMANIFESTED_DISCOVERY_ONLY"
        if self.manifest_path:
            self.manifest = json.loads(self.manifest_path.read_bytes())
            body = {k: v for k, v in self.manifest.items() if k != "snapshot_id"}
            if self.manifest.get("schema") != SCHEMA or self.manifest.get("snapshot_id") != (
                "bavaria-workspace-sha256:" + identity(body)
            ):
                raise ValueError("Invalid workspace manifest identity")
            expected = {"ledger": self.db}
            if self.relations_db:
                expected["relations"] = self.relations_db
            for i, path in enumerate(self.source_databases):
                expected["source_" + str(i + 1)] = path
            if self.source_databases:
                if self.manifest.get("source_ledger_order") != [
                    str(p) for p in self.source_databases
                ] or self.manifest.get("disposition_ledger") != (
                    str(self.disposition_db) if self.disposition_db else None
                ):
                    raise ValueError("Explicit source/disposition roles differ from manifest")
            if set(expected) != set(self.manifest["artifacts"]):
                raise ValueError("Selected databases differ from manifested artifact set")
            for kind, path in expected.items():
                artifact = self.manifest["artifacts"][kind]
                if str(path) != artifact["path"] or file_hash(path) != artifact["sha256"]:
                    raise ValueError(f"Changed or incorrectly selected {kind} database")
                wal = Path(str(path) + "-wal")
                if wal.exists() and wal.stat().st_size:
                    raise ValueError("Workspace database has an unbound WAL")
            self.snapshot_id = self.manifest["snapshot_id"]
        self.relation_rows = []
        self.source_rows = {}
        self.evidence_rows = {}
        self.entities = {}
        if self.relations_db:
            with read_only(self.relations_db) as conn:
                metadata = dict(conn.execute("SELECT key,value FROM metadata"))
                if metadata.get("schema") != "bavaria-relational-v1":
                    raise ValueError("Unsupported relational research schema")
                self.entities = {
                    r["entity_id"]: dict(r) for r in conn.execute("SELECT * FROM entities")
                }
                self.source_rows = {
                    r["source_sha256"]: dict(r) for r in conn.execute("SELECT * FROM sources")
                }
                self.evidence_rows = {
                    r["evidence_id"]: dict(r) for r in conn.execute("SELECT * FROM evidence")
                }
                for row in conn.execute("SELECT * FROM relations ORDER BY relation_id"):
                    relation = dict(row)
                    relation["record"] = json.loads(row["record_json"])
                    evidence = self.evidence_rows[relation["evidence_id"]]
                    record = relation["record"]
                    if (
                        evidence["text"][record["start_char"] : record["end_char"]]
                        != record["quote"]
                    ):
                        raise ValueError("Relation quote does not match its exact source-unit span")
                    self.relation_rows.append(relation)

    def resolve_ags(self, supplied: list[str] | None, question: str = "") -> list[str]:
        if supplied:
            invalid = set(supplied) - self.municipalities.keys()
            if invalid:
                raise ValueError("AGS outside the official frame: " + ", ".join(sorted(invalid)))
            return list(dict.fromkeys(supplied))
        folded = norm(question)
        omit = {"an", "der", "a", "d", "am", "im", "i", "in", "bei", "ob", "auf", "dem"}
        tokens = [t for t in re.findall(r"\w+", folded) if t not in omit]
        matches = []
        for ags, record in self.municipalities.items():
            names = {record["name"], record["official_name"].split(",")[0]}
            for name in names:
                significant = [t for t in re.findall(r"\w+", norm(name)) if t not in omit]
                if len("".join(significant)) < 4:
                    continue
                for start in range(len(tokens) - len(significant) + 1):
                    if tokens[start : start + len(significant)] == significant:
                        matches.append((start, start + len(significant), ags))
        # Neuhaus/Pegnitz must not be misread as the separate city Pegnitz.
        accepted: list[tuple[int, int, str]] = []
        for start, end, ags in sorted(set(matches), key=lambda item: (item[0] - item[1], item)):
            overlaps = [m for m in accepted if start < m[1] and end > m[0]]
            if overlaps and all(end - start < m[1] - m[0] for m in overlaps):
                continue
            if any((start, end) == m[:2] and ags != m[2] for m in overlaps):
                raise ValueError("Ambiguous municipality name; supply explicit --ags")
            if not overlaps:
                accepted.append((start, end, ags))
        return list(dict.fromkeys(m[2] for m in sorted(accepted)))

    def dossiers(self, ags_list: list[str]) -> dict:
        targets = self.resolve_ags(ags_list)
        output = []
        with closing(source_connection(self.db, self.source_databases)) as conn:
            for ags in targets:
                item = self.municipalities[ags]
                docs = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT a.sha256,a.url,a.authority,d.parse_status FROM associations a "
                        "JOIN documents d USING(sha256) WHERE a.ags=? ORDER BY a.sha256,a.url",
                        (ags,),
                    )
                ]
                relations = [
                    r
                    for r in self.relation_rows
                    if ags in (r["record"].get("claim_scope_ags") or [])
                ]
                output.append(
                    {
                        "ags": ags,
                        "name": item["official_name"],
                        "region": item["region"],
                        "population": item["population"],
                        "population_date": item["population_date"],
                        "crawl_status": item["crawl_status"],
                        "source_versions": len({r["sha256"] for r in docs}),
                        "sources": docs,
                        "source_reviewed_relation_candidates": len(relations),
                        "relations_by_predicate": dict(Counter(r["predicate"] for r in relations)),
                        "policy_measure_total": None,
                        "verified_implemented_measure_total": None,
                    }
                )
        return {
            "schema": "bavaria-workspace-dossiers-v1",
            "snapshot_id": self.snapshot_id,
            "database": str(self.db),
            "additional_source_databases": [str(p) for p in self.source_databases],
            "municipalities": output,
            "comparison_basis": "source availability and equally defined provisional relations",
            "warnings": [
                "Not a climate-policy performance ranking.",
                "Missing sources or relations do not imply zero policy activity.",
            ],
        }

    def _unit_rows(self, ags: list[str], unit_ids: list[str] | None = None):
        if unit_ids == []:
            return
        conditions, parameters = [], []
        if ags:
            conditions.append("a.ags IN (" + ",".join("?" for _ in ags) + ")")
            parameters.extend(ags)
        if unit_ids is not None:
            conditions.append("u.unit_id IN (" + ",".join("?" for _ in unit_ids) + ")")
            parameters.extend(unit_ids)
        sql = (
            "SELECT u.*,min(a.url) source_url,group_concat(DISTINCT a.ags) frame_ags "
            "FROM units u JOIN associations a USING(sha256) "
            + ("WHERE " + " AND ".join(conditions) + " " if conditions else "")
            + "GROUP BY u.unit_id ORDER BY u.unit_id"
        )
        conn = source_connection(self.db, self.source_databases)
        try:
            for row in conn.execute(sql, parameters):
                yield dict(row)
        finally:
            conn.close()

    def discover(self, question: str, ags: list[str] | None = None, k: int = 8) -> dict:
        index = getattr(self, "discovery_index", None)
        if index is not None:
            return index.discover(question, ags, k)
        if not question.strip() or not 1 <= k <= 20:
            raise ValueError("Supply a question and k in 1..20")
        selected = self.resolve_ags(ags, question)
        terms = Counter(roots(question))
        # Explicit municipality filtering already supplies its name context.
        for target in selected:
            for token in roots(self.municipalities[target]["name"]):
                terms.pop(token, None)
        if not terms:
            return {"status": "NO_CONTENT_QUERY_TERMS", "results": [], "scope_ags": selected}
        documents = 0
        total_length = 0
        frequencies: Counter[str] = Counter()
        candidates = []
        workset_bytes = 0
        # Exact same corpus statistics/ranking, tokenized once rather than twice.
        # Retain sparse query counts, never a duplicate text corpus or index.
        for row in self._unit_rows(selected):
            counts = Counter(
                roots((row["heading"] or "") + " " + row["source_url"] + " " + row["text"])
            )
            documents += 1
            length = sum(counts.values())
            total_length += length
            nonzero = {term: counts[term] for term in terms if counts[term]}
            frequencies.update(nonzero.keys())
            if nonzero:
                candidate = (row["unit_id"], length, nonzero)
                workset_bytes += (
                    sys.getsizeof(candidate)
                    + sys.getsizeof(row["unit_id"])
                    + sys.getsizeof(nonzero)
                    + sys.getsizeof(length)
                )
                if workset_bytes > 64 * 1024**2:
                    raise ValueError("Query workset exceeds 64 MiB; narrow the municipality scope")
                candidates.append(candidate)
        average = total_length / documents if documents else 1
        heap: list[tuple[float, str]] = []
        for unit_id, length, nonzero in candidates:
            score = 0.0
            for term, frequency in nonzero.items():
                idf = math.log1p((documents - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                score += (
                    terms[term]
                    * idf
                    * frequency
                    * 2.2
                    / (frequency + 1.2 * (0.25 + 0.75 * length / average))
                )
            ranked = (score, unit_id)
            if len(heap) < k:
                heapq.heappush(heap, ranked)
            elif ranked > heap[0]:
                heapq.heapreplace(heap, ranked)
        rows = {row["unit_id"]: row for row in self._unit_rows(selected, [r[1] for r in heap])}
        results = []
        for score, unit_id in sorted(heap, key=lambda item: (-item[0], item[1])):
            row = rows[unit_id]
            row.update(
                score=score,
                source_frame_ags=row.pop("frame_ags").split(","),
                claim_scope_ags=None,
                source_type="segment",
                source_id=row["unit_id"],
            )
            results.append(row)
        return {
            "schema": "bavaria-workspace-discovery-v1",
            "question": question,
            "status": "EVIDENCE_DISCOVERY_NOT_A_COMPLETE_POLICY_ANSWER",
            "database": str(self.db),
            "additional_source_databases": [str(p) for p in self.source_databases],
            "snapshot_id": self.snapshot_id,
            "scope_ags": selected,
            "method": "streaming-context-bm25",
            "execution": "single-tokenization+sparse-top-k-refetch",
            "candidate_workset_bytes": workset_bytes,
            "candidate_units": documents,
            "query_terms": dict(terms),
            "results": results,
            "warnings": [
                "Source frame is not claim jurisdiction.",
                "Rank is not evidence entailment; no-source is not no-policy.",
            ],
        }

    def entity(self, query: str) -> list[str]:
        if query in self.entities:
            return [query]
        target = norm(query)
        exact = [key for key, entity in self.entities.items() if norm(entity["label"]) == target]
        return exact or [
            key
            for key, entity in self.entities.items()
            if len(target) >= 3 and target in norm(entity["label"])
        ]

    def graph_paths(
        self, start: str, target: str | None = None, max_hops: int = 3, limit: int = 20
    ) -> dict:
        if not 1 <= max_hops <= 3 or not 1 <= limit <= 100:
            raise ValueError("Graph exploration bounds are hops=1..3 and limit=1..100")
        starts = self.entity(start)
        targets = set(self.entity(target)) if target else None
        adjacency = defaultdict(list)
        for relation in self.relation_rows:
            adjacency[relation["subject_id"]].append((relation["object_id"], relation))
            adjacency[relation["object_id"]].append((relation["subject_id"], relation))
        output: list[dict] = []
        queue: list[tuple[str, list[str], list[dict]]] = [
            (entity, [entity], []) for entity in starts
        ]
        while queue and len(output) < limit:
            entity, visited, path = queue.pop(0)
            if path and (targets is None or entity in targets):
                output.append(
                    {
                        "entity_path": [
                            {"entity_id": key, "label": self.entities[key]["label"]}
                            for key in visited
                        ],
                        "relations": [self.relation_payload(r) for r in path],
                    }
                )
            if len(path) == max_hops:
                continue
            for endpoint, relation in adjacency[entity]:
                if endpoint not in visited:
                    queue.append((endpoint, visited + [endpoint], path + [relation]))
        return {
            "schema": "bavaria-workspace-paths-v1",
            "snapshot_id": self.snapshot_id,
            "paths": output,
            "max_hops": max_hops,
            "limit": limit,
            "interpretation": (
                "Undirected navigation over explicitly directed, provisional relations."
            ),
            "warnings": ["Each edge requires original review; a path is not causal inference."],
        }

    def relation_payload(self, relation: dict) -> dict:
        return {
            "relation_id": relation["relation_id"],
            "subject": self.entities[relation["subject_id"]]["label"],
            "predicate": relation["predicate"],
            "object": self.entities[relation["object_id"]]["label"],
            "evidence_id": relation["evidence_id"],
            "record": relation["record"],
            "required_source_ids": [
                self.evidence_rows[eid]["unit_id"]
                for eid in [
                    relation["evidence_id"],
                    *relation["record"].get("context_evidence_ids", []),
                ]
            ],
        }

    def temporal(self, entity: str) -> dict:
        ids = set(self.entity(entity))
        events = [
            self.relation_payload(r)
            for r in self.relation_rows
            if r["subject_id"] in ids or r["object_id"] in ids
        ]
        events.sort(key=lambda r: (str(r["record"].get("reference_time") or ""), r["relation_id"]))
        return {
            "schema": "bavaria-workspace-temporal-v1",
            "entity": entity,
            "events": events,
            "verified_status_transition": None,
            "warnings": [
                "Different publication dates or mentions do not alone prove a status change.",
                "Missing reference_time remains unknown; plans are not implementation.",
            ],
        }

    def _verify_relation_original(self, evidence: dict) -> None:
        source = self.source_rows[evidence["source_sha256"]]
        if not source["original_verified"]:
            raise ValueError("Relation source has not passed original-byte verification")
        path = Path(source["raw_path"]).resolve(strict=True)
        digest = hashlib.sha256()
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                digest.update(block)
        if digest.hexdigest() != evidence["source_sha256"]:
            raise ValueError("Original source changed since relation extraction")

    def relation_retrieval(
        self,
        question: str,
        ags: list[str] | None = None,
        k: int = 8,
        *,
        verify_original: Callable[[dict], None] | None = None,
        relation_ids: frozenset[str] | None = None,
    ) -> tuple[RetrievalResult, dict]:
        if not self.manifest or not self.relations_db:
            raise ValueError("Typed answers require a bound relational sidecar and manifest")
        targets = self.resolve_ags(ags, question)
        plan = {
            "scope_ags": targets,
            "current_status_requested": bool(
                re.search(r"\b(?:heute|aktuell|derzeit|gegenwärtig)\b", question, re.I)
            ),
            "implementation_requested": bool(
                re.search(
                    r"umgesetzt|fertiggestellt|abgeschlossen|realisiert|in Betrieb", question, re.I
                )
            ),
            "amount_requested": bool(
                re.search(r"wie viel|betrag|summe|budget|euro|€", question, re.I)
            ),
        }
        families = []
        if re.search(r"förder|finanz|finanzier|zuschuss|bezahlt|geldgeber", question, re.I):
            families = ["FUND", "GRANT", "SUPPORT", "FINANC", "PROGRAMME"]
        elif re.search(r"betreib|zuständig|verantwort", question, re.I):
            families = ["OPERAT", "ADMINIST", "RESPONS", "IMPLEMENT"]
        elif re.search(r"strateg|konzept|gehört|teil", question, re.I):
            families = ["PART_OF", "TARGET", "STRATEG", "IMPLEMENT"]
        if plan["amount_requested"]:
            families = []
        if re.search(r"bis wann|verlängert|förderphase|zeitraum|laufzeit", question, re.I):
            families = [
                "FUNDING_EXTENDED_UNTIL",
                "EXPIRED_ON",
                "STUDY_DURING",
                "HAS_EVIDENCE_LIMIT",
                "HAS_REPORTED_STATUS",
            ]
            if re.search(r"\bwer\b|fördergeber|geldgeber", question, re.I):
                families += ["FUNDS", "FUNDED_BY", "SUPPORTED_BY"]
        plan["shared_funder_requested"] = bool(
            re.search(r"beide|sowohl|gemeinsam|mehrere", question, re.I)
            and re.search(r"förder|finanz|geldgeber", question, re.I)
        )
        terms = set(roots(question))
        selected = []
        for relation in self.relation_rows:
            if relation_ids is not None and relation["relation_id"] not in relation_ids:
                continue
            record = relation["record"]
            scope = record.get("claim_scope_ags") or []
            if targets and not set(targets) & set(scope):
                continue
            if families and not any(part in relation["predicate"].upper() for part in families):
                continue
            if plan["implementation_requested"] and relation["predicate"] not in {
                "PLANS",
                "DEVELOPS",
                "PLANS_GRID_CONNECTION_SUPPORT",
                "HAS_REPORTED_STATUS",
                "HAS_EVIDENCE_LIMIT",
                "IMPLEMENTS",
                "COMPLETED_ON",
                "OPERATES",
            }:
                continue
            if plan["amount_requested"] and not any(
                part in relation["predicate"].upper()
                for part in ["AMOUNT", "COST", "BUDGET", "CAP", "RATE", "COUNT", "GENERATION"]
            ):
                continue
            labels = (
                self.entities[relation["subject_id"]]["label"]
                + " "
                + self.entities[relation["object_id"]]["label"]
            )
            score = (
                1
                if relation_ids is not None
                else len(terms & set(roots(labels + " " + record["quote"])))
            )
            if families and (targets or plan["shared_funder_requested"]):
                score = max(score, 1)
            if score:
                selected.append((score, relation))
        selected.sort(key=lambda pair: (-pair[0], pair[1]["relation_id"]))
        if plan["shared_funder_requested"]:
            grouped = defaultdict(list)
            for score, relation in selected:
                if relation["predicate"] == "FUNDS":
                    grouped[relation["subject_id"]].append((score, relation))
            selected = []
            for group in grouped.values():
                scopes = {
                    ags for _, r in group for ags in (r["record"].get("claim_scope_ags") or [])
                }
                if len({r["object_id"] for _, r in group}) > 1 and set(targets) <= scopes:
                    selected.extend(group)
        if plan["current_status_requested"]:
            selected = []
        admitted = []
        admitted_ids: set[str] = set()
        for score, relation in selected:
            required = {
                relation["evidence_id"],
                *relation["record"].get("context_evidence_ids", []),
            }
            if not required <= self.evidence_rows.keys():
                raise ValueError("Relation context refers to missing evidence")
            if len(admitted_ids | required) <= k:
                admitted.append((score, relation))
                admitted_ids |= required
        selected = admitted
        by_evidence = defaultdict(list)
        for score, relation in selected:
            by_evidence[relation["evidence_id"]].append((score, relation))
            for context_eid in relation["record"].get("context_evidence_ids", []):
                by_evidence.setdefault(context_eid, [])
        items = []
        run_id = "relational-source-review:" + self.manifest["artifacts"]["relations"]["sha256"]
        for ordinal, (eid, relations) in enumerate(by_evidence.items(), 1):
            evidence = self.evidence_rows[eid]
            (verify_original or self._verify_relation_original)(evidence)
            source = self.source_rows[evidence["source_sha256"]]
            source_id = evidence["unit_id"]
            content_id = evidence_id_for(
                "segment", source_id, hashlib.sha256(evidence["text"].encode()).hexdigest()
            )
            frame = source["source_frame_ags_json"]
            frame = json.loads(frame) if isinstance(frame, str) else frame
            assertions = tuple(
                GraphAssertion(
                    assertion_id=r["relation_id"],
                    graph_artifact_id="relation:" + r["relation_id"],
                    extraction_run_id=run_id,
                    subject_id=r["subject_id"],
                    subject_name=self.entities[r["subject_id"]]["label"],
                    relation_type=r["predicate"],
                    object_id=r["object_id"],
                    object_name=self.entities[r["object_id"]]["label"],
                    evidence_excerpt=r["record"]["quote"],
                )
                for _, r in relations
            )
            items.append(
                RetrievedEvidence(
                    database_snapshot_id=self.snapshot_id,
                    evidence_id=content_id,
                    source_type="segment",
                    source_id=source_id,
                    segment_id=source_id,
                    chunk_id=None,
                    document=DocumentSource(
                        "source-version:" + evidence["source_sha256"],
                        source["source_url"],
                        evidence["heading"],
                        tuple(frame),
                    ),
                    evidence_text=evidence["text"],
                    extraction_run_ids=(run_id,),
                    graph_artifact_ids=tuple(a.graph_artifact_id for a in assertions),
                    assertions=assertions,
                    retrieval_traces=(
                        RetrievalTrace(
                            "workspace-typed-graph",
                            ordinal,
                            float(relations[0][0]) if relations else 0.0,
                        ),
                    ),
                    retrieval_score=float(relations[0][0]) if relations else 0.0,
                    rank=ordinal,
                )
            )
        projection = "workspace-relations:" + self.manifest["artifacts"]["relations"]["sha256"]
        retrieval = RetrievalResult(
            question,
            "graph",
            self.snapshot_id,
            projection,
            tuple(items),
            warnings=(
                "PROVISIONAL agent original review, not independently validated policy facts.",
                "Citation municipality IDs are source frames, not inferred claim jurisdiction.",
            ),
        )
        plan["selected_relations"] = [self.relation_payload(r) for _, r in selected]
        plan["scope_evidence_present"] = {
            ags: any(ags in (r["record"].get("claim_scope_ags") or []) for _, r in selected)
            for ags in targets
        }
        return retrieval, plan


class FixedResearchRetriever:
    """Feed a validated research graph result into the unchanged canonical generator."""

    def __init__(self, result: RetrievalResult, manifest: Path):
        self.result, self.manifest = result, manifest

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if Path(request.manifest_path).resolve() != self.manifest:
            raise ValueError("Generation selected another workspace manifest")
        if request.query != self.result.query or request.projection_id != self.result.projection_id:
            raise ValueError("Generation query/projection differs from prepared research retrieval")
        return self.result


class RelationComposer:
    """Explicit deterministic source-report composition; no raw-fragment answer fallback."""

    name = "workspace_rules"

    def __init__(self, plan: dict):
        self.plan = plan

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        blocks = [
            json.loads(block)
            for block in re.findall(
                r"<untrusted_evidence>\n(.*?)\n</untrusted_evidence>",
                generation_input.evidence_context,
                re.S,
            )
        ]
        statements: list[str] = []
        labels_by_source = {b["metadata"]["source_id"]: b["citation_label"] for b in blocks}
        for block in blocks:
            for assertion in block["graph_assertions"]:
                subject = assertion["subject"].replace("[", "(").replace("]", ")")
                obj = assertion["object"].replace("[", "(").replace("]", ")")
                # Presentation only: avoid a false sentence boundary in the legal suffix.
                # Full source names and original evidence remain unchanged in assertions/citations.
                subject = re.sub(r"\bCo\.\s+KG\b", "Co KG", subject)
                obj = re.sub(r"\bCo\.\s+KG\b", "Co KG", obj)
                predicate = assertion["relation"]
                relation = next(
                    r
                    for r in self.plan["selected_relations"]
                    if r["relation_id"] == assertion["assertion_id"]
                )
                required = relation["required_source_ids"]
                if not set(required) <= labels_by_source.keys():
                    # A context budget may exclude a required co-reference/scope unit.
                    # Never keep the derived claim after dropping its essential context.
                    continue
                citation_suffix = "".join(f"[{labels_by_source[source]}]" for source in required)
                templates = {
                    "COMMISSIONS": "Die Quelle nennt {subject} als Auftraggeber für „{obj}“",
                    "AUTHORS": "Die Quelle nennt {subject} als Bearbeiter von „{obj}“",
                    "COMMISSIONED_FOR": "Die Quelle nennt {subject} als beauftragten Akteur für „{obj}“",
                    "FUNDING_DURING": "Die Quelle nennt für „{subject}“ die Förderphase {obj}",
                    "ADOPTED_ON": "Die Quelle nennt für „{subject}“ das Beschlussdatum {obj}",
                    "OCCURRED_ON": "Die Quelle datiert „{subject}“ auf {obj}",
                    "PRESENTS_PROPOSED_MEASURES": "Die Quelle nennt für „{subject}“ {obj} als Vorschläge, nicht als umgesetzt",
                    "REPORT_DATED": "Die Quelle datiert „{subject}“ auf {obj}",
                    "HAS_GRANT_CAP": "Die Quelle nennt für „{subject}“ den Förderhöchstbetrag {obj}; dies ist keine Auszahlung",
                    "PROJECTED_ANNUAL_GENERATION": "Die Quelle prognostiziert für „{subject}“ {obj} jährlich; dies ist kein Messwert",
                    "OFFERS": "Die Quelle nennt {subject} als Anbieter von „{obj}“",
                    "OPPOSES": "Die Quelle berichtet, dass {subject} den Vorschlag „{obj}“ ablehnt",
                    "TARGETS": "Die Quelle nennt für „{subject}“ das Ziel „{obj}“; damit ist keine Zielerreichung belegt",
                    "STARTED_PLANNING": "Die Quelle nennt für „{subject}“ den Planungsbeginn {obj}",
                    "HAS_REPORTED_VALUE": "Die Quelle nennt für „{subject}“ den berichteten Wert {obj}; dies ist keine unabhängige Messvalidierung",
                    "INSTANCE_OF": "Die Quellenanalyse ordnet „{subject}“ der benannten Reihe „{obj}“ zu",
                    "DATA_REFERENCE_YEAR": "Die Quelle nennt für „{subject}“ das Datenbezugsjahr {obj}",
                    "EXPECTED_REPORT": "Die Quelle erwartet für „{subject}“ den Bericht bis {obj}; Fertigstellung ist damit nicht belegt",
                    "NAMED_CONTACT_FOR": "Die Quelle nennt {subject} als Kontakt für „{obj}“; Eigentum oder Betreiberrolle ist damit nicht belegt",
                    "CREDITS": "Die Quelle nennt „{obj}“ als Beteiligten an „{subject}“",
                    "LISTS": "Die Quelle verlinkt „{obj}“ im Index „{subject}“",
                    "ON_SITE_DATE": "Die Quelle nennt für „{subject}“ den Vor-Ort-Termin {obj}",
                    "TELEPHONE_DATE": "Die Quelle nennt für „{subject}“ den telefonischen Termin {obj}",
                    "PLANNED_OPERATION": "Die Quelle nennt für „{subject}“ die geplante Inbetriebnahme {obj}; eine erfolgte Inbetriebnahme folgt daraus nicht",
                    "UPDATES": "Die Quelle beschreibt „{subject}“ als Fortschreibung von „{obj}“",
                    "REPORTED_DEVELOPMENT_YEAR": "Die Quelle nennt für „{subject}“ das Entwicklungsjahr {obj}",
                }
                if predicate in templates:
                    sentence = templates[predicate].format(subject=subject, obj=obj)
                elif predicate == "FUNDS":
                    sentence = (
                        f"Die Quelle nennt {subject} als Förder-/Unterstützungsakteur für „{obj}“"
                    )
                elif predicate in ("FUNDED_BY", "SUPPORTED_BY"):
                    sentence = (
                        f"Die Quelle nennt {obj} als Förder-/Unterstützungsakteur für „{subject}“"
                    )
                elif predicate == "PROJECT_CARRIER_FOR":
                    sentence = f"Die Quelle nennt {subject} als Projektträger für „{obj}“"
                elif predicate == "DEVELOPS":
                    sentence = f"Die Quelle nennt {subject} für die Entwicklung von „{obj}“"
                elif predicate == "CONDUCTS_STUDY":
                    sentence = f"Die Quelle nennt {subject} für die Untersuchung „{obj}“"
                elif predicate == "PARTICIPATES_IN":
                    sentence = f"Die Quelle nennt {subject} als Beteiligten an „{obj}“"
                elif predicate == "NAMED_EXTERNAL_FUNDING_OPTION_FOR":
                    sentence = f"Die Quelle nennt „{subject}“ als externe Förderoption für {obj}"
                elif predicate == "HAS_ANNUAL_BUDGET":
                    sentence = f"Die Quelle nennt für „{subject}“ ein jährliches Budget von {obj}"
                elif predicate == "HAS_ESTIMATED_COST":
                    sentence = f"Die Quelle nennt für „{subject}“ geschätzte Kosten von {obj}"
                elif predicate == "HAS_FUNDING_RATE":
                    sentence = f"Die Quelle nennt für „{subject}“ eine Förderquote von {obj}"
                elif predicate == "CONTRACTED_FOR":
                    sentence = f"Die Quelle nennt {subject} als beauftragten Akteur für „{obj}“"
                elif predicate == "OFFERS_PROGRAMME":
                    sentence = f"Die Quelle nennt „{obj}“ als Förderprogramm von {subject}"
                elif predicate == "REPORTS_NO_LOCAL_PROGRAMME":
                    sentence = f"Die Quelle berichtet für {subject}: „{obj}“"
                elif predicate == "FUNDING_EXTENDED_UNTIL":
                    sentence = (
                        f"Die Quelle nennt für „{subject}“ eine verlängerte Förderphase bis {obj}"
                    )
                elif predicate == "EXPIRED_ON":
                    sentence = f"Die Quelle nennt für „{subject}“ das Ende {obj}"
                elif predicate == "STUDY_DURING":
                    sentence = f"Die Quelle nennt für „{subject}“ den Untersuchungszeitraum {obj}"
                elif predicate in ("HAS_REPORTED_STATUS", "HAS_EVIDENCE_LIMIT"):
                    sentence = f"Die Quelle berichtet für „{subject}“: „{obj}“"
                elif predicate == "OPERATES":
                    sentence = f"Die Quelle benennt {subject} als Betreiber von „{obj}“"
                elif predicate == "PART_OF":
                    sentence = (
                        f"Die Quelle ordnet „{subject}“ dem übergeordneten Zusammenhang „{obj}“ zu"
                    )
                elif predicate in ("PLANS", "PLANS_GRID_CONNECTION_SUPPORT"):
                    sentence = f"Die Quelle beschreibt „{obj}“ als Planung von {subject}"
                else:
                    sentence = (
                        f"Die Quelle berichtet die Relation „{predicate}“ "
                        f"zwischen {subject} und „{obj}“"
                    )
                record = next(
                    r["record"]
                    for r in self.plan["selected_relations"]
                    if r["relation_id"] == assertion["assertion_id"]
                )
                if record.get("reference_time"):
                    sentence += f" (Quellenzeitbezug: {record['reference_time']})"
                # Existing grounding splits at abbreviations too; repeat the same citation
                # on resulting source-name clauses rather than altering canonical validation.
                clauses = [c.strip() for c in re.split(r"(?<=[.!?])\s+", sentence) if c.strip()]
                statements.extend(clause + f" {citation_suffix}." for clause in clauses)
        if not statements:
            text = (
                "INSUFFICIENT_EVIDENCE: No scope-qualified typed relation supports this question."
            )
        else:
            text = "\n".join(dict.fromkeys(statements))
            planning = [
                r
                for r in self.plan["selected_relations"]
                if "PLAN" in r["predicate"]
                or "PLAN" in str(r["record"].get("modality", "")).upper()
            ]
            if self.plan["implementation_requested"] and planning:
                label = labels_by_source[planning[0]["required_source_ids"][0]]
                text += (
                    "\nDie genannten Planungsbelege belegen keine abgeschlossene Umsetzung ["
                    + label
                    + "]."
                )
        return ProviderResponse(
            text=text, model=generation_input.model, finish_reason="deterministic"
        )


def complete_context_bundles(
    retrieval: RetrievalResult, plan: dict, budget: EvidenceBudget
) -> tuple[RetrievalResult, dict]:
    context = build_answer_context(retrieval, budget=budget)
    admitted = set(context.selected_evidence_ids)
    source_ids = {item.source_id for item in retrieval.items if item.evidence_id in admitted}
    relations = [
        r for r in plan["selected_relations"] if set(r["required_source_ids"]) <= source_ids
    ]
    if plan.get("shared_funder_requested"):
        scopes = {ags for r in relations for ags in (r["record"].get("claim_scope_ags") or [])}
        if len({r["object"] for r in relations}) < 2 or not set(plan["scope_ags"]) <= scopes:
            relations = []
    relation_ids = {r["relation_id"] for r in relations}
    required_sources = {source for r in relations for source in r["required_source_ids"]}
    kept: list[RetrievedEvidence] = []
    for item in retrieval.items:
        if item.source_id not in required_sources:
            continue
        kept.append(
            replace(
                item,
                rank=len(kept) + 1,
                assertions=tuple(a for a in item.assertions if a.assertion_id in relation_ids),
            )
        )
    updated = {
        **plan,
        "selected_relations": relations,
        "context_bundle_preflight": {
            "max_characters": budget.max_characters,
            "excluded_relation_ids": sorted(
                {r["relation_id"] for r in plan["selected_relations"]} - relation_ids
            ),
        },
    }
    updated["scope_evidence_present"] = {
        ags: any(ags in (r["record"].get("claim_scope_ags") or []) for r in relations)
        for ags in plan["scope_ags"]
    }
    return replace(retrieval, items=tuple(kept)), updated


def answer(
    workspace: Workspace,
    question: str,
    run_log: str | Path,
    ags: list[str] | None = None,
    k: int = 8,
    provider: str = "workspace_rules",
    model: str = "workspace-relation-composer-v1",
    ollama_url: str | None = None,
    evidence_budget: EvidenceBudget | None = None,
    generation_parameters: GenerationParameters | None = None,
) -> dict:
    if not 1 <= k <= 12:
        raise ValueError("Answer evidence bound is k=1..12")
    write_guard(run_log, 512 * 1024)
    Path(run_log).parent.mkdir(parents=True, exist_ok=True)
    retrieval, plan = workspace.relation_retrieval(question, ags, k)
    budget = evidence_budget or EvidenceBudget(
        max_items=k, max_characters=4500 if provider == "ollama" else 18000
    )
    retrieval, plan = complete_context_bundles(retrieval, plan, budget)
    if workspace.manifest_path is None:
        raise ValueError("Typed answers require an explicit manifest")
    providers: dict[str, GenerationProvider] = {"workspace_rules": RelationComposer(plan)}
    if provider == "ollama":
        parsed = urlsplit(ollama_url or "")
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only an explicitly selected local Ollama endpoint is supported")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama endpoint must not contain credentials, query or fragment")
        providers["ollama"] = OllamaGenerationProvider(ollama_url or "")
    elif provider != "workspace_rules":
        raise ValueError("Unknown generation provider; no fallback is permitted")
    request = AnswerRequest(
        question=question,
        database_snapshot_id=workspace.snapshot_id,
        retrieval_request=RetrievalRequest(
            query=question,
            manifest_path=str(workspace.manifest_path),
            mode=RetrievalMode.GRAPH,
            top_k=k,
            projection_id=retrieval.projection_id,
        ),
        generation_provider=provider,
        generation_model=model,
        generation_parameters=generation_parameters
        or GenerationParameters(
            temperature=0,
            max_output_tokens=384 if provider == "ollama" else 512,
            seed=42,
            timeout_seconds=90,
            context_window_tokens=2048 if provider == "ollama" else None,
            num_threads=1 if provider == "ollama" else None,
            think=False if provider == "ollama" else None,
        ),
        evidence_budget=budget,
    )
    generator = CanonicalAnswerGenerator(
        retriever=FixedResearchRetriever(retrieval, workspace.manifest_path),
        providers=providers,
        run_recorder=JsonlAnswerRunRecorder(run_log),
    )
    result = generator.generate(request)
    return {
        "schema": "bavaria-workspace-answer-v1",
        "generation_qualification": (
            "EXPERIMENTAL_MODEL_FAILED_PILOT_DO_NOT_TREAT_AS_VALIDATED_ANSWER"
            if provider == "ollama"
            else "PROVISIONAL_DETERMINISTIC_RELATION_COMPOSITION"
        ),
        "canonical_answer": result.to_dict(),
        "canonical_markdown": render_answer_markdown(result),
        "question_plan": plan,
        "databases": {"ledger": str(workspace.db), "relations": str(workspace.relations_db)},
        "answer_scope": "selected provisional original-reviewed relation corpus",
        "answerability": result.status.value
        if result.status.value != "answered"
        else (
            "partial_scope" if not all(plan["scope_evidence_present"].values()) else "source_scoped"
        ),
        "limitations": [
            "No present-world completeness or independent factual validation.",
            "No paid provider or silent fallback; current-status questions abstain.",
            "Claim-scope localities are supported instances, not exhaustive service areas.",
        ],
    }
