"""
extract_graph_spacy.py
======================
Offline graph extraction for the demo DB — no Ollama / GPU / internet needed.

Uses spaCy German NER (de_core_news_sm) + keyword-based entity classification
+ sentence-level co-occurrence for relationship detection.

Writes results to the same `graph_triplets` table used by extract_graph_ollama.py,
so the rest of the pipeline (export_graph_to_neo4j, detect_communities, query_agent)
works unchanged.

Install:
    pip install spacy
    python -m spacy download de_core_news_sm

Usage:
    python demo/extract_graph_spacy.py
    python demo/extract_graph_spacy.py --limit 500 --min-score 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import spacy

from crawler.core.config import PROMPT_VERSION
from crawler.core.database import connect_existing_read_write
from crawler.core.provenance import (
    GraphProvenance,
    ensure_provenance_schema,
    finish_extraction_run,
    start_extraction_run,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path("demo/demo.sqlite")
MODEL_NAME = "spacy-de_core_news_sm"

# ---------------------------------------------------------------------------
# Entity-type classification via keyword matching
# ---------------------------------------------------------------------------

_FOERDERPROGRAMM = frozenset(
    {
        "kfw",
        "bafa",
        "nki",
        "efre",
        "förder",
        "förderprogramm",
        "fördermittel",
        "fördergeld",
        "förderrichtlinie",
        "bundesförderung",
        "energieförderung",
        "klimaförderung",
        "solarförderung",
        "wärmeförderung",
        "zuschuss",
        "subvention",
        "förderzusage",
        "fördersatz",
    }
)

_INFRASTRUKTUR = frozenset(
    {
        "photovoltaik",
        "pv-anlage",
        "solaranlage",
        "windanlage",
        "windkraft",
        "windrad",
        "wärmepumpe",
        "wärmenetz",
        "fernwärme",
        "nahwärme",
        "blockheizkraftwerk",
        "bhkw",
        "solarthermie",
        "ladepunkt",
        "ladestation",
        "wallbox",
        "ladenetz",
        "batteriespeicher",
        "speicher",
        "stromnetz",
        "gasnetz",
        "netz",
        "anlage",
        "kataster",
        "solarpotenzialkataster",
    }
)

_MASSNAHME = frozenset(
    {
        "sanierung",
        "gebäudesanierung",
        "ausbau",
        "installation",
        "umbau",
        "modernisierung",
        "projekt",
        "initiative",
        "maßnahme",
        "aktionspaket",
        "umsetzung",
        "vorhaben",
        "pilotprojekt",
        "modellprojekt",
    }
)

_KONZEPT_ZIEL = frozenset(
    {
        "konzept",
        "klimaschutzkonzept",
        "klimastrategie",
        "strategie",
        "klimaziel",
        "ziel",
        "klimaneutralität",
        "co2-neutralität",
        "co2",
        "treibhausgas",
        "emissionsreduktion",
        "dekarbonisierung",
        "masterplan",
        "aktionsplan",
        "integriertes klimaschutz",
    }
)

_DOKUMENT = frozenset(
    {
        "bericht",
        "klimaschutzbericht",
        "jahresbericht",
        "satzung",
        "beschluss",
        "atlas",
        "kataster",
        "leitfaden",
        "studie",
        "gutachten",
        "handlungsempfehlung",
        "handlungsfeld",
        "ausschreibung",
    }
)

# Map domain keywords → (entity_type, base_category)
_TYPE_TABLE: list[tuple[frozenset, str, str]] = [
    (_FOERDERPROGRAMM, "Förderprogramm", "Finanzen"),
    (_INFRASTRUKTUR, "Infrastruktur", "Strom"),
    (_KONZEPT_ZIEL, "Konzept/Ziel", "Governance"),
    (_MASSNAHME, "Maßnahme", "Sonstiges"),
    (_DOKUMENT, "Dokument", "Governance"),
]


def _classify(name: str) -> tuple[str, str]:
    """Return (entity_type, category) for a surface form."""
    nl = name.lower()
    for kw_set, etype, ecat in _TYPE_TABLE:
        if any(kw in nl for kw in kw_set):
            # Refine Infrastruktur category
            if etype == "Infrastruktur":
                if any(k in nl for k in {"solar", "pv", "photo", "strom", "wind"}):
                    ecat = "Strom"
                elif any(k in nl for k in {"wärm", "heiz", "fernwärm", "bhkw"}):
                    ecat = "Wärme"
                elif any(k in nl for k in {"lade", "mobil", "e-auto"}):
                    ecat = "Mobilität"
            return etype, ecat
    # Default: Akteur
    return "Akteur", "Governance"


# ---------------------------------------------------------------------------
# Relationship detection
# ---------------------------------------------------------------------------

_RELATION_PATTERNS: list[tuple[frozenset[str], str]] = [
    (
        frozenset(
            {
                "fördert",
                "förderte",
                "unterstützt",
                "finanziert",
                "finanzierte",
                "bezuschusst",
                "förder",
                "förderprogramm",
            }
        ),
        "FÖRDERT",
    ),
    (
        frozenset(
            {
                "baut",
                "baute",
                "errichtet",
                "errichtete",
                "installiert",
                "installierte",
                "realisiert",
                "realisierte",
                "bau ",
                "errichtung",
            }
        ),
        "BAUT",
    ),
    (
        frozenset(
            {
                "beschließt",
                "beschloss",
                "verabschiedet",
                "verabschiedete",
                "genehmigt",
                "genehmigte",
                "beschlossen",
                "beschluss",
            }
        ),
        "BESCHLIESST",
    ),
    (
        frozenset(
            {
                "setzt um",
                "setzte um",
                "implementiert",
                "implementierte",
                "führt durch",
                "führte durch",
                "umgesetzt",
                "umsetzung",
            }
        ),
        "IMPLEMENTIERT",
    ),
    (frozenset({"plant", "plante", "beabsichtigt", "strebt an", "geplant", "planung"}), "PLANT"),
    (frozenset({"gehört zu", "ist teil", "unterliegt", "bestandteil"}), "GEHÖRT_ZU"),
    (
        frozenset(
            {
                "kooperiert",
                "kooperierte",
                "zusammenarbeit",
                "partner",
                "arbeitet zusammen",
                "arbeitete zusammen",
            }
        ),
        "KOOPERIERT_MIT",
    ),
    (
        frozenset(
            {
                "finanziert durch",
                "finanziert von",
                "gefördert durch",
                "gefördert von",
                "mit mitteln",
            }
        ),
        "FINANZIERT_DURCH",
    ),
    (frozenset({"bezieht sich", "referenziert", "basiert auf", "im rahmen"}), "BEZIEHT_SICH_AUF"),
]

_NOISE = frozenset(
    {
        "hier",
        "mehr",
        "auch",
        "bitte",
        "diese",
        "dieser",
        "dieses",
        "alle",
        "viele",
        "weitere",
        "verschiedene",
        "seit",
        "noch",
        "zum",
        "zur",
        "der",
        "die",
        "das",
        "ein",
        "eine",
        "einem",
    }
)


def _find_relation(between: str) -> Optional[str]:
    bl = between.lower()
    for verbs, rel in _RELATION_PATTERNS:
        if any(v in bl for v in verbs):
            return rel
    return None


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def extract(nlp, text: str, heading: Optional[str]) -> dict:
    """
    Returns a dict with 'entities' and 'relationships' lists
    matching the KnowledgeGraph Pydantic schema shape used by extract_graph_ollama.py.
    """
    combined = f"{heading}: {text}" if heading else text
    doc = nlp(combined[:2000])  # keep within spaCy's practical limit

    seen: dict[str, dict] = {}

    # 1. Named entities from NER
    for ent in doc.ents:
        name = ent.text.strip()
        if len(name) < 4 or name.lower() in _NOISE:
            continue
        # Remove trailing punctuation
        name = re.sub(r"[.,;:!?]+$", "", name).strip()
        if name and name not in seen:
            etype, ecat = _classify(name)
            seen[name] = {
                "name": name,
                "type": etype,
                "category": ecat,
                "status": "Existierend",
                "metrics": {},
            }

    # 2. Domain noun chunks not caught by NER
    for chunk in doc.noun_chunks:
        phrase = chunk.text.strip()
        phrase = re.sub(r"^(die|der|das|ein|eine|dem|den|des)\s+", "", phrase, flags=re.I)
        phrase = re.sub(r"[.,;:!?]+$", "", phrase).strip()
        if len(phrase) < 6 or phrase in seen:
            continue
        pl = phrase.lower()
        if any(any(kw in pl for kw in kw_set) for kw_set, *_ in _TYPE_TABLE):
            etype, ecat = _classify(phrase)
            seen[phrase] = {
                "name": phrase,
                "type": etype,
                "category": ecat,
                "status": "Existierend",
                "metrics": {},
            }

    entities = list(seen.values())

    # 3. Relationship detection: entity pairs in the same sentence
    ent_names = list(seen.keys())
    relationships = []

    for sent in doc.sents:
        st = sent.text
        sl = st.lower()
        in_sent = [e for e in ent_names if e.lower() in sl]
        for i, src in enumerate(in_sent):
            for tgt in in_sent[i + 1 :]:
                i_src = sl.find(src.lower())
                i_tgt = sl.find(tgt.lower(), i_src)
                if i_src == -1 or i_tgt == -1:
                    continue
                between = st[i_src + len(src) : i_tgt]
                rel = _find_relation(between)
                if rel:
                    relationships.append(
                        {
                            "source_entity": src,
                            "relation_type": rel,
                            "target_entity": tgt,
                            "evidence": st[:200],
                        }
                    )

    return {"entities": entities, "relationships": relationships}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_SETUP_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=60000;

CREATE TABLE IF NOT EXISTS graph_triplets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id    TEXT NOT NULL,
    document_id        TEXT NOT NULL,
    segment_rowid      INTEGER NOT NULL,
    segment_hash       TEXT NOT NULL,
    model_name         TEXT NOT NULL,
    graph_json         TEXT NOT NULL,
    entity_count       INTEGER NOT NULL DEFAULT 0,
    relationship_count INTEGER NOT NULL DEFAULT 0,
    extracted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_graph_triplets_muni
    ON graph_triplets(municipality_id);
"""


