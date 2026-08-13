from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from neo4j import GraphDatabase
except ImportError:  # Modul bleibt für Tests/Tools ohne Neo4j importierbar
    GraphDatabase = None

from crawler.core.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from crawler.core.database import connect_existing_read_only, resolve_runtime_database

# ---------- normalization / keys ----------
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\wäöüÄÖÜß\- ]+", re.UNICODE)


def norm_name(s: str) -> str:
    s = (s or "").strip()
    s = _WS.sub(" ", s)
    return s


def entity_key(name: str, etype: str, category: str) -> str:
    # stable merge key: normalized name + coarse type/category
    n = norm_name(name).lower()
    n = _PUNCT.sub("", n)
    n = _WS.sub(" ", n).strip()
    return f"{etype}::{category}::{n}"


# ---------- Neo4j schema ----------
SCHEMA_CYPHER = [
    "CREATE CONSTRAINT municipality_id IF NOT EXISTS FOR (m:Municipality) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT segment_rowid IF NOT EXISTS FOR (s:Segment) REQUIRE s.rowid IS UNIQUE",
    "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX entity_category IF NOT EXISTS FOR (e:Entity) ON (e.category)",
    "CREATE INDEX entity_status IF NOT EXISTS FOR (e:Entity) ON (e.status)",
    "CREATE INDEX segment_heading IF NOT EXISTS FOR (s:Segment) ON (s.heading_context)",
]

ALLOWED_REL_TYPES = {
    "FÖRDERT",
    "BAUT",
    "BESCHLIESST",
    "IMPLEMENTIERT",
    "PLANT",
    "GEHÖRT_ZU",
    "KOOPERIERT_MIT",
    "FINANZIERT_DURCH",
    "BEZIEHT_SICH_AUF",
}


def safe_rel_type(t: str) -> str:
    t = (t or "").strip().upper()
    # Neo4j relationship types cannot contain spaces; keep your German tokens
    t = t.replace(" ", "_")
    return t if t in ALLOWED_REL_TYPES else "BEZIEHT_SICH_AUF"


# ---------- read from SQLite ----------
@dataclass(frozen=True)
class TripletRow:
    municipality_id: str
    document_id: str
    segment_rowid: int
    graph_json: str


def iter_graph_triplets(
    conn: sqlite3.Connection, limit: Optional[int] = None
) -> Iterable[TripletRow]:
    sql = """
    SELECT municipality_id, document_id, segment_rowid, graph_json
    FROM graph_triplets
    ORDER BY extracted_at ASC
    """
    if limit is not None:
        sql += " LIMIT ?"
        cur = conn.execute(sql, (int(limit),))
    else:
        cur = conn.execute(sql)
    for r in cur.fetchall():
        yield TripletRow(
            municipality_id=str(r[0]),
            document_id=str(r[1]),
            segment_rowid=int(r[2]),
            graph_json=str(r[3]),
        )


