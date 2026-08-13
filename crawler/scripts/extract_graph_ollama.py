"""
extract_graph_ollama.py
=======================
Stage 2b of the GraphRAG pipeline — privacy-compliant, local-LLM variant.

Replaces the OpenAI-based extract_graph.py with a fully local pipeline:

  Ollama  →  local LLM (e.g. llama3.1:8b, mistral, qwen2.5)
  Pydantic → strict JSON schema enforcement to prevent import errors
  Gleaning → self-reflection pass that asks the model if any entities
             were missed ("Did you overlook anything?") — significantly
             increases extraction recall on dense municipal documents.

Graph output is written to the same `graph_triplets` table so the
existing Neo4j exporter (export_graph_to_neo4j.py) works unchanged.

MODELS tested (German-language performance, descending):
  - qwen2.5:14b          best quality, needs 10+ GB VRAM
  - llama3.1:8b          good quality, 6 GB VRAM
  - mistral:7b           decent, 5 GB VRAM
  - gemma2:9b            strong for structured output

Usage:
    python -m crawler.scripts.extract_graph_ollama
    python -m crawler.scripts.extract_graph_ollama --model qwen2.5:14b --gleaning 2
    python -m crawler.scripts.extract_graph_ollama --chunk-mode  # use chunks table
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from crawler.core.config import (
    LLM_SEED,
    LLM_TEMPERATURE,
    OLLAMA_URL,
    PROMPT_VERSION,
)
from crawler.core.config import (
    OLLAMA_MODEL as _DEFAULT_MODEL,
)
from crawler.core.database import (
    bind_live_database_state,
    connect_existing_read_write,
    resolve_runtime_database,
)
from crawler.core.extraction import (
    ExtractionCancelledError,
    ExtractionConfiguration,
    GraphArtifact,
    SelectedSource,
    finalize_extraction_run,
    initialize_extraction_run,
    persist_graph_artifact,
    record_source_outcome,
    resume_extraction_run,
    safe_error_summary,
    selected_source_hash,
)
from crawler.core.provenance import (
    GraphProvenance,
    ProvenanceError,
    ensure_provenance_v4,
)

MODEL_NAME = _DEFAULT_MODEL


class MalformedProviderResponseError(RuntimeError):
    """Raised when Ollama returns no valid graph JSON."""


# ---------------------------------------------------------------------------
# Pydantic schema (same as extract_graph.py for compatibility)
# ---------------------------------------------------------------------------


class Entity(BaseModel):
    name: str
    type: Literal[
        "Akteur",
        "Infrastruktur",
        "Förderprogramm",
        "Maßnahme",
        "Konzept/Ziel",
        "Dokument",
    ]
    category: Literal["Mobilität", "Wärme", "Strom", "Finanzen", "Governance", "Sonstiges"]
    status: Literal["Geplant", "In Umsetzung", "Abgeschlossen", "Existierend", "Unbekannt"]
    metrics: dict[str, str] = Field(default_factory=dict)


class Relationship(BaseModel):
    source_entity: str
    relation_type: Literal[
        "FÖRDERT",
        "BAUT",
        "BESCHLIESST",
        "IMPLEMENTIERT",
        "PLANT",
        "GEHÖRT_ZU",
        "KOOPERIERT_MIT",
        "FINANZIERT_DURCH",
        "BEZIEHT_SICH_AUF",
    ]
    target_entity: str
    evidence: str


class KnowledgeGraph(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_EXTRACTION = """\
Du bist ein Experte für kommunale Klimaschutzpolitik in Bayern.
Extrahiere alle Entitäten und Beziehungen aus dem gegebenen Text.

