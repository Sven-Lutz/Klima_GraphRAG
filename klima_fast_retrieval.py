"""Fast read-only lexical retrieval with optional on-demand BGE-M3 reranking.

Use from the Klima_GraphRAG project root. No bulk embeddings are required.
This is a retrieval experiment, not a replacement for verified policy answering.

    python klima_fast_retrieval.py --query "Does Regensburg fund roof photovoltaics?" --lexical-only
    python klima_fast_retrieval.py --query "Does Regensburg fund roof photovoltaics?"
    python klima_fast_retrieval.py --query "Foerdert Regensburg Photovoltaik?" --candidates 12 --top-k 6

Reads the existing PDF-V2 source database and original complete FTS index.
Does not edit or delete any databases or previously generated embeddings.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import sys
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

import numpy as np

SOURCE = Path("crawler/data/db/RECOVERY_RAG_PDF_V2.sqlite")
LEXICAL = Path("crawler/data/db/RECOVERY_LEXICAL_PDF_V2.sqlite")
HOST = "http://127.0.0.1:11434"
MODEL = "bge-m3"


def readonly(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Required database not found: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def fts_expression(question: str) -> str:
    """Use the project's own lexical expansion, preserving existing policy aliases."""
    try:
        from crawler.core.retrieval import _lexical_match_query
        return _lexical_match_query(question)
    except ImportError:
        # Minimal fallback if only a subset of crawler dependencies is installed.
        words = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", question.casefold(), re.UNICODE)))
        if not words:
            raise ValueError("Question contains no searchable terms")
        return " OR ".join('"' + word.replace('"', '') + '"*' for word in words[:40])


def lexical_candidates(question: str, n: int, municipality_id: str | None):
    expression = fts_expression(question)
    with closing(readonly(LEXICAL)) as fts, closing(readonly(SOURCE)) as source:
        fts_columns = {r[1] for r in fts.execute("PRAGMA table_info(chunks_fts)")}
        if not {"chunk_id", "municipality_id", "text"}.issubset(fts_columns):
            raise RuntimeError("Unexpected FTS schema; expected chunk_id, municipality_id and text")
        total_fts = fts.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        total_src = source.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if total_fts != total_src:
            raise RuntimeError(f"FTS/source row mismatch: {total_fts} vs {total_src}")
        if total_src < 1:
            raise RuntimeError("PDF-V2 corpus is empty")

        if municipality_id:
            result = fts.execute(
                "SELECT chunk_id, municipality_id, text, bm25(chunks_fts) AS bm "
                "FROM chunks_fts WHERE chunks_fts MATCH ? AND municipality_id=? "
                "ORDER BY bm, chunk_id LIMIT ?",
                (expression, municipality_id, n),
            ).fetchall()
        else:
            result = fts.execute(
                "SELECT chunk_id, municipality_id, text, bm25(chunks_fts) AS bm "
                "FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm, chunk_id LIMIT ?",
                (expression, n),
            ).fetchall()
        candidates = []
        for lexrank, (chunk_id, city, indexed_text, bm25) in enumerate(result, start=1):
            row = source.execute(
                "SELECT c.chunk_id, c.text, c.heading_context, c.document_id, "
                "d.url_canonical FROM chunks c "
                "JOIN documents_raw d ON c.document_id=d.document_id WHERE c.chunk_id=?",
                (str(chunk_id),),
            ).fetchone()
            if not row:
                raise RuntimeError(f"Chunk {chunk_id!r} from FTS missing in source corpus")
            _, actual_text, heading, document_id, url = row
            if actual_text != indexed_text:
                raise RuntimeError(f"Chunk {chunk_id!r} text differs between FTS and source; stop")
            # Verify municipality provenance rather than blindly trusting the FTS field.
            source_cities = {str(r[0]) for r in source.execute(
                "SELECT municipality_id FROM documents_raw WHERE document_id=? AND municipality_id IS NOT NULL",
                (document_id,),
            )}
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='municipality_documents'"
            ).fetchone():
                source_cities.update(str(r[0]) for r in source.execute(
                    "SELECT municipality_id FROM municipality_documents WHERE document_id=?",
                    (document_id,),
                ))
            if str(city) not in source_cities:
                raise RuntimeError(f"FTS municipality {city} not supported by document {document_id}")
            candidates.append({
                "chunk_id": str(chunk_id), "municipality_id": str(city),
                "document_id": str(document_id), "url": str(url or ""),
                "heading": str(heading or ""), "text": str(actual_text),
                "lexical_rank": lexrank, "bm25": float(bm25),
            })
    return candidates


