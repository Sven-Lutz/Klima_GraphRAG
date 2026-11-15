"""
generate_embeddings.py
======================
Stage 2a of the GraphRAG pipeline.

Computes one complete, versioned embedding artifact for the canonical chunk
corpus. Each vector is bound to its model, revision, dimension, preprocessing
identity, source database, chunk identity, and content hash. Legacy values in
``chunks.embedding`` are deliberately not promoted into this trusted contract.

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
from datetime import datetime, timezone

import numpy as np

from crawler.core.config import EMBED_BATCH_SIZE, EMBED_MODEL
from crawler.core.database import connect_existing_read_write, resolve_runtime_database
from crawler.core.retrieval import (
    EmbeddingIdentity,
    chunk_corpus_rows,
    corpus_fingerprint,
    embedding_artifact_id,
    ensure_embedding_schema,
    vector_to_blob,
)

DEFAULT_MODEL = EMBED_MODEL
DEFAULT_BATCH = EMBED_BATCH_SIZE
DEFAULT_PROVIDER = "sentence-transformers"
DEFAULT_MODEL_REVISION = "unversioned"
DEFAULT_PREPROCESSING_ID = "canonical-chunks-v1:max-seq-length=256"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def vec_to_blob(vec: np.ndarray) -> bytes:
    """Pack float32 numpy array to raw bytes for SQLite BLOB storage."""
    return vector_to_blob(vec)


def blob_to_vec(blob: bytes) -> np.ndarray:
    """Unpack SQLite BLOB back to float32 numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------