WICHTIG: Antworte NUR mit einem JSON-Objekt im folgenden Format:
{
  "entities": [
    {"name": "...", "type": "Akteur|Infrastruktur|Förderprogramm|Maßnahme|Konzept/Ziel|Dokument",
     "category": "Mobilität|Wärme|Strom|Finanzen|Governance|Sonstiges",
     "status": "Geplant|In Umsetzung|Abgeschlossen|Existierend|Unbekannt",
     "metrics": {"year": "...", "amount": "...", "capacity": "..."}}
  ],
  "relationships": [
    {"source_entity": "...",
     "relation_type": "FÖRDERT|BAUT|BESCHLIESST|IMPLEMENTIERT|PLANT|GEHÖRT_ZU\
|KOOPERIERT_MIT|FINANZIERT_DURCH|BEZIEHT_SICH_AUF",
     "target_entity": "...",
     "evidence": "exakter Originaltext (max. 200 Zeichen)"}
  ]
}
Nur belegte Fakten. Kein Halluzinieren. Kein erklärender Text außerhalb des JSON.
"""

SYSTEM_GLEANING = """\
Du hast soeben Entitäten und Beziehungen aus einem Text extrahiert.
Überprüfe deine Extraktion kritisch:

Wurden Akteure (Behörden, Firmen, Vereine), Maßnahmen (Projekte, Investitionen),
Infrastrukturen (Anlagen, Netze) oder Förderprogramme (KfW, BAFA, NKI, EFRE) übersehen?

Falls ja, antworte mit einem JSON-Objekt mit NUR den NEUEN (bisher fehlenden) Entitäten
und Beziehungen im gleichen Schema wie zuvor.
Falls alle Entitäten vollständig sind, antworte mit: {"entities": [], "relationships": []}
"""


def _user_extraction_prompt(text: str, heading_context: Optional[str], url: Optional[str]) -> str:
    parts = []
    if heading_context:
        parts.append(f"ABSCHNITT: {heading_context}")
    if url:
        parts.append(f"QUELLE: {url}")
    parts.append(f"TEXT:\n{text}")
    return "\n\n".join(parts)


def _user_gleaning_prompt(text: str, first_extraction: KnowledgeGraph) -> str:
    entity_names = [e.name for e in first_extraction.entities]
    return (
        f"ORIGINALTEXT:\n{text[:800]}\n\n"
        f"BEREITS EXTRAHIERTE ENTITÄTEN: {json.dumps(entity_names, ensure_ascii=False)}\n\n"
        "Hast du relevante Entitäten oder Beziehungen übersehen?"
    )


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


def _call_ollama(model: str, system: str, user: str, max_retries: int = 3) -> str:
    """Call Ollama REST API; returns raw model response text.

    Retry mit exponentiellem Backoff — bei langen Läufen führen transiente
    Timeouts sonst zu stillen Daten-Lücken im Graphen.
    """
    import urllib.request

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": LLM_TEMPERATURE, "seed": LLM_SEED, "num_predict": 2048},
            "format": "json",  # Ollama structured output mode
        }
    ).encode()

    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["message"]["content"]
        except Exception as ex:
            last_err = ex
            if attempt < max_retries - 1:
                time.sleep(2**attempt)  # 1s, 2s
    raise last_err


def _parse_kg(raw: str) -> KnowledgeGraph:
    """Parse JSON string into a graph or reject the malformed provider result."""
    # Strip markdown code fences if model wraps output
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return KnowledgeGraph.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as first_error:
        # Fallback: try to extract the JSON object manually
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return KnowledgeGraph.model_validate_json(raw[start:end])
            except (ValidationError, json.JSONDecodeError):
                pass
        raise MalformedProviderResponseError(
            "Ollama response is not valid KnowledgeGraph JSON"
        ) from first_error


def extract_with_gleaning(
    model: str,
    text: str,
    heading_context: Optional[str],
    url: Optional[str],
    gleaning_rounds: int = 1,
) -> KnowledgeGraph:
    """
    Two-pass extraction:
      1. Initial structured extraction
      2. Up to `gleaning_rounds` self-reflection passes that append missed entities
    """
    # --- Round 1: initial extraction ---
    user_prompt = _user_extraction_prompt(text, heading_context, url)
    raw = _call_ollama(model, SYSTEM_EXTRACTION, user_prompt)
    kg = _parse_kg(raw)

    # --- Gleaning rounds: self-reflection ---
    for _ in range(gleaning_rounds):
        if not kg.entities:
            break  # nothing to reflect on
        glean_prompt = _user_gleaning_prompt(text, kg)
        raw2 = _call_ollama(model, SYSTEM_GLEANING, glean_prompt)
        kg2 = _parse_kg(raw2)

        # Merge new findings, deduplicating by name
        existing_names = {e.name.lower() for e in kg.entities}
        for e in kg2.entities:
            if e.name.lower() not in existing_names:
                kg.entities.append(e)
                existing_names.add(e.name.lower())

        # Relationships ebenfalls deduplizieren — Gleaning liefert oft
        # dieselben Beziehungen erneut, was relationship_count verfälscht
        existing_rels = {
            (r.source_entity.lower(), r.relation_type, r.target_entity.lower())
            for r in kg.relationships
        }
        for r in kg2.relationships:
            rel_key = (r.source_entity.lower(), r.relation_type, r.target_entity.lower())
            if rel_key not in existing_rels:
                kg.relationships.append(r)
                existing_rels.add(rel_key)

    return kg


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def setup_db(cur: sqlite3.Cursor) -> None:
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA busy_timeout=60000;

        CREATE TABLE IF NOT EXISTS graph_triplets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            municipality_id   TEXT NOT NULL,
            document_id       TEXT NOT NULL,
            segment_rowid     INTEGER NOT NULL,
            segment_hash      TEXT NOT NULL,
            model_name        TEXT NOT NULL,
            graph_json        TEXT NOT NULL,
            entity_count      INTEGER NOT NULL DEFAULT 0,
            relationship_count INTEGER NOT NULL DEFAULT 0,
            extracted_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_graph_triplets_muni
            ON graph_triplets(municipality_id);
        """
    )


