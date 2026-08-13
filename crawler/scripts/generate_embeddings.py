"""
generate_embeddings.py
======================
Stage 2a of the GraphRAG pipeline.

Computes vector embeddings for every chunk using a local SentenceTransformers
model (privacy-compliant, no API calls) and stores them in SQLite as raw
float32 bytes. Neo4j writes belong exclusively to the canonical, manifested
snapshot projection owner; vector projection is not implemented in that model.

Recommended model: paraphrase-multilingual-MiniLM-L12-v2
  - 384 dimensions, multilingual (covers German)
  - Fits comfortably in 4 GB VRAM / 8 GB RAM
  - max_seq_length = 256 tokens (chunks ≤600 tok are truncated; still good)

For better quality (if GPU available): intfloat/multilingual-e5-large (1024-dim)

Usage:
    python -m crawler.scripts.generate_embeddings
    python -m crawler.scripts.generate_embeddings --model intfloat/multilingual-e5-large --batch 64
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
from datetime import datetime, timezone

import numpy as np

from crawler.core.config import EMBED_BATCH_SIZE, EMBED_MODEL
from crawler.core.database import connect_existing_read_write, resolve_runtime_database

DEFAULT_MODEL = EMBED_MODEL
DEFAULT_BATCH = EMBED_BATCH_SIZE


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def vec_to_blob(vec: np.ndarray) -> bytes:
    """Pack float32 numpy array to raw bytes for SQLite BLOB storage."""
    return struct.pack(f"{len(vec)}f", *vec.astype(np.float32).tolist())


def blob_to_vec(blob: bytes) -> np.ndarray:
    """Unpack SQLite BLOB back to float32 numpy array."""
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------


def load_model(model_name: str):
    """Load SentenceTransformer model with automatic device selection."""
    import torch
    from sentence_transformers import SentenceTransformer

    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    print(f"   🔧 Modell: {model_name}  |  Device: {device}")
    model = SentenceTransformer(model_name, device=device)
    model.max_seq_length = 256
    return model


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def get_unembed_chunks(conn: sqlite3.Connection, limit: int = 10_000) -> list[tuple[str, str]]:
    """Return (chunk_id, text) for chunks without an embedding yet."""
    rows = conn.execute(
        """
        SELECT chunk_id, text FROM chunks
        WHERE embedding IS NULL
        ORDER BY impact_score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def store_embeddings_sqlite(
    conn: sqlite3.Connection,
    chunk_ids: list[str],
    embeddings: np.ndarray,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = [(vec_to_blob(embeddings[i]), now, chunk_ids[i]) for i in range(len(chunk_ids))]
    conn.executemany(
        "UPDATE chunks SET embedding=?, embedded_at=? WHERE chunk_id=?",
        rows,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(
    model_name: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH,
    limit: int = 50_000,
    db_path: str | None = None,
) -> None:
    database = resolve_runtime_database(db_path)
    model = load_model(model_name)
    conn = connect_existing_read_write(database.path)
    print(f"database={database.path} database_id={database.database_id}")

    total_embedded = 0

    while True:
        rows = get_unembed_chunks(conn, limit=min(limit, batch_size * 10))
        if not rows:
            break

        chunk_ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]

        print(f"⚙️  Embedding {len(texts)} Chunks…")
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)

        with conn:
            store_embeddings_sqlite(conn, chunk_ids, embeddings)

        total_embedded += len(chunk_ids)
        print(f"   ✅ {total_embedded} Chunks embedded (SQLite)")

        if total_embedded >= limit:
            break

    dim = model.get_sentence_embedding_dimension()
    print(f"\n🏁 Embedding abgeschlossen. Dimensionen: {dim}  |  Gesamt: {total_embedded}")

    conn.close()


def cli(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="SentenceTransformer Embedding (Stage 2a)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="SentenceTransformers Modell-Name")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--limit", type=int, default=50_000, help="Max. Chunks pro Lauf")
    ap.add_argument("--db", default=None, help="SQLite database; overrides KLIMA_DB_PATH")
    args = ap.parse_args(argv)
    main(
        model_name=args.model,
        batch_size=args.batch,
        limit=args.limit,
        db_path=args.db,
    )


if __name__ == "__main__":
    cli()