def load_model(model_name: str, model_revision: str = DEFAULT_MODEL_REVISION):
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
    revision = {} if model_revision == DEFAULT_MODEL_REVISION else {"revision": model_revision}
    model = SentenceTransformer(model_name, device=device, **revision)
    model.max_seq_length = 256
    return model


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def store_embeddings_sqlite(
    conn: sqlite3.Connection,
    artifact_id: str,
    chunk_ids: list[str],
    content_hashes: list[str],
    embeddings: np.ndarray,
    embedded_at: str,
) -> None:
    dimension = int(embeddings.shape[1])
    rows = [
        (
            artifact_id,
            chunk_ids[i],
            content_hashes[i],
            dimension,
            vec_to_blob(embeddings[i]),
            embedded_at,
        )
        for i in range(len(chunk_ids))
    ]
    conn.executemany(
        """
        INSERT INTO chunk_embeddings(
          artifact_id, chunk_id, source_content_hash, vector_dimension, embedding, embedded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _validate_completed_artifact(
    conn: sqlite3.Connection,
    artifact_id: str,
    corpus: list[tuple[str, str, str]],
    dimension: int,
) -> None:
    rows = conn.execute(
        """
        SELECT chunk_id, source_content_hash, vector_dimension, length(embedding)
        FROM chunk_embeddings WHERE artifact_id=? ORDER BY chunk_id
        """,
        (artifact_id,),
    ).fetchall()
    expected = [(chunk_id, content_hash) for chunk_id, content_hash, _text in corpus]
    actual = [(str(row[0]), str(row[1])) for row in rows]
    if actual != expected or any(
        int(row[2]) != dimension or int(row[3]) != dimension * 4 for row in rows
    ):
        raise RuntimeError(
            f"Completed embedding artifact is inconsistent and will not be reused: {artifact_id}"
        )


def _claim_embedding_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    identity: EmbeddingIdentity,
    source_database_id: str,
    fingerprint: str,
    corpus: list[tuple[str, str, str]],
    created_at: str,
) -> bool:
    """Atomically claim one artifact; return false only for a valid completed artifact."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT status, planned_count, embedded_count FROM embedding_artifacts "
            "WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if existing and existing[0] == "completed":
            if (int(existing[1]), int(existing[2])) != (len(corpus), len(corpus)):
                raise RuntimeError(
                    f"Completed embedding artifact is inconsistent and will not be reused: "
                    f"{artifact_id}"
                )
            _validate_completed_artifact(conn, artifact_id, corpus, identity.vector_dimension)
            conn.execute("COMMIT")
            return False
        if existing and existing[0] == "running":
            raise RuntimeError(
                f"Embedding artifact is already owned by another active or interrupted writer: "
                f"{artifact_id}. Mark it failed explicitly before retrying an interrupted run."
            )
        if existing:
            conn.execute("DELETE FROM chunk_embeddings WHERE artifact_id=?", (artifact_id,))
            conn.execute(
                """
                UPDATE embedding_artifacts
                SET planned_count=?, embedded_count=0, status='running', created_at=?,
                    completed_at=NULL
                WHERE artifact_id=? AND status='failed'
                """,
                (len(corpus), created_at, artifact_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO embedding_artifacts(
                  artifact_id, schema_version, provider, model, model_revision, vector_dimension,
                  preprocessing_id, source_database_id, corpus_fingerprint, planned_count,
                  embedded_count, status, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'running', ?)
                """,
                (
                    artifact_id,
                    identity.provider,
                    identity.model,
                    identity.model_revision,
                    identity.vector_dimension,
                    identity.preprocessing_id,
                    source_database_id,
                    fingerprint,
                    len(corpus),
                    created_at,
                ),
            )
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(
    model_name: str = DEFAULT_MODEL,
    model_revision: str = DEFAULT_MODEL_REVISION,
    preprocessing_id: str = DEFAULT_PREPROCESSING_ID,
    batch_size: int = DEFAULT_BATCH,
    limit: int = 50_000,
    db_path: str | None = None,
) -> None:
    database = resolve_runtime_database(db_path)
    conn = connect_existing_read_write(database.path)
    print(f"database={database.path} database_id={database.database_id}")
    try:
        ensure_embedding_schema(conn)
        corpus = chunk_corpus_rows(conn)
        if not corpus:
            raise RuntimeError("Canonical chunk corpus is empty; run chunk_documents first")
        if len(corpus) > limit:
            raise RuntimeError(
                f"Corpus has {len(corpus)} chunks but --limit is {limit}; partial artifacts "
                "are not allowed"
            )
        model = load_model(model_name, model_revision)
        dimension = int(model.get_sentence_embedding_dimension())
        identity = EmbeddingIdentity(
            provider=DEFAULT_PROVIDER,
            model=model_name,
            model_revision=model_revision,
            vector_dimension=dimension,
            preprocessing_id=preprocessing_id,
        )
        fingerprint = corpus_fingerprint(corpus)
        artifact_id = embedding_artifact_id(
            identity,
            source_database_id=database.database_id,
            corpus_identity=fingerprint,
        )
        now = datetime.now(timezone.utc).isoformat()
        claimed = _claim_embedding_artifact(
            conn,
            artifact_id=artifact_id,
            identity=identity,
            source_database_id=database.database_id,
            fingerprint=fingerprint,
            corpus=corpus,
            created_at=now,
        )
        if not claimed:
            print(f"Embedding artifact already completed: {artifact_id}")
            return
        try:
            texts = [row[2] for row in corpus]
            print(f"⚙️  Embedding {len(texts)} Chunks…")
            embeddings = model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=True,
                normalize_embeddings=True,
                convert_to_numpy=True,
            ).astype(np.float32)
            if embeddings.shape != (len(corpus), dimension):
                raise RuntimeError(
                    f"Model returned shape {embeddings.shape}; expected {(len(corpus), dimension)}"
                )
            if not np.all(np.isfinite(embeddings)) or np.any(
                np.linalg.norm(embeddings, axis=1) == 0
            ):
                raise RuntimeError("Model returned a non-finite or zero embedding vector")
            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute("BEGIN IMMEDIATE")
            store_embeddings_sqlite(
                conn,
                artifact_id,
                [row[0] for row in corpus],
                [row[1] for row in corpus],
                embeddings,
                completed_at,
            )
            conn.execute(
                """
                UPDATE embedding_artifacts
                SET embedded_count=?, status='completed', completed_at=?
                WHERE artifact_id=?
                """,
                (len(corpus), completed_at, artifact_id),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute(
                "UPDATE embedding_artifacts SET status='failed' WHERE artifact_id=?",
                (artifact_id,),
            )
            raise
        print(
            f"\n🏁 Embedding abgeschlossen. Dimensionen: {dimension} | "
            f"Gesamt: {len(corpus)} | artifact={artifact_id}"
        )
    finally:
        conn.close()


def cli(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="SentenceTransformer Embedding (Stage 2a)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="SentenceTransformers Modell-Name")
    ap.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    ap.add_argument("--preprocessing-id", default=DEFAULT_PREPROCESSING_ID)
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--limit", type=int, default=50_000, help="Max. Chunks pro Lauf")
    ap.add_argument("--db", default=None, help="SQLite database; overrides KLIMA_DB_PATH")
    args = ap.parse_args(argv)
    main(
        model_name=args.model,
        model_revision=args.model_revision,
        preprocessing_id=args.preprocessing_id,
        batch_size=args.batch,
        limit=args.limit,
        db_path=args.db,
    )


if __name__ == "__main__":
    cli()