def get_chunks_for_extraction(
    cur: sqlite3.Cursor,
    limit: int = 200,
    min_score: int = 15,
    min_len: int = 160,
    per_doc: int = 5,
    use_chunks_table: bool = False,
) -> list[dict]:
    """Select one deterministic experiment input set without history filtering."""
    if use_chunks_table:
        query = """
        WITH ranked AS (
          SELECT
            c.rowid         AS legacy_rowid,
            'chunk'         AS source_type,
            c.chunk_id      AS source_id,
            c.segment_id,
            c.chunk_id,
            c.chunk_index   AS order_index,
            d.municipality_id,
            c.document_id,
            d.url_canonical,
            c.text,
            c.heading_context,
            COALESCE(c.impact_score, 0) AS impact_score,
            ROW_NUMBER() OVER (
              PARTITION BY c.document_id
              ORDER BY COALESCE(c.impact_score, 0) DESC, c.chunk_index, c.chunk_id
            ) AS rn
          FROM chunks c
          LEFT JOIN documents_raw d ON d.document_id = c.document_id
          LEFT JOIN segments s ON s.segment_id = c.segment_id
          WHERE length(c.text) >= ?
            AND COALESCE(c.impact_score, 0) >= ?
        )
        SELECT legacy_rowid, source_type, source_id, segment_id, chunk_id,
               order_index, municipality_id, document_id, url_canonical,
               text, heading_context, impact_score
        FROM ranked WHERE rn <= ?
        ORDER BY impact_score DESC, document_id, order_index, source_id LIMIT ?
        """
    else:
        query = """
        WITH ranked AS (
          SELECT
            s.rowid         AS legacy_rowid,
            'segment'       AS source_type,
            s.segment_id    AS source_id,
            s.segment_id,
            NULL            AS chunk_id,
            s.order_index,
            d.municipality_id,
            d.document_id,
            d.url_canonical,
            s.text,
            s.heading_context,
            COALESCE(s.impact_score, 0) AS impact_score,
            ROW_NUMBER() OVER (
              PARTITION BY s.document_id
              ORDER BY COALESCE(s.impact_score, 0) DESC, s.order_index, s.segment_id
            ) AS rn
          FROM segments s
          LEFT JOIN documents_raw d ON d.document_id = s.document_id
          WHERE length(s.text) >= ?
            AND COALESCE(s.is_negative, 0) = 0
            AND COALESCE(s.impact_score, 0) >= ?
        )
        SELECT legacy_rowid, source_type, source_id, segment_id, chunk_id,
               order_index, municipality_id, document_id, url_canonical,
               text, heading_context, impact_score
        FROM ranked WHERE rn <= ?
        ORDER BY impact_score DESC, document_id, order_index, source_id LIMIT ?
        """
    cur.execute(query, (min_len, min_score, per_doc, limit))
    cols = [
        "legacy_rowid",
        "source_type",
        "source_id",
        "segment_id",
        "chunk_id",
        "order_index",
        "municipality_id",
        "document_id",
        "url_canonical",
        "text",
        "heading_context",
        "impact_score",
    ]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _load_source_row(
    conn: sqlite3.Connection, source_type: str, source_id: str
) -> dict[str, object]:
    if source_type == "segment":
        row = conn.execute(
            """
            SELECT s.rowid, s.segment_id, s.document_id, s.order_index, s.text,
                   s.heading_context, COALESCE(s.impact_score, 0),
                   d.municipality_id, d.url_canonical
            FROM segments s LEFT JOIN documents_raw d ON d.document_id=s.document_id
            WHERE s.segment_id=?
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            return {"source_type": "segment", "source_id": source_id, "text": ""}
        return {
            "legacy_rowid": row[0],
            "source_type": "segment",
            "source_id": row[1],
            "segment_id": row[1],
            "chunk_id": None,
            "document_id": row[2],
            "order_index": row[3],
            "text": row[4],
            "heading_context": row[5],
            "impact_score": row[6],
            "municipality_id": row[7],
            "url_canonical": row[8],
        }
    row = conn.execute(
        """
        SELECT c.rowid, c.chunk_id, c.segment_id, c.document_id, c.chunk_index,
               c.text, c.heading_context, COALESCE(c.impact_score, 0),
               d.municipality_id, d.url_canonical
        FROM chunks c LEFT JOIN documents_raw d ON d.document_id=c.document_id
        WHERE c.chunk_id=?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        return {"source_type": "chunk", "source_id": source_id, "text": ""}
    return {
        "legacy_rowid": row[0],
        "source_type": "chunk",
        "source_id": row[1],
        "segment_id": row[2],
        "chunk_id": row[1],
        "document_id": row[3],
        "order_index": row[4],
        "text": row[5],
        "heading_context": row[6],
        "impact_score": row[7],
        "municipality_id": row[8],
        "url_canonical": row[9],
    }


def validate_source_hierarchy(
    conn: sqlite3.Connection,
    row: dict,
    *,
    model: str,
    run_id: str,
) -> GraphProvenance:
    """Resolve and validate the complete source hierarchy before any provider call."""
    source_type = str(row.get("source_type", ""))
    source_id = str(row.get("source_id", ""))
    if source_type == "segment":
        source = conn.execute(
            "SELECT document_id, text FROM segments WHERE segment_id=?", (source_id,)
        ).fetchone()
        if source is None:
            raise ProvenanceError(f"Segment {source_id!r} does not exist")
        document_id, source_text = source
        segment_id, chunk_id = source_id, None
        chunk_municipality_id = None
    elif source_type == "chunk":
        source = conn.execute(
            "SELECT document_id, segment_id, text, municipality_id FROM chunks WHERE chunk_id=?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise ProvenanceError(f"Chunk {source_id!r} does not exist")
        document_id, segment_id, source_text, chunk_municipality_id = source
        parent = conn.execute(
            "SELECT document_id FROM segments WHERE segment_id=?", (segment_id,)
        ).fetchone()
        if parent is None:
            raise ProvenanceError(f"Chunk {source_id!r} has no resolvable parent segment")
        if parent[0] != document_id:
            raise ProvenanceError(
                f"Chunk {source_id!r} document contradicts parent segment {segment_id!r}"
            )
        chunk_id = source_id
    else:
        raise ProvenanceError(f"Unsupported extraction source type: {source_type!r}")

    document = conn.execute(
        "SELECT municipality_id, url_canonical FROM documents_raw WHERE document_id=?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise ProvenanceError(f"Source {source_id!r} references missing document {document_id!r}")
    municipality_id, source_url = document
    if not isinstance(municipality_id, str) or not municipality_id.strip():
        raise ProvenanceError(f"Document {document_id!r} has no valid municipality")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ProvenanceError(f"Document {document_id!r} has no valid source URL")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ProvenanceError(f"Document {document_id!r} has no valid HTTP(S) source URL")
    if not isinstance(source_text, str) or not source_text.strip():
        raise ProvenanceError(f"Source {source_id!r} has no valid source text")
    if chunk_municipality_id not in {None, "", municipality_id}:
        raise ProvenanceError(
            f"Chunk {source_id!r} municipality contradicts document {document_id!r}"
        )

    municipality_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='municipality_documents'"
    ).fetchone()
    if municipality_table:
        associations = {
            str(association[0])
            for association in conn.execute(
                "SELECT municipality_id FROM municipality_documents WHERE document_id=?",
                (document_id,),
            )
        }
        if municipality_id not in associations:
            raise ProvenanceError(f"Document {document_id!r} has no valid municipality association")

    provenance = GraphProvenance(
        source_type=source_type,  # type: ignore[arg-type]
        source_id=source_id,
        segment_id=str(segment_id) if segment_id is not None else None,
        chunk_id=chunk_id,
        document_id=str(document_id),
        municipality_id=municipality_id,
        source_url=source_url,
        source_text=source_text,
        extraction_model=model,
        prompt_version=PROMPT_VERSION,
        extraction_run_id=run_id,
    )
    provenance.validate()
    return provenance


def main(
    model: str = MODEL_NAME,
    gleaning_rounds: int = 1,
    limit: int = 200,
    min_score: int = 15,
    use_chunks_table: bool = False,
    sleep_between: float = 0.0,
    parallel: int = 1,
    db_path: str | None = None,
    resume_run_id: str | None = None,
    stale_after_seconds: int = 3600,
    cancellation_requested: Callable[[], bool] | None = None,
) -> str:
    selected_database = resolve_runtime_database(db_path)
    conn = connect_existing_read_write(selected_database)
    run_id: str | None = None
    try:
        cur = conn.cursor()
        setup_db(cur)
        ensure_provenance_v4(conn)
        database = bind_live_database_state(selected_database)
        print(
            f"database={database.path} database_id={database.database_id} "
            f"snapshot_id={database.snapshot_id or 'unmanifested'}"
        )

        # Build canonical name map from resolve_entities output (if available)
        canonical_map: dict[str, str] = {}
        try:
            for raw_name, canon_name in cur.execute(
                "SELECT original_name, canonical_name FROM entity_canonical"
            ).fetchall():
                canonical_map[raw_name.lower()] = canon_name
        except sqlite3.OperationalError:
            pass

        source_type = "chunk" if use_chunks_table else "segment"
        parameters = {
            "gleaning_rounds": gleaning_rounds,
            "temperature": LLM_TEMPERATURE,
            "limit": limit,
            "min_score": min_score,
            "parallel": parallel,
        }
        configuration = ExtractionConfiguration(
            provider="ollama",
            model=model,
            prompt_version=PROMPT_VERSION,
            parameters=parameters,
            seed=LLM_SEED,
            source_type=source_type,
        )

        if resume_run_id:
            pending_ids = resume_extraction_run(
                conn,
                run_id=resume_run_id,
                configuration=configuration,
                database=database,
                stale_after=timedelta(seconds=stale_after_seconds),
            )
            run_id = resume_run_id
            rows = [_load_source_row(conn, source_type, source_id) for source_id in pending_ids]
            mode_label = "resume"
        else:
            rows = get_chunks_for_extraction(
                cur, limit=limit, min_score=min_score, use_chunks_table=use_chunks_table
            )
            selected_sources = [
                SelectedSource(source_type, str(row["source_id"]), str(row.get("text") or ""))
                for row in rows
            ]
            run_id = initialize_extraction_run(
                conn,
                configuration=configuration,
                database=database,
                selected_sources=selected_sources,
            )
            mode_label = "new experiment"

        mode = "chunks" if use_chunks_table else "segments"
        print(
            f"🚀 Extrahiere Graph via Ollama ({model}) für {len(rows)} {mode} "
            f"| gleaning={gleaning_rounds} | parallel={parallel} | {mode_label} "
            f"| run_id={run_id}\n"
        )
        if not rows:
            summary = finalize_extraction_run(conn, run_id)
            print(f"✅ Keine Eingaben ausgewählt; run_status={summary.status}")
            return run_id

        def _extract_one(row: dict, provenance: GraphProvenance) -> KnowledgeGraph:
            kg = extract_with_gleaning(
                model=model,
                text=provenance.source_text,
                heading_context=row.get("heading_context"),
                url=provenance.source_url,
                gleaning_rounds=gleaning_rounds,
            )
            if canonical_map:
                for ent in kg.entities:
                    ent.name = canonical_map.get(ent.name.lower(), ent.name)
                for rel in kg.relationships:
                    rel.source_entity = canonical_map.get(
                        rel.source_entity.lower(), rel.source_entity
                    )
                    rel.target_entity = canonical_map.get(
                        rel.target_entity.lower(), rel.target_entity
                    )
            return kg

        def _store(row: dict, provenance: GraphProvenance, kg: KnowledgeGraph) -> None:
            artifact = GraphArtifact(
                legacy_rowid=int(row["legacy_rowid"]),
                segment_hash=hashlib.sha256(provenance.source_text.encode()).hexdigest(),
                graph_json=kg.model_dump_json(ensure_ascii=False),
                entity_count=len(kg.entities),
                relationship_count=len(kg.relationships),
            )
            persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
            print(
                f"   🟢 source={provenance.source_type}:{provenance.source_id}  "
                f"muni={provenance.municipality_id}  entities={artifact.entity_count}  "
                f"relationships={artifact.relationship_count}"
            )

        def _validate_before_provider(row: dict) -> GraphProvenance | None:
            try:
                provenance = validate_source_hierarchy(conn, row, model=model, run_id=run_id)
                if (
                    selected_source_hash(conn, run_id, provenance.source_id)
                    != hashlib.sha256(provenance.source_text.encode("utf-8")).hexdigest()
                ):
                    raise ProvenanceError("Selected source content changed before extraction")
                return provenance
            except ProvenanceError as exc:
                record_source_outcome(
                    conn,
                    run_id=run_id,
                    source_type=source_type,
                    source_id=str(row["source_id"]),
                    outcome="rejected",
                    error_category="invalid_provenance",
                    error_summary=safe_error_summary(exc),
                )
                print(f"   🟠 Abgelehnt source={row['source_id']}: {safe_error_summary(exc)}")
                return None

        def _handle_result(
            row: dict, provenance: GraphProvenance, result: KnowledgeGraph | BaseException
        ) -> None:
            if isinstance(result, BaseException):
                category = (
                    "malformed_provider_result"
                    if isinstance(result, MalformedProviderResponseError)
                    else "provider_unavailable"
                    if isinstance(result, (ConnectionError, TimeoutError, OSError))
                    else "provider_error"
                )
                record_source_outcome(
                    conn,
                    run_id=run_id,
                    source_type=source_type,
                    source_id=provenance.source_id,
                    outcome="failed",
                    error_category=category,
                    error_summary=safe_error_summary(result),
                    retryable=category == "provider_unavailable",
                )
                print(f"   🔴 Fehler source={provenance.source_id}: {safe_error_summary(result)}")
                return
            try:
                _store(row, provenance, result)
            except Exception as exc:
                record_source_outcome(
                    conn,
                    run_id=run_id,
                    source_type=source_type,
                    source_id=provenance.source_id,
                    outcome="failed",
                    error_category="persistence_failure",
                    error_summary=safe_error_summary(exc),
                    retryable=True,
                )
                print(
                    f"   🔴 Persistenzfehler source={provenance.source_id}: "
                    f"{safe_error_summary(exc)}"
                )

        try:
            if parallel <= 1:
                for row in rows:
                    if cancellation_requested and cancellation_requested():
                        raise ExtractionCancelledError("Cooperative cancellation requested")
                    provenance = _validate_before_provider(row)
                    if provenance is None:
                        continue
                    try:
                        result: KnowledgeGraph | BaseException = _extract_one(row, provenance)
                    except KeyboardInterrupt:
                        raise
                    except BaseException as exc:
                        result = exc
                    if cancellation_requested and cancellation_requested():
                        raise ExtractionCancelledError("Cooperative cancellation requested")
                    _handle_result(row, provenance, result)
                    if sleep_between > 0:
                        time.sleep(sleep_between)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                prepared = []
                for row in rows:
                    if cancellation_requested and cancellation_requested():
                        raise ExtractionCancelledError("Cooperative cancellation requested")
                    provenance = _validate_before_provider(row)
                    if provenance is not None:
                        prepared.append((row, provenance))
                with ThreadPoolExecutor(max_workers=parallel) as pool:
                    futures = {
                        pool.submit(_extract_one, row, provenance): (row, provenance)
                        for row, provenance in prepared
                    }
                    for future in as_completed(futures):
                        if cancellation_requested and cancellation_requested():
                            for pending in futures:
                                pending.cancel()
                            raise ExtractionCancelledError("Cooperative cancellation requested")
                        row, provenance = futures[future]
                        try:
                            result = future.result()
                        except KeyboardInterrupt:
                            raise
                        except BaseException as exc:
                            result = exc
                        if cancellation_requested and cancellation_requested():
                            for pending in futures:
                                pending.cancel()
                            raise ExtractionCancelledError("Cooperative cancellation requested")
                        _handle_result(row, provenance, result)
            summary = finalize_extraction_run(conn, run_id)
        except ExtractionCancelledError as exc:
            summary = finalize_extraction_run(
                conn,
                run_id,
                forced_status="cancelled",
                status_details=safe_error_summary(exc),
            )
        except KeyboardInterrupt as exc:
            finalize_extraction_run(
                conn,
                run_id,
                forced_status="interrupted",
                status_details="KeyboardInterrupt",
            )
            raise exc
        except BaseException as exc:
            finalize_extraction_run(
                conn,
                run_id,
                forced_status="failed",
                status_details=safe_error_summary(exc),
            )
            raise

        print(f"\n🏁 Done. run_id={run_id} status={summary.status}")
        return run_id
    finally:
        conn.close()


def cli(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ollama Graph Extraktion mit Gleaning (Stage 2b)")
    ap.add_argument("--model", default=MODEL_NAME)
    ap.add_argument("--gleaning", type=int, default=1, help="Anzahl Gleaning-Runden")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min-score", type=int, default=15)
    ap.add_argument(
        "--chunk-mode", action="store_true", help="Aus chunks-Tabelle statt segments lesen"
    )
    ap.add_argument("--sleep", type=float, default=0.0, help="Pause zwischen Anfragen (Sekunden)")
    ap.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Gleichzeitige LLM-Anfragen (HPC: 4-8, abhängig von VRAM)",
    )
    ap.add_argument(
        "--resume-run",
        default=None,
        metavar="RUN_ID",
        help="Resume this interrupted or stale run without creating a new experiment",
    )
    ap.add_argument(
        "--stale-after",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Age after which a running run is safe to resume (default: 3600)",
    )
    ap.add_argument("--db", default=None, help="SQLite database; overrides KLIMA_DB_PATH")
    args = ap.parse_args(argv)
    main(
        model=args.model,
        gleaning_rounds=args.gleaning,
        limit=args.limit,
        min_score=args.min_score,
        use_chunks_table=args.chunk_mode,
        sleep_between=args.sleep,
        parallel=args.parallel,
        db_path=args.db,
        resume_run_id=args.resume_run,
        stale_after_seconds=args.stale_after,
    )


if __name__ == "__main__":
    cli()