def get_segment_meta(
    conn: sqlite3.Connection, segment_rowid: int
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Returns (impact_score, text_snippet, heading_context) for a segment."""
    cur = conn.execute(
        "SELECT COALESCE(impact_score,0), text, heading_context "
        "FROM segments WHERE rowid=? LIMIT 1",
        (int(segment_rowid),),
    )
    row = cur.fetchone()
    if not row:
        return None, None, None
    score = int(row[0]) if row[0] is not None else None
    text = str(row[1]) if row[1] is not None else None
    heading_ctx = str(row[2]) if row[2] is not None else None
    return score, text, heading_ctx


# ---------- Neo4j upsert queries (UNWIND-Batches statt Einzel-Roundtrips) ----------
UPSERT_BASE_BATCH = """
UNWIND $rows AS r
MERGE (m:Municipality {id: r.muni_id})
MERGE (d:Document {id: r.doc_id})
MERGE (s:Segment {rowid: r.seg_rowid})
SET s.impact_score = r.impact_score,
    s.text = r.seg_text,
    s.heading_context = r.heading_context
MERGE (m)-[:HAS_DOCUMENT]->(d)
MERGE (d)-[:HAS_SEGMENT]->(s)
"""

# Entity-Upsert + MENTIONS-Kante in einem Statement pro Batch
UPSERT_ENTITY_BATCH = """
UNWIND $rows AS r
MERGE (e:Entity {key: r.key})
ON CREATE SET
  e.name = r.name,
  e.type = r.type,
  e.category = r.category,
  e.status = r.status,
  e.metrics = r.metrics
ON MATCH SET
  e.name = coalesce(e.name, r.name),
  e.type = coalesce(e.type, r.type),
  e.category = coalesce(e.category, r.category),
  e.status = coalesce(e.status, r.status),
  e.metrics = CASE
    WHEN e.metrics IS NULL THEN r.metrics
    ELSE apoc.map.merge(e.metrics, r.metrics)
  END
WITH e, r
MATCH (s:Segment {rowid: r.seg_rowid})
MERGE (s)-[m:MENTIONS]->(e)
SET m.weight = coalesce(m.weight, 0) + 1
"""

# NOTE: requires APOC for apoc.map.merge. If you don't have APOC, remove metrics merge logic.


def rel_edge_batch_query(rel_type: str) -> str:
    # dynamic relationship type — ein UNWIND-Batch pro Typ
    return f"""
UNWIND $rows AS r
MATCH (src:Entity {{key: r.src_key}})
MATCH (dst:Entity {{key: r.dst_key}})
MATCH (s:Segment {{rowid: r.seg_rowid}})
MERGE (src)-[rel:{rel_type}]->(dst)
ON CREATE SET rel.evidence = r.evidence, rel.segment_rowid = r.seg_rowid
ON MATCH SET rel.evidence = coalesce(rel.evidence, r.evidence)
MERGE (s)-[:EVIDENCE_FOR]->(rel)
"""


def _load_canonical_map(conn: sqlite3.Connection) -> Dict[str, str]:
    """Load entity_canonical table (from resolve_entities.py) → {original_name → canonical_name}.

    Schlüssel sind lowercase — identisch zur Anwendung in extract_graph_ollama.py,
    sonst greift die Entity Resolution je nach Schreibweise nur teilweise.
    """
    try:
        cur = conn.execute("SELECT original_name, canonical_name FROM entity_canonical")
        return {str(r[0]).strip().lower(): str(r[1]) for r in cur.fetchall()}
    except Exception:
        return {}


def _resolve(name: str, canonical_map: Dict[str, str]) -> str:
    """Wendet die Entity Resolution case-insensitiv an."""
    return canonical_map.get(name.strip().lower(), name)


def build_entity_key_index(
    conn: sqlite3.Connection,
    canonical_map: Dict[str, str],
    limit: Optional[int] = None,
) -> Dict[str, str]:
    """Pass 1: globaler Index {lowercase-Name → Entity-Key} über ALLE Triplets.

    Relationships dürfen nur auf tatsächlich extrahierte Entitäten zeigen —
    auch wenn die Entität in einem anderen Chunk extrahiert wurde. Ohne diesen
    globalen Index entstünden Phantom-Knoten (falsch als Dokument/Sonstiges
    typisiert) aus LLM-Tippfehlern in Endpunkt-Namen.
    """
    index: Dict[str, str] = {}
    for row in iter_graph_triplets(conn, limit=limit):
        try:
            kg = json.loads(row.graph_json)
        except Exception:
            continue
        for e in kg.get("entities", []) or []:
            raw_name = norm_name(e.get("name", ""))
            if not raw_name:
                continue
            name = _resolve(raw_name, canonical_map)
            etype = e.get("type", "Dokument")
            cat = e.get("category", "Sonstiges")
            index.setdefault(name.lower(), entity_key(name, etype, cat))
    return index


def main(limit: Optional[int] = None, db_path: str | None = None):
    database = resolve_runtime_database(db_path)
    conn = connect_existing_read_only(database.path)
    print(f"database={database.path} database_id={database.database_id}")

    # Load entity resolution map (if available)
    canonical_map = _load_canonical_map(conn)
    if canonical_map:
        print(f"[neo4j] Loaded {len(canonical_map)} entity canonical mappings")

    # Pass 1: globaler Entity-Key-Index (für Chunk-übergreifende Relationships)
    key_by_name = build_entity_key_index(conn, canonical_map, limit=limit)
    print(f"[neo4j] Entity-Index: {len(key_by_name)} eindeutige Entitäten")

    # Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        # Schema
        for stmt in SCHEMA_CYPHER:
            session.run(stmt)

        # Optional: ensure APOC exists if you keep the metrics merge
        # (otherwise remove UPSERT_ENTITY usage of apoc)
        # session.run("RETURN apoc.version()")

        n_rows = 0
        n_entities = 0
        n_rels = 0
        n_dangling = 0

        batch_size = 200  # Triplets pro Flush
        base_batch: List[dict] = []
        entity_batch: List[dict] = []
        rel_batches: Dict[str, List[dict]] = {}

        def flush() -> None:
            nonlocal base_batch, entity_batch, rel_batches
            if base_batch:
                session.run(UPSERT_BASE_BATCH, rows=base_batch)
            if entity_batch:
                session.run(UPSERT_ENTITY_BATCH, rows=entity_batch)
            for rt, rows_ in rel_batches.items():
                if rows_:
                    session.run(rel_edge_batch_query(rt), rows=rows_)
            base_batch, entity_batch, rel_batches = [], [], {}

        for row in iter_graph_triplets(conn, limit=limit):
            n_rows += 1

            impact_score, seg_text, heading_ctx = get_segment_meta(conn, row.segment_rowid)

            base_batch.append(
                {
                    "muni_id": row.municipality_id,
                    "doc_id": row.document_id,
                    "seg_rowid": int(row.segment_rowid),
                    "impact_score": int(impact_score) if impact_score is not None else 0,
                    "seg_text": seg_text[:2000] if seg_text else None,  # cap to keep graph lean
                    "heading_context": heading_ctx,
                }
            )

            try:
                kg = json.loads(row.graph_json)
            except Exception:
                continue

            entities = kg.get("entities", []) or []
            rels = kg.get("relationships", []) or []

            # entities + mention edges (apply entity resolution if available)
            for e in entities:
                raw_name = norm_name(e.get("name", ""))
                if not raw_name:
                    continue
                name = _resolve(raw_name, canonical_map)
                etype = e.get("type", "Dokument")
                cat = e.get("category", "Sonstiges")

                entity_batch.append(
                    {
                        "key": key_by_name.get(name.lower()) or entity_key(name, etype, cat),
                        "name": name,
                        "type": etype,
                        "category": cat,
                        "status": e.get("status", "Unbekannt"),
                        "metrics": e.get("metrics", {}) or {},
                        "seg_rowid": int(row.segment_rowid),
                    }
                )
                n_entities += 1

            # relationships — nur zwischen tatsächlich extrahierten Entitäten.
            # Nicht auflösbare Endpunkte (LLM-Tippfehler, Halluzinationen)
            # werden verworfen und gezählt statt Phantom-Knoten zu erzeugen.
            for r in rels:
                src_name = _resolve(norm_name(r.get("source_entity", "")), canonical_map)
                dst_name = _resolve(norm_name(r.get("target_entity", "")), canonical_map)
                rt = safe_rel_type(r.get("relation_type", "BEZIEHT_SICH_AUF"))
                evidence = norm_name(r.get("evidence", ""))[:500]

                src_key = key_by_name.get(src_name.lower())
                dst_key = key_by_name.get(dst_name.lower())
                if not src_key or not dst_key:
                    n_dangling += 1
                    continue

                rel_batches.setdefault(rt, []).append(
                    {
                        "src_key": src_key,
                        "dst_key": dst_key,
                        "seg_rowid": int(row.segment_rowid),
                        "evidence": evidence,
                    }
                )
                n_rels += 1

            if n_rows % batch_size == 0:
                flush()
                print(f"[neo4j] processed {n_rows} triplets...")

        flush()

        print(f"✅ Done. triplets={n_rows}, entities_upserts~={n_entities}, rel_upserts~={n_rels}")
        if n_dangling:
            print(
                f"   ⚠  {n_dangling} Relationships verworfen (Endpunkt keine extrahierte Entität)"
            )

        # Export document link graph (LINKS_TO edges)
        n_links = 0
        try:
            cur = conn.execute(
                """
                SELECT dl.source_document_id, dl.target_document_id, dl.anchor_text
                FROM document_links dl
                WHERE dl.target_document_id IS NOT NULL
                """
            )
            for src_doc, tgt_doc, anchor in cur.fetchall():
                session.run(
                    """
                    MATCH (src:Document {id: $src_id})
                    MATCH (tgt:Document {id: $tgt_id})
                    MERGE (src)-[r:LINKS_TO]->(tgt)
                    SET r.anchor_text = $anchor
                    """,
                    src_id=str(src_doc),
                    tgt_id=str(tgt_doc),
                    anchor=(anchor or "")[:300],
                )
                n_links += 1
            print(f"✅ Document links exported: {n_links} LINKS_TO edges")
        except Exception as e:
            print(f"⚠️  Document links export skipped: {e}")

    driver.close()
    conn.close()


def cli(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export GraphRAG SQLite graph to Neo4j")
    parser.add_argument("--db", default=None, help="SQLite database; overrides KLIMA_DB_PATH")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)
    main(limit=args.limit, db_path=args.db)


if __name__ == "__main__":
    cli()