def _get_chunks(cur: sqlite3.Cursor, limit: int, min_score: int) -> list[dict]:
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            c.rowid         AS legacy_rowid,
            c.chunk_id,
            c.segment_id,
            d.municipality_id,
            c.document_id,
            d.url_canonical,
            c.text,
            c.heading_context,
            COALESCE(c.impact_score, 0) AS impact_score,
            ROW_NUMBER() OVER (
              PARTITION BY c.document_id
              ORDER BY COALESCE(c.impact_score, 0) DESC
            ) AS rn
          FROM chunks c
          JOIN documents_raw d ON d.document_id = c.document_id
          JOIN segments s ON s.segment_id = c.segment_id AND s.document_id = c.document_id
          WHERE length(c.text) >= 80
            AND COALESCE(c.impact_score, 0) >= ?
            AND NOT EXISTS (
              SELECT 1 FROM graph_triplets g
              WHERE g.source_type='chunk' AND g.source_id=c.chunk_id
                AND g.provenance_status='verified'
            )
        )
        SELECT legacy_rowid, chunk_id, segment_id, municipality_id, document_id, url_canonical,
               text, heading_context, impact_score
        FROM ranked WHERE rn <= 5
        ORDER BY impact_score DESC LIMIT ?
        """,
        (min_score, limit),
    )
    cols = [
        "legacy_rowid",
        "chunk_id",
        "segment_id",
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


def main(limit: int = 500, min_score: int = 0, db: Optional[Path] = None) -> None:
    db_path = db or DB_PATH
    print("Lade spaCy-Modell de_core_news_sm …")
    nlp = spacy.load("de_core_news_sm")

    conn = connect_existing_read_write(db_path)
    cur = conn.cursor()
    cur.executescript(_SETUP_SQL)
    ensure_provenance_schema(conn)

    rows = _get_chunks(cur, limit=limit, min_score=min_score)
    if not rows:
        print("✅ Keine neuen Chunks für Extraktion (alle bereits verarbeitet).")
        conn.close()
        return

    print(f"🚀 Extrahiere Graph für {len(rows)} Chunks (spaCy NER + Keyword-Matching) …\n")

    run_id = str(uuid.uuid4())
    start_extraction_run(
        conn,
        run_id=run_id,
        extraction_model=MODEL_NAME,
        prompt_version=PROMPT_VERSION,
        source_type="chunk",
    )
    total_e = total_r = 0
    for idx, row in enumerate(rows):  # type: ignore[assignment]
        rowid = int(row["legacy_rowid"])
        text = row["text"]
        muni = row["municipality_id"]
        doc_id = row["document_id"]
        heading = row["heading_context"]

        kg = extract(nlp, text, heading)
        e, r = len(kg["entities"]), len(kg["relationships"])
        total_e += e
        total_r += r

        seg_hash = hashlib.sha256(text.encode()).hexdigest()
        graph_json = json.dumps(kg, ensure_ascii=False)
        provenance = GraphProvenance(
            "chunk",
            row["chunk_id"],
            row["segment_id"],
            row["chunk_id"],
            doc_id,
            muni,
            row["url_canonical"],
            text,
            MODEL_NAME,
            PROMPT_VERSION,
            run_id,
        )
        provenance.validate()

        cur.execute(
            """
            INSERT OR IGNORE INTO graph_triplets (
                municipality_id, document_id, segment_rowid, segment_hash,
                model_name, graph_json, entity_count, relationship_count,
                source_type, source_id, segment_id, chunk_id, source_url, source_text,
                extraction_model, prompt_version, extraction_run_id, provenance_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                muni,
                doc_id,
                rowid,
                seg_hash,
                MODEL_NAME,
                graph_json,
                e,
                r,
                "chunk",
                row["chunk_id"],
                row["segment_id"],
                row["chunk_id"],
                row["url_canonical"],
                text,
                MODEL_NAME,
                PROMPT_VERSION,
                run_id,
                "verified",
            ),
        )

        if (idx + 1) % 100 == 0 or (idx + 1) == len(rows):
            print(f"  [{idx + 1:4d}/{len(rows)}]  entities={total_e}  rels={total_r}")

    finish_extraction_run(conn, run_id, status="completed")
    conn.close()

    print(f"\n{'═' * 55}")
    print(" Graph-Extraktion abgeschlossen (spaCy)")
    print(f" Chunks verarbeitet : {len(rows)}")
    print(f" Entitäten          : {total_e}")
    print(f" Beziehungen        : {total_r}")
    print(f" Modell             : {MODEL_NAME}")
    print(f" DB                 : {db_path}")
    print(f"{'═' * 55}")
    print()
    print(" Graph inspizieren:")
    print(f"   python demo/inspect_graph.py --db {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Offline graph extraction using spaCy German NER")
    ap.add_argument("--limit", type=int, default=500, help="Max chunks to process (default: 500)")
    ap.add_argument(
        "--min-score", type=int, default=0, help="Minimum impact_score for chunks (default: 0)"
    )
    ap.add_argument(
        "--db", default=str(DB_PATH), help="Path to SQLite DB (default: demo/demo.sqlite)"
    )
    args = ap.parse_args()
    main(limit=args.limit, min_score=args.min_score, db=Path(args.db))
