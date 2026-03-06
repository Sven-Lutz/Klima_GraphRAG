import sqlite3
import time
import hashlib
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()
MODEL_NAME = "gpt-4o-mini"
DB_PATH = Path("crawler/data/db/crawl.sqlite")


class Entity(BaseModel):
    name: str
    type: Literal["Akteur", "Infrastruktur", "Förderprogramm", "Konzept/Ziel", "Dokument"]
    category: Literal["Mobilität", "Wärme", "Strom", "Finanzen", "Governance", "Sonstiges"]
    status: Literal["Geplant", "In Umsetzung", "Abgeschlossen", "Existierend", "Unbekannt"]
    metrics: dict[str, str] = Field(default_factory=dict)


class Relationship(BaseModel):
    source_entity: str
    relation_type: Literal["FÖRDERT", "BAUT", "BESCHLIESST", "GEHÖRT_ZU", "KOOPERIERT_MIT", "BEZIEHT_SICH_AUF"]
    target_entity: str
    evidence: str


class KnowledgeGraph(BaseModel):
    entities: List[Entity] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)


SYSTEM_MSG = (
    "Extrahiere strikt strukturierte Knowledge-Graph-Daten aus kommunalen Texten. "
    "Nur Fakten, die im Text belegt sind. Wenn keine Fakten: entities=[] relationships=[]. "
    "evidence: kurzer Originalsatz/Teilsatz."
)


def setup_db(cur: sqlite3.Cursor) -> None:
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA busy_timeout=60000;")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_triplets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            municipality_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            segment_rowid INTEGER NOT NULL,
            segment_hash TEXT NOT NULL,
            model_name TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            entity_count INTEGER NOT NULL,
            relationship_count INTEGER NOT NULL,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_triplets_seg ON graph_triplets(segment_rowid)")


def get_segments(cur: sqlite3.Cursor, limit: int = 10, min_len: int = 160, min_score: int = 20, per_doc: int = 2):
    query = """
    WITH ranked AS (
      SELECT
        s.rowid AS rowid,
        d.municipality_id,
        d.document_id,
        s.text,
        COALESCE(s.impact_score, 0) AS impact_score,
        ROW_NUMBER() OVER (
          PARTITION BY s.document_id
          ORDER BY COALESCE(s.impact_score, 0) DESC
        ) AS rn
      FROM segments s
      JOIN documents_raw d ON d.document_id = s.document_id
      WHERE length(s.text) >= ?
        AND COALESCE(s.is_negative, 0) = 0
        AND COALESCE(s.impact_score, 0) >= ?
        AND s.rowid NOT IN (SELECT segment_rowid FROM graph_triplets)
    )
    SELECT rowid, municipality_id, document_id, text, impact_score
    FROM ranked
    WHERE rn <= ?
    ORDER BY impact_score DESC
    LIMIT ?;
    """
    cur.execute(query, (min_len, min_score, per_doc, limit))
    return cur.fetchall()


def seg_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def extract(text: str) -> KnowledgeGraph:
    resp = client.beta.chat.completions.parse(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": f"TEXT:\n{text}"},
        ],
        response_format=KnowledgeGraph,
        temperature=0.0,
    )
    return resp.choices[0].message.parsed


def main():
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0, isolation_level=None)
    cur = conn.cursor()
    setup_db(cur)

    segs = get_segments(cur, limit=12, min_len=160, min_score=20, per_doc=2)
    if not segs:
        print("✅ Keine neuen Segmente (scored) gefunden.")
        conn.close()
        return

    print(f"🚀 Extrahiere Graph für {len(segs)} Segmente\n")

    for rowid, muni, doc, text, score in segs:
        print(f"⚙️ muni={muni} doc={doc} rowid={rowid} score={score}")
        try:
            kg = extract(text)
            graph_json = kg.model_dump_json(ensure_ascii=False)
            e, r = len(kg.entities), len(kg.relationships)
            print(f"   🟢 entities={e} relationships={r}")

            cur.execute(
                """
                INSERT OR IGNORE INTO graph_triplets (
                    municipality_id, document_id, segment_rowid, segment_hash,
                    model_name, graph_json, entity_count, relationship_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(muni), str(doc), int(rowid), seg_hash(text), MODEL_NAME, graph_json, int(e), int(r)),
            )

        except Exception as ex:
            print(f"   🔴 Fehler: {ex}")

        time.sleep(0.3)

    conn.close()
    print("\n🏁 Done.")


if __name__ == "__main__":
    main()