"""Bavaria composition of existing Workspace and canonical application contracts."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from crawler.core.answer_generation import AnswerResult
from crawler.core.provenance import evidence_id_for
from crawler.core.retrieval import (
    CanonicalRetriever,
    DocumentSource,
    RetrievalError,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
    RetrievedEvidence,
    SearchScope,
)
from crawler.web.service import (
    ProviderAcceptance,
    ProviderAvailability,
    ProviderGate,
    ResearchApplicationService,
)
from research.bavaria.federation import source_connection
from research.bavaria.workspace.core import Workspace, norm, read_only


class BavariaPreviewRetriever(CanonicalRetriever):
    supported_modes = ["lexical", "lexical_expanded", "graph"]

    def __init__(
        self, workspace: Workspace, *, original_roots=None, source_presentation="archived"
    ):
        super().__init__()
        self.workspace = workspace
        if source_presentation not in {"archived", "linked_originals"}:
            raise ValueError("Unknown source presentation")
        self.source_presentation = source_presentation
        self.original_roots = tuple(archive_roots() if original_roots is None else original_roots)
        if not self.original_roots:
            raise ValueError("Explicit original roots must not be empty")
        paths = [workspace.db, *workspace.source_databases]
        if workspace.relations_db:
            paths.append(workspace.relations_db)
        if workspace.manifest_path:
            paths.append(workspace.manifest_path)
        self.identities = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}
        if source_presentation == "linked_originals":
            with read_only(workspace.db) as conn:
                for row in conn.execute("SELECT sha256 FROM documents"):
                    proof = conn.execute(
                        "SELECT value FROM metadata WHERE key=?",
                        ("pilot_verified_original:" + row[0],),
                    ).fetchone()
                    if not proof or proof[0] != row[0]:
                        raise ValueError(
                            "Linked-original mode requires export verification receipts"
                        )

    def check_identity(self):
        for path, expected in self.identities.items():
            if (path.stat().st_size, path.stat().st_mtime_ns) != expected:
                raise RetrievalError("snapshot_changed")
            wal = Path(str(path) + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise RetrievalError("snapshot_changed")

    def retrieve(
        self, request: RetrievalRequest, *, relation_ids: frozenset[str] | None = None
    ) -> RetrievalResult:
        started = time.perf_counter()
        request.validate()
        self.check_identity()
        w = self.workspace
        if Path(request.manifest_path).resolve() != w.manifest_path or request.top_k > 20:
            raise RetrievalError("unsupported_preview_configuration")
        if request.mode.value not in self.supported_modes:
            raise RetrievalError("method_unavailable_for_snapshot")
        if set(request.municipality_ids) - w.municipalities.keys():
            raise RetrievalError("invalid_municipality_scope")
        question = request.query + (" " + request.topic if request.topic else "")
        if request.mode is RetrievalMode.GRAPH:
            if request.reference_year:
                raise RetrievalError("graph_year_filter_not_supported")
            with closing(source_connection(w.db, w.source_databases)) as conn:
                verified: set[str] = set()

                def verify(evidence):
                    sha = evidence["source_sha256"]
                    if sha in verified:
                        return
                    if not w.source_rows[sha]["original_verified"]:
                        raise RetrievalError("original_not_available")
                    if self.source_presentation == "linked_originals":
                        receipt = conn.execute(
                            "SELECT value FROM metadata WHERE key=?",
                            ("pilot_verified_original:" + sha,),
                        ).fetchone()
                        if not receipt or receipt[0] != sha:
                            raise RetrievalError("export_original_verification_missing")
                    else:
                        record = conn.execute(
                            "SELECT raw_path,raw_bytes FROM documents WHERE sha256=?", (sha,)
                        ).fetchone()
                        _original_bytes(record, sha, self.original_roots)
                    verified.add(sha)

                result, plan = w.relation_retrieval(
                    question,
                    list(request.municipality_ids) or None,
                    request.top_k,
                    verify_original=verify,
                    relation_ids=relation_ids,
                )
            # An explicitly named indexed entity is a navigation constraint, not an
            # oracle answer. Keep the existing graph engine and all required context.
            named = {
                key
                for key, entity in w.entities.items()
                if len(norm(entity["label"])) >= 12
                and re.search(
                    r"(?<!\w)" + re.escape(norm(entity["label"])) + r"(?!\w)",
                    norm(question),
                )
            }
            relations = {r["relation_id"]: r for r in plan["selected_relations"]}
            kept = {
                assertion.assertion_id
                for item in result.items
                for assertion in item.assertions
                if not named or assertion.subject_id in named or assertion.object_id in named
            }
            required = {sid for key in kept for sid in relations[key]["required_source_ids"]}
            graph_items: list[RetrievedEvidence] = []
            for item in result.items:
                if item.source_id not in required:
                    continue
                assertions = []
                for assertion in item.assertions:
                    if assertion.assertion_id not in kept:
                        continue
                    relation = relations[assertion.assertion_id]
                    record = relation["record"]
                    reference_time = record.get("reference_time")
                    scope = record.get("claim_scope_ags")
                    assertions.append(
                        replace(
                            assertion,
                            required_source_ids=tuple(relation["required_source_ids"]),
                            reference_time=str(reference_time)
                            if reference_time is not None
                            else None,
                            claim_scope_ags=tuple(scope) if scope is not None else None,
                            validation="PROVISIONAL_NOT_INDEPENDENTLY_REVIEWED",
                            subject_type=w.entities.get(assertion.subject_id, {}).get(
                                "entity_type"
                            ),
                            object_type=w.entities.get(assertion.object_id, {}).get("entity_type"),
                        )
                    )
                graph_items.append(
                    replace(
                        item,
                        assertions=tuple(assertions),
                        rank=len(graph_items) + 1,
                        graph_artifact_ids=tuple(a.graph_artifact_id for a in assertions),
                    )
                )
            warnings = result.warnings
            if named:
                warnings += (
                    "Named-entity restriction after bounded selection: " + ", ".join(sorted(named)),
                )
            return response_budget(
                replace(
                    result,
                    items=tuple(graph_items),
                    query=request.query,
                    warnings=warnings,
                    search_scope=make_scope(request, plan["scope_ags"], "provisional_claim_scope"),
                    diagnostics={
                        "method": "stored-provisional-graph",
                        "graph_version": request.projection_id,
                        "effective_ags": plan["scope_ags"],
                        "graph_entities": len(w.entities),
                        "graph_relations": len(w.relation_rows)
                        if hasattr(w, "relation_rows")
                        else None,
                        "selected_relations": len(kept),
                        "after_named_entity_filter": len(graph_items),
                        "fallback": False,
                        "retrieval_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    },
                )
            )
        raw = w.discover(question, list(request.municipality_ids) or None, k=request.top_k)
        if request.mode is RetrievalMode.LEXICAL_EXPANDED:
            expanded_terms = expand_query(question)
            local_index = getattr(w, "discovery_index", None)
            if local_index is not None:
                expanded_terms = tuple(
                    dict.fromkeys((*expanded_terms, *local_index.expand_query(question)))
                )
                # Generic Verkehr matches temporary road closures, not necessarily
                # climate mobility policy. Only this exploratory local composition changes.
                expanded_terms = tuple(t for t in expanded_terms if t != "verkehr")
            if expanded_terms:
                extra = w.discover(
                    question + " " + " ".join(expanded_terms),
                    list(request.municipality_ids) or None,
                    k=request.top_k,
                )
                seen = {r["unit_id"] for r in raw["results"]}
                raw["results"] = (
                    raw["results"] + [r for r in extra["results"] if r["unit_id"] not in seen]
                )[: request.top_k]
                raw["method"] = (
                    "local-index-expansion-v2"
                    if local_index is not None
                    else "baseline-preserving-lexical-expansion-v1"
                )
                if "diagnostics" in raw:
                    raw["diagnostics"]["expansion"] = {
                        "version": "explicit-domain-rules-v2",
                        "terms": list(expanded_terms),
                        "strategy": "baseline-first-union-bounded-top-k",
                        "expanded_run": extra["diagnostics"],
                        "merged_candidates": len(raw["results"]),
                    }
        selected = raw["results"]
        if request.reference_year:
            selected = [r for r in selected if str(request.reference_year) in r["text"]]
        diagnostics = raw.get("diagnostics")
        if diagnostics is not None:
            diagnostics.update(
                method=raw["method"],
                after_year_filter=len(selected),
                text_year=request.reference_year,
                year_semantics="mention_after_top_k_not_temporal_validity",
                fallback=False,
                final_source_ids=[r["sha256"] for r in selected],
                final_unit_ids=[r["unit_id"] for r in selected],
            )
        items = []
        for rank, row in enumerate(selected, 1):
            locator = row["heading"] or "Originalabschnitt"
            locator += f" · Einheit {row['ordinal']}"
            if row["page_ref"]:
                locator += f" · physische Seite {row['page_ref']}"
            items.append(
                RetrievedEvidence(
                    database_snapshot_id=w.snapshot_id,
                    evidence_id=evidence_id_for(
                        "segment", row["unit_id"], hashlib.sha256(row["text"].encode()).hexdigest()
                    ),
                    source_type="segment",
                    source_id=row["unit_id"],
                    segment_id=row["unit_id"],
                    chunk_id=None,
                    document=DocumentSource(
                        "source-version:" + row["sha256"],
                        row["source_url"],
                        locator,
                        tuple(row["source_frame_ags"]),
                    ),
                    evidence_text=row["text"],
                    extraction_run_ids=(),
                    graph_artifact_ids=(),
                    assertions=(),
                    retrieval_traces=(
                        RetrievalTrace(
                            raw.get("method", "streaming-context-bm25"), rank, float(row["score"])
                        ),
                    ),
                    retrieval_score=float(row["score"]),
                    rank=rank,
                )
            )
        warning = "Quellenrahmen ist keine Aussagegeltung. Inhalt fachlich unvalidiert."
        lexical_warnings: tuple[str, ...] = (warning,)
        if request.reference_year:
            lexical_warnings += ("Jahresnennung nach top-k gefiltert, keine Gültigkeitsprüfung.",)
        if diagnostics is not None:
            diagnostics["retrieval_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return response_budget(
            RetrievalResult(
                request.query,
                request.mode.value,
                w.snapshot_id,
                None,
                tuple(items),
                warnings=lexical_warnings,
                search_scope=make_scope(request, raw["scope_ags"], "source_association"),
                diagnostics=diagnostics,
            )
        )


def make_scope(request: RetrievalRequest, effective: list[str], basis: str) -> SearchScope:
    resolution = "explicit" if request.municipality_ids else "from_query" if effective else "all"
    return SearchScope(
        request.municipality_ids,
        tuple(effective),
        resolution,
        basis,
        request.topic,
        request.reference_year,
    )


class BavariaApplicationService(ResearchApplicationService):
    def __init__(
        self, workspace: Workspace, *, original_roots=None, source_presentation="archived"
    ):
        if not workspace.manifest_path or not workspace.manifest:
            raise ValueError("Preview requires an explicit manifested workspace")
        self.workspace = workspace
        if source_presentation not in {"archived", "linked_originals"}:
            raise ValueError("Unknown source presentation")
        self.source_presentation = source_presentation
        self.original_roots = tuple(archive_roots() if original_roots is None else original_roots)
        if not self.original_roots:
            raise ValueError("Explicit original roots must not be empty")
        projection = (
            "workspace-relations:" + workspace.manifest["artifacts"]["relations"]["sha256"]
            if workspace.relations_db
            else None
        )
        retriever = BavariaPreviewRetriever(
            workspace, original_roots=self.original_roots, source_presentation=source_presentation
        )
        if not workspace.relations_db:
            retriever.supported_modes = ["lexical", "lexical_expanded"]
        super().__init__(
            retriever=retriever,
            generator=None,
            manifest_path=str(workspace.manifest_path),
            database_snapshot_id=workspace.snapshot_id,
            projection_id=projection,
            provider_gate=ProviderGate(
                ProviderAvailability.RETRIEVAL_ONLY_READY,
                ProviderAcceptance.NOT_APPLICABLE,
                "No generator configured for this snapshot.",
                None,
                None,
                "Belegsuche verfügbar. Antwortgenerierung ist nicht freigegeben.",
            ),
        )
        self.retrieved_at: dict[str, str] = {}
        for path in [workspace.db, *workspace.source_databases]:
            with read_only(path) as ledger:
                # Minimized pilot bundles retain acquisition timestamps as source
                # metadata, without carrying crawl request URLs or diagnostics.
                for key, at in ledger.execute(
                    "SELECT key,value FROM metadata WHERE key LIKE 'pilot_retrieved_at:%'"
                ):
                    sha = key.removeprefix("pilot_retrieved_at:")
                    if at and at > self.retrieved_at.get(sha, ""):
                        self.retrieved_at[sha] = at
                for sha, at in ledger.execute(
                    "SELECT sha256,max(at) FROM requests WHERE sha256 IS NOT NULL GROUP BY sha256"
                ):
                    if at and at > self.retrieved_at.get(sha, ""):
                        self.retrieved_at[sha] = at
        c = source_connection(workspace.db, workspace.source_databases)
        try:
            self.coverage = {
                "municipalities": len(workspace.municipalities),
                "original_versions": c.execute("SELECT count(*) FROM documents").fetchone()[0],
                "text_units": c.execute("SELECT count(*) FROM units").fetchone()[0],
                "municipalities_with_text": c.execute(
                    "SELECT count(DISTINCT a.ags) FROM associations a JOIN units u USING(sha256)"
                ).fetchone()[0],
            }
        finally:
            c.close()

    def original(self, result_id: str, evidence_id: str, raw: bool = False):
        if self.source_presentation == "linked_originals":
            raise RetrievalError("archive_distribution_disabled")
        assert isinstance(self.retriever, BavariaPreviewRetriever)
        self.retriever.check_identity()
        stored = self.store.get(result_id)
        if stored is None:
            raise RetrievalError("original_not_available")
        canonical = stored.canonical
        retrieval = canonical.retrieval_result if isinstance(canonical, AnswerResult) else canonical
        item = next((x for x in retrieval.items if x.evidence_id == evidence_id), None)
        if item is None:
            raise RetrievalError("original_not_available")
        sha = item.document.document_id.removeprefix("source-version:")
        c = source_connection(self.workspace.db, self.workspace.source_databases)
        try:
            record = c.execute(
                "SELECT raw_path,raw_bytes FROM documents WHERE sha256=?", (sha,)
            ).fetchone()
        finally:
            c.close()
        if not record or record["raw_bytes"] > 16 * 1024**2:
            raise RetrievalError("original_not_available")
        return read_original(record, sha, self.original_roots, raw=raw)

    def status(self):
        out = super().status()
        if getattr(self.workspace, "discovery_index", None) is not None:
            out["local_validation_studio"] = True
            out["lexical_execution"] = "sqlite-fts5-normalized-bm25; global IDF, scoped candidates"
            out["method_catalog"] = self.method_catalog()
        relation = next(
            (r for r in self.workspace.relation_rows if r["predicate"] == "FUNDS"), None
        )
        label = (
            self.workspace.entities[relation["subject_id"]]["label"] if relation else "Förderung"
        )
        out["graph_example"] = "Welche Beziehungen nennen die Quellen zu " + label + "?"
        out.update(
            application="Klima Research",
            local_preview=True,
            public_release_cleared=False,
            coverage=self.coverage,
            human_gold_available=False,
            municipalities=[
                {
                    "ags": ags,
                    "name": r["name"],
                    "district": r.get("district", ""),
                    "region": r.get("region", ""),
                }
                for ags, r in sorted(self.workspace.municipalities.items())
            ],
            examples=[
                {
                    "question": "Welche Quellen beschreiben die kommunale Wärmeplanung?",
                    "municipality": name,
                }
                for name in ["Stammbach", "Unterhaching", "Strahlungen"]
            ],
            scope_notice="Technische Quellenabdeckung, kein vollständiges Politik-Inventar.",
        )
        if getattr(self, "pilot_mode", False):
            out["pilot_mode"] = True
            out["research_preview"] = {
                "scientific_validation": "PENDING_NOT_A_RELEASE_PREREQUISITE",
                "results": "REAL_PROVISIONAL",
                "source_presentation": self.source_presentation,
            }
            out["examples"] = self.preview_examples
        return out

    def method_catalog(self):
        w = self.workspace
        graph_sources = {r["record"]["source_sha256"] for r in w.relation_rows}
        ie = sorted(
            {
                (
                    r["record"].get("extraction_model", "UNKNOWN"),
                    r["record"].get("prompt_version", "UNKNOWN"),
                )
                for r in w.relation_rows
            }
        )
        return {
            "snapshot": w.snapshot_id,
            "graph_version": self.projection_id,
            "graph_sources": len(graph_sources),
            "graph_entities": len(w.entities),
            "graph_relations": len(w.relation_rows) if hasattr(w, "relation_rows") else None,
            "ie_runs": [{"model": m, "prompt_version": p} for m, p in ie],
            "ie_source_ids": sorted(graph_sources),
            "methods": [
                {
                    "id": "lexical",
                    "label": "BM25",
                    "available": True,
                    "coverage": self.coverage["text_units"],
                    "unit": "Texteinheiten",
                    "version": "sqlite-fts5-context-bm25-query-v2",
                    "description": (
                        "Persistente normalisierte Terme; globale IDF, "
                        "Gemeindefilter; exakte Navigationsfloskeln ausgeschlossen (Diagnose)."
                    ),
                },
                {
                    "id": "lexical_expanded",
                    "label": "BM25 mit lexikalischer Expansion",
                    "available": True,
                    "coverage": self.coverage["text_units"],
                    "unit": "Texteinheiten",
                    "version": "explicit-domain-rules-v2",
                    "description": (
                        "Explizite Domänenbegriffe; Basistreffer zuerst, "
                        "begrenzte zusätzliche Kandidaten."
                    ),
                },
                {
                    "id": "graph",
                    "label": "Quellengebundenes Graph Retrieval",
                    "available": True,
                    "coverage": len(graph_sources),
                    "unit": "Quellenversionen",
                    "version": self.projection_id,
                    "description": (
                        "Vorläufiger Teilgraph; Aussagegeltung und Belege, kein BM25-Fallback."
                    ),
                },
                {
                    "id": "semantic",
                    "label": "Dense Retrieval",
                    "available": False,
                    "description": (
                        "Vorhandener Altindex: 1543 Einheiten; "
                        "keine geprüfte Zuordnung zum Vollbestand."
                    ),
                },
                {
                    "id": "hybrid",
                    "label": "Hybrid Retrieval",
                    "available": False,
                    "description": "Kein zum Bayern-Snapshot gebundener Dense-Vollindex verfügbar.",
                },
                {
                    "id": "structured",
                    "label": "Strukturierter Quellenzugriff",
                    "available": False,
                    "description": (
                        "Über Quellenkatalog und Filter implementiert; kein Freifrage-Ranker."
                    ),
                },
            ],
        }

    def execute(self, options):
        result = super().execute(options)
        return self.enrich(result, options.mode, options.reference_year)

    def graph_evidence(self, relation_id):
        if relation_id not in {r["relation_id"] for r in self.workspace.relation_rows}:
            raise ValueError("Unknown relation")
        assert isinstance(self.retriever, BavariaPreviewRetriever)
        result = self.retriever.retrieve(
            RetrievalRequest(
                query="Quellenevidenz",
                mode=RetrievalMode.GRAPH,
                manifest_path=self.manifest_path,
                projection_id=self.projection_id,
                top_k=20,
            ),
            relation_ids=frozenset([relation_id]),
        )
        return self.enrich(self.present(result), RetrievalMode.GRAPH)

    def enrich(self, result, mode, reference_year=None):
        c = source_connection(self.workspace.db, self.workspace.source_databases)
        try:
            for entry in result["evidence"]:
                row = c.execute(
                    "SELECT ordinal,page_ref,heading,kind FROM units WHERE unit_id=?",
                    (entry["source_id"],),
                ).fetchone()
                entry["source_metadata"] = {
                    "archive_url": (
                        None
                        if self.source_presentation == "linked_originals"
                        else "/api/source/" + result["result_id"] + "/" + entry["evidence_id"]
                    ),
                    "source_presentation": self.source_presentation,
                    "locator": dict(row) if row else None,
                    "publisher": None,
                    "publication_date": None,
                    "retrieved_at": self.retrieved_at.get(
                        entry["document"]["document_id"].removeprefix("source-version:")
                    ),
                    "original_sha256": entry["document"]["document_id"].removeprefix(
                        "source-version:"
                    ),
                    "identity_status": "EXPORT_VERIFIED_NO_RUNTIME_ORIGINAL"
                    if self.source_presentation == "linked_originals"
                    else "ORIGINAL_REHASHED"
                    if mode is RetrievalMode.GRAPH
                    else "ARCHIVE_LEDGER_REFERENCE",
                    "authority": "UNREVIEWED",
                    "human_review": "NOT_INDEPENDENTLY_REVIEWED",
                    "claim_scope": "NOT_INHERITED_FROM_SOURCE_FRAME",
                }
        finally:
            c.close()
        result["partial"] = reference_year is not None
        return result


def create_bavaria_service(
    manifest: str | Path, *, original_roots=None, source_presentation="archived"
):
    path = Path(manifest).resolve(strict=True)
    binding = json.loads(path.read_text())
    return BavariaApplicationService(
        Workspace(
            binding["artifacts"]["ledger"]["path"],
            relations_db=binding["artifacts"].get("relations", {}).get("path"),
            manifest=path,
            source_databases=binding.get("source_ledger_order", []),
            disposition_db=binding.get("disposition_ledger"),
        ),
        original_roots=original_roots,
        source_presentation=source_presentation,
    )


def archive_roots() -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    return [
        root / "generated/bavaria",
        root / "research/experiments/e2_v11_acquisition_20261024/export/raw",
    ]


def _original_bytes(record, sha: str, allowed: Sequence[Path]) -> bytes:
    if not record or not 0 <= record["raw_bytes"] <= 16 * 1024**2:
        raise RetrievalError("original_not_available")
    path = Path(record["raw_path"]).resolve(strict=True)
    if not any(base.resolve() in path.parents for base in allowed) or not path.is_file():
        raise RetrievalError("original_not_available")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        data = stream.read(16 * 1024**2 + 1)
    if len(data) != record["raw_bytes"] or hashlib.sha256(data).hexdigest() != sha:
        raise RetrievalError("original_integrity_failure")
    return data


def read_original(record, sha: str, allowed: Sequence[Path], *, raw: bool = False):
    """Read only a bounded, hash-verified version under explicitly allowed roots."""
    data = _original_bytes(record, sha, allowed)
    if raw:
        return data, "application/octet-stream", sha + ".bin"
    if data.startswith(b"%PDF"):
        return data, "application/pdf", sha + ".pdf"
    from crawler.core.source_presentation import sanitized_html

    return sanitized_html(data).encode(), "text/html", sha + ".html"


def response_budget(result: RetrievalResult) -> RetrievalResult:
    if sum(len(item.evidence_text) for item in result.items) > 1024**2:
        raise RetrievalError("preview_response_budget_exceeded")
    return result


EXPANSION = {
    "photovoltaik": ("photovoltaikanlage", "solar", "agri-photovoltaikanlage"),
    "wärmeplanung": ("wärmeplan", "wärmeversorgung", "nahwärmenetz", "fernwärme"),
    "mobilität": ("radverkehr", "radweg", "verkehr", "öpnv"),
    "klimaanpassung": ("hochwasser", "starkregen", "hitze", "entsiegelung"),
    "klimaschutzziele": ("klimaneutral", "treibhausgas", "co2", "klimaschutz"),
    "förderprogramme": ("förderung", "zuschuss", "förderprogramm"),
}


def expand_query(question: str) -> tuple[str, ...]:
    folded = norm(question)
    return tuple(
        dict.fromkeys(
            word for key, values in EXPANSION.items() if norm(key) in folded for word in values
        )
    )