def embed_one(text: str) -> np.ndarray:
    payload = json.dumps({"model": MODEL, "input": [text], "truncate": True,
                          "keep_alive": "10m"}, ensure_ascii=False).encode("utf-8")
    req = Request(HOST + "/api/embed", data=payload,
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.load(resp)
    except (TimeoutError, socket.timeout, URLError, HTTPError) as exc:
        raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
    vectors = result.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise RuntimeError("Unexpected Ollama embedding response")
    vector = np.asarray(vectors[0], dtype=np.float32)
    if vector.shape != (1024,) or not np.all(np.isfinite(vector)):
        raise RuntimeError(f"Unexpected BGE-M3 embedding shape or values: {vector.shape}")
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise RuntimeError("Invalid BGE-M3 embedding norm")
    return vector / norm


def rank_candidates(question: str, candidates: list[dict], lexical_only: bool) -> tuple[list[dict], str]:
    if lexical_only or not candidates:
        return candidates, "lexical-only"
    try:
        qvector = embed_one(question)
        scored = []
        for i, item in enumerate(candidates, start=1):
            # On-demand BGE uses only a small number of candidate chunks.
            # Truncation prevents a single oversized source from blocking completion.
            docvector = embed_one(item["text"])
            scored.append((float(np.dot(qvector, docvector)), i - 1))
        scored.sort(key=lambda value: (-value[0], candidates[value[1]]["chunk_id"]))
        semantic_ranks = {index: rank for rank, (_score, index) in enumerate(scored, start=1)}
        similarity = {index: score for score, index in scored}
        for i, item in enumerate(candidates):
            # RRF balances the lexical candidate order with semantic similarity.
            item["semantic_similarity"] = similarity[i]
            item["semantic_rank_among_candidates"] = semantic_ranks[i]
            item["fusion_score"] = 1 / (60 + item["lexical_rank"]) + 1 / (60 + semantic_ranks[i])
        candidates.sort(key=lambda item: (-item["fusion_score"], item["chunk_id"]))
        return candidates, "lexical+BGE-M3-rerank"
    except (RuntimeError, ValueError) as exc:
        print(f"WARNING: {exc}. Falling back to lexical ranking for this query.", file=sys.stderr)
        return sorted(candidates, key=lambda item: item["lexical_rank"]), "lexical-fallback"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Policy question in English or German")
    parser.add_argument("--candidates", type=int, default=12, help="FTS candidates to rerank (default 12)")
    parser.add_argument("--top-k", type=int, default=6, help="Returned passages (default 6)")
    parser.add_argument("--municipality-id", default=None, help="Optional official municipality ID")
    parser.add_argument("--lexical-only", action="store_true", help="Skip Ollama and return FTS BM25 results immediately")
    parser.add_argument("--save-json", type=Path, default=None, help="Optional full-text evidence JSON output")
    args = parser.parse_args()
    if not 1 <= args.candidates <= 50:
        parser.error("--candidates must be between 1 and 50")
    if not 1 <= args.top_k <= args.candidates:
        parser.error("--top-k must be between 1 and --candidates")
    rows = lexical_candidates(args.query, args.candidates, args.municipality_id)
    print(f"FTS CANDIDATES: {len(rows)} (full PDF-V2 corpus)", flush=True)
    ranked, method = rank_candidates(args.query, rows, args.lexical_only)
    ranked = ranked[:args.top_k]
    print(f"METHOD: {method}")
    print("Retrieval only; policy dates and Yes/No/Unknown still require verified reasoning.\n")
    for rank, item in enumerate(ranked, start=1):
        print(f"[{rank}] Municipality: {item['municipality_id']} | Chunk: {item['chunk_id']}")
        print("SOURCE:", item["url"])
        if item["heading"]:
            print("HEADING:", item["heading"])
        if "semantic_similarity" in item:
            print(f"BGE score: {item['semantic_similarity']:.4f} | BM25 rank: {item['lexical_rank']}")
        print("TEXT:", item["text"][:650].replace("\n", " ").strip())
        print()
    if args.save_json:
        output = {"query": args.query, "method": method,
                  "source_database": str(SOURCE), "lexical_database": str(LEXICAL),
                  "results": ranked,
                  "note": "Candidate retrieval only; verify source, time, eligibility before answering."}
        args.save_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SAVED:", args.save_json)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
