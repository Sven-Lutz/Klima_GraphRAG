"""
query_demo.py  —  Schlanke SQLite-only GraphRAG-Suche für den PoC.

Kein Neo4j, kein Ollama nötig. Beweist:
  ✓ Tiktoken-Chunking funktioniert
  ✓ Embeddings liegen als BLOB in SQLite
  ✓ Cosine-Similarity Retrieval gibt relevante Chunks zurück
  ✓ Kontext-Assembly (heading_context + Nachbar-Chunks)

Verwendung:
    python demo/query_demo.py "Welche Kommunen haben Solaranlagen gefördert?"
    python demo/query_demo.py --top 5 "PV-Anlage Förderung Bayern"
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

# Repo-Root in sys.path, damit crawler.core.config importierbar ist
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler.core.config import EMBED_MODEL  # gleiches Modell wie Produktion
from crawler.core.database import connect_existing_read_only

DEMO_DB = Path("demo/demo.sqlite")
TOP_K = 5


# ── Serialisation ─────────────────────────────────────────────────────────────


def blob_to_vec(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


# ── Retrieval ─────────────────────────────────────────────────────────────────


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    model = SentenceTransformer(EMBED_MODEL)
    q_vec = model.encode(query, normalize_embeddings=True).astype(np.float32)

    con = connect_existing_read_only(DEMO_DB, row_factory=sqlite3.Row)

    rows = con.execute("""
        SELECT
            c.chunk_id, c.text, c.heading_context,
            c.impact_score, c.chunk_index,
            d.url_canonical, d.municipality_id,
            c.embedding
        FROM chunks c
        JOIN documents_raw d ON d.document_id = c.document_id
        WHERE c.embedding IS NOT NULL
    """).fetchall()

    if not rows:
        con.close()
        print("  [!] Keine eingebetteten Chunks gefunden. Bitte zuerst run_poc.sh ausführen.")
        sys.exit(1)

    scored = []
    for r in rows:
        vec = blob_to_vec(r["embedding"])
        sim = cosine(q_vec, vec)
        scored.append((sim, dict(r)))

    scored.sort(key=lambda x: -x[0])
    top = scored[:top_k]

    # Lade benachbarte Chunks für Kontext-Fenster
    results = []
    for sim, row in top:
        neighbours = con.execute(
            """
            SELECT text FROM chunks
            WHERE document_id = (
                SELECT document_id FROM chunks WHERE chunk_id = ?
            )
            AND chunk_index BETWEEN ? AND ?
            ORDER BY chunk_index
        """,
            (row["chunk_id"], row["chunk_index"] - 1, row["chunk_index"] + 1),
        ).fetchall()

        context = "\n".join(n["text"] for n in neighbours if n["text"] != row["text"])
        results.append(
            {
                "score": round(sim, 4),
                "muni": row["municipality_id"],
                "url": row["url_canonical"],
                "heading": row["heading_context"] or "",
                "text": row["text"],
                "context": context,
            }
        )

    con.close()
    return results


# ── Ausgabe ───────────────────────────────────────────────────────────────────


def display(query: str, results: list[dict]) -> None:
    print(f"\n{'═' * 65}")
    print(f" Anfrage: {query}")
    print(f" Modell:  {EMBED_MODEL}  |  Top-{len(results)} Chunks")
    print(f"{'═' * 65}\n")

    for i, r in enumerate(results, 1):
        print(f"[{i}] Score={r['score']:.4f}  Muni={r['muni']}")
        if r["heading"]:
            print(f"    📂 {r['heading']}")
        print(f"    🔗 {r['url']}")
        print(f"    {r['text'][:300].replace(chr(10), ' ')}…")
        if r["context"]:
            print(f"    [Kontext-Nachbar] {r['context'][:150].replace(chr(10), ' ')}…")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default="Klimaschutz Maßnahmen Solarenergie Förderung")
    ap.add_argument("--top", type=int, default=TOP_K)
    ap.add_argument("--db", default=str(DEMO_DB))
    args = ap.parse_args()

    DEMO_DB = Path(args.db)
    results = search(args.query, top_k=args.top)
    display(args.query, results)
