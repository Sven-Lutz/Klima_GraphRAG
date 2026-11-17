"""PyTorch-free, resumable BGE-M3 embeddings for Klima_GraphRAG PDF-V2.

Place beside klima_hybrid_resume_v4.py at the repository root. Existing original
SQLite databases, prior MiniLM vectors, and Python source are not modified.

Commands:
    ollama pull bge-m3
    python klima_ollama_hybrid.py --check
    python klima_ollama_hybrid.py --build --batch 4 --max-batches 1
    python klima_ollama_hybrid.py --build --batch 4
    python klima_ollama_hybrid.py --query "Foerdert Regensburg Photovoltaik?"
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

MODEL = "bge-m3"
DIMENSION = 1024
PROVIDER = "ollama"
PREPROCESSING_ID = "canonical-chunks-v1:ollama-bge-m3-raw-document-v1"
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
FROZEN = Path("crawler/data/db/RECOVERY_RAG_PDF_V2_OLLAMA_BGE_M3_SNAPSHOT.sqlite")
LEXICAL = Path("crawler/data/db/RECOVERY_RAG_PDF_V2_OLLAMA_BGE_M3_LEXICAL.sqlite")
MANIFEST = FROZEN.with_suffix(FROZEN.suffix + ".manifest.json")


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def api(path: str, payload: dict | None = None, *, timeout: int = 300) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(HOST + path, data=data,
                  headers={"Content-Type": "application/json"},
                  method="POST" if payload is not None else "GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Ollama HTTP {exc.code} at {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Ollama not reachable at {HOST}: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Invalid Ollama response at {path}")
    return result


def identity_revision() -> str:
    """Pin the exact installed Ollama model digest, not an unstable 'latest' tag."""
    models = api("/api/tags").get("models", [])
    selected = [m for m in models
                if m.get("name") in (MODEL, MODEL + ":latest")
                or m.get("model") in (MODEL, MODEL + ":latest")]
    if len(selected) != 1 or not selected[0].get("digest"):
        raise RuntimeError("Install the exact model first: ollama pull bge-m3")
    digest = str(selected[0]["digest"])
    if len(digest) < 32:
        raise RuntimeError("Ollama model digest is invalid")
    return "ollama-digest:" + digest


def embed(texts: list[str]) -> np.ndarray:
    if not texts or any(not text.strip() for text in texts):
        raise RuntimeError("Cannot embed empty texts")
    # Keep the original embedding payload and identity unchanged.
    # Only retry transient socket failures; each successful batch is already
    # saved atomically by build(), so progress remains safely resumable.
    for attempt in range(1, 4):
        try:
            result = api("/api/embed", {"model": MODEL, "input": texts,
                                         "truncate": False, "keep_alive": "10m"},
                         timeout=300)
            break
        except (TimeoutError, ConnectionError) as exc:
            lengths = [len(text) for text in texts]
            print(f"OLLAMA TEMPORARY FAILURE: attempt {attempt}/3; "
                  f"text lengths={lengths}; {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            if attempt == 3:
                raise RuntimeError(
                    "The same embedding batch failed three times. "
                    f"Text lengths (characters): {lengths}. "
                    "Previously saved embeddings remain intact. "
                    "Please share this error; do not delete databases."
                ) from exc
            import time
            time.sleep(10 * attempt)
    rows = result.get("embeddings")
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError("Ollama returned the wrong number of embeddings")
    vectors = np.asarray(rows, dtype=np.float32)
    if vectors.shape != (len(texts), DIMENSION):
        raise RuntimeError(f"Embedding shape is {vectors.shape}; expected ({len(texts)}, {DIMENSION})")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.all(np.isfinite(vectors)) or not np.all(np.isfinite(norms)) or np.any(norms <= 0):
        raise RuntimeError("Ollama returned invalid embedding values")
    return vectors / norms[:, None]


def check() -> str:
    revision = identity_revision()
    vector = embed(["Fördert die Stadt Photovoltaikanlagen auf Gebäuden?"])
    print("OLLAMA MODEL:", MODEL, flush=True)
    print("MODEL DIGEST:", revision, flush=True)
    print("EMBEDDING DIMENSIONS:", vector.shape[1], flush=True)
    print("PyTorch is not required by this script", flush=True)
    return revision


def make_identity(revision: str):
    from crawler.core.retrieval import EmbeddingIdentity
    return EmbeddingIdentity(PROVIDER, MODEL, revision, DIMENSION, PREPROCESSING_ID)


def init_artifact(db: sqlite3.Connection, *, artifact: str, identity,
                  source_id: str, fingerprint: str, rows) -> dict[str, str]:
    from crawler.core.retrieval import ensure_embedding_schema
    ensure_embedding_schema(db)
    db.commit()
    expected = {chunk_id: h for chunk_id, h, _text in rows}
    record = db.execute(
        "SELECT status,planned_count,provider,model,model_revision,vector_dimension,"
        "preprocessing_id,source_database_id,corpus_fingerprint "
        "FROM embedding_artifacts WHERE artifact_id=?", (artifact,)).fetchone()
    if record is None:
        db.execute("""INSERT INTO embedding_artifacts
            (artifact_id,schema_version,provider,model,model_revision,vector_dimension,
             preprocessing_id,source_database_id,corpus_fingerprint,planned_count,
             embedded_count,status,created_at)
            VALUES (?,1,?,?,?,?,?,?,?,?,0,'running',?)""",
            (artifact, identity.provider, identity.model, identity.model_revision,
             identity.vector_dimension, identity.preprocessing_id, source_id,
             fingerprint, len(rows), stamp()))
        db.commit()
    else:
        status, planned, provider, model, revision, dimension, preprocessing, source, fp = record
        if (int(planned), provider, model, revision, int(dimension), preprocessing, source, fp) != (
                len(rows), identity.provider, identity.model, identity.model_revision,
                identity.vector_dimension, identity.preprocessing_id, source_id, fingerprint):
            raise RuntimeError("Existing Ollama artifact metadata does not match; refusing reuse")
    existing = {}
    for chunk_id, source_hash, dim, blob_length in db.execute(
        "SELECT chunk_id,source_content_hash,vector_dimension,length(embedding) "
        "FROM chunk_embeddings WHERE artifact_id=?", (artifact,)):
        if expected.get(chunk_id) != source_hash or int(dim) != DIMENSION or int(blob_length) != DIMENSION * 4:
            raise RuntimeError(f"Existing vector/hash mismatch for chunk {chunk_id}")
        existing[str(chunk_id)] = source_hash
    if record is not None and record[0] == "completed" and len(existing) != len(rows):
        raise RuntimeError("Artifact says completed but its vectors are incomplete")
    db.execute("UPDATE embedding_artifacts SET embedded_count=?,status=? WHERE artifact_id=?",
               (len(existing), "completed" if len(existing) == len(rows) else "running", artifact))
    db.commit()
    return existing


def build(*, batch_size: int, max_batches: int | None) -> None:
    if batch_size < 1 or batch_size > 16:
        raise RuntimeError("Use --batch between 1 and 16 for this local Ollama build")
    revision = check()
    import klima_hybrid_resume_v4 as v4
    from crawler.core.database import resolve_runtime_database
    from crawler.core.retrieval import embedding_artifact_id, vector_to_blob
    if not v4.SOURCE.is_file():
        raise RuntimeError(f"Original PDF-V2 snapshot is missing: {v4.SOURCE}")
    rows, fp = v4.corpus_rows(v4.SOURCE)
    empty = [chunk_id for chunk_id, _h, text in rows if not text.strip()]
    if empty:
        raise RuntimeError(f"Corpus contains {len(empty)} empty chunks (first={empty[0]}); resolve before embedding")
    print(f"SOURCE CHUNKS: {len(rows):,}", flush=True)
    print("Original PDF-V2 and V3 databases will not be modified", flush=True)
    lock = v4.WORK.with_suffix(".ollama-bge-m3.build.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(stamp())
    except FileExistsError:
        raise RuntimeError(f"Build lock exists: {lock}. Check that no other build is running.")
    try:
        current_rows, current_fp = v4.ensure_working_copy(fp)
        if current_fp != fp:
            raise RuntimeError("Working copy does not match PDF-V2 source")
        identity = make_identity(revision)
        source_id = resolve_runtime_database(str(v4.WORK)).database_id
        artifact = embedding_artifact_id(identity, source_database_id=source_id, corpus_identity=fp)
        print("ARTIFACT:", artifact, flush=True)
        with closing(sqlite3.connect(str(v4.WORK))) as db:
            existing = init_artifact(db, artifact=artifact, identity=identity,
                                     source_id=source_id, fingerprint=fp, rows=current_rows)
            print(f"ALREADY EMBEDDED: {len(existing):,} / {len(current_rows):,}", flush=True)
            if len(existing) != len(current_rows):
                # Avoid duplicated Ollama calls for chunks with identical text.
                groups: dict[str, list] = {}
                for chunk_id, h, text in current_rows:
                    if chunk_id in existing:
                        continue
                    entry = groups.setdefault(h, [text, []])
                    entry[1].append(chunk_id)
                items = list(groups.items())
                print(f"REMAINING UNIQUE TEXTS: {len(items):,}", flush=True)
                batches_done = 0
                chunks_done = 0
                for start in range(0, len(items), batch_size):
                    selection = items[start:start + batch_size]
                    vectors = embed([value[0] for _h, value in selection])
                    content = []
                    for (h, (_text, chunk_ids)), vec in zip(selection, vectors, strict=True):
                        blob = vector_to_blob(vec)
                        for chunk_id in chunk_ids:
                            content.append((artifact, chunk_id, h, DIMENSION, blob, stamp()))
                    # Each batch is atomic and therefore safe to resume.
                    try:
                        db.execute("BEGIN IMMEDIATE")
                        db.executemany("INSERT INTO chunk_embeddings "
                                       "(artifact_id,chunk_id,source_content_hash,vector_dimension,embedding,embedded_at) "
                                       "VALUES (?,?,?,?,?,?)", content)
                        db.execute("UPDATE embedding_artifacts SET embedded_count=embedded_count+? "
                                   "WHERE artifact_id=?", (len(content), artifact))
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
                    batches_done += 1
                    chunks_done += len(content)
                    if batches_done % 25 == 0 or start + batch_size >= len(items):
                        print(f"PROGRESS: {len(existing)+chunks_done:,}/{len(current_rows):,}", flush=True)
                    if max_batches is not None and batches_done >= max_batches:
                        print("PILOT STOP: persisted batches; rerun --build without --max-batches to resume.")
                        return
            # Full integrity check: IDs, original text hashes, dimensionality and byte count.
            actual = list(db.execute(
                "SELECT chunk_id,source_content_hash,vector_dimension,length(embedding) "
                "FROM chunk_embeddings WHERE artifact_id=? ORDER BY chunk_id", (artifact,)))
            expected = [(chunk_id, h, DIMENSION, DIMENSION * 4) for chunk_id, h, _ in current_rows]
            if actual != expected:
                raise RuntimeError("Final vector audit failed: corpus membership/hash/dimension mismatch")
            db.execute("UPDATE embedding_artifacts SET status='completed',embedded_count=?,completed_at=? "
                       "WHERE artifact_id=?", (len(current_rows), stamp(), artifact))
            db.commit()
        # Reuse the previously tested manifest export and lexical-index writer,
        # with DISTINCT Ollama output names so no previous output is overwritten.
        v4.FROZEN = FROZEN
        v4.LEXICAL = LEXICAL
        v4.export_and_index(fp, artifact)
        print("READY: use --query for semantic+lexical retrieval smoke test", flush=True)
    finally:
        lock.unlink(missing_ok=True)


def query(question: str, k: int) -> None:
    from crawler.core.retrieval import (
        CanonicalRetriever, RetrievalMode, RetrievalRequest, render_retrieval_markdown)
    if not MANIFEST.is_file() or not FROZEN.is_file() or not LEXICAL.is_file():
        raise RuntimeError("Complete --build first; no validated Ollama hybrid snapshot yet")
    revision = identity_revision()

    class OllamaQueryEmbedder:
        def __init__(self):
            self.identity = make_identity(revision)

        def embed_query(self, text: str) -> np.ndarray:
            return embed([text])[0]

    retriever = CanonicalRetriever(
        embedder=OllamaQueryEmbedder(), lexical_database=str(LEXICAL))
    result = retriever.retrieve(RetrievalRequest(
        query=question, manifest_path=str(MANIFEST),
        mode=RetrievalMode.SEMANTIC_LEXICAL, top_k=k))
    print("EMBEDDING ARTIFACT:", result.embedding_artifact_id)
    print(render_retrieval_markdown(result))
    print("Retrieval only: policy verification and final answers must be evaluated separately.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--query", metavar="QUESTION")
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--max-batches", type=int, default=None,
                        help="Pilot safety stop; persists progress, does NOT create a complete artifact")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    print("SCRIPT: ollama-bge-m3-hybrid-v1", flush=True)
    if args.check:
        check()
    elif args.build:
        if args.max_batches is not None and args.max_batches < 1:
            parser.error("--max-batches must be at least 1")
        build(batch_size=args.batch, max_batches=args.max_batches)
    else:
        query(args.query, args.top_k)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
