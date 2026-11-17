"""Read-only expanded lexical retrieval plus optional on-demand BGE-M3 ranking.

Run from Klima_GraphRAG project root, beside klima_fast_retrieval.py.
Never writes to project databases or triggers corpus-wide embeddings.
This is evidence retrieval, not a policy decision engine.

Example:
 python klima_fast_retrieval_v2.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --lexical-only
 python klima_fast_retrieval_v2.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --candidates 10 --top-k 6
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

import numpy as np
import klima_fast_retrieval as prior

# General-purpose terms across energy and climate policy questions. Not benchmark IDs.
ALIASES = {
    "photovoltaics": ["photovoltaik", "dachphotovoltaik", "fassadenphotovoltaik", "gebäudephotovoltaik", "pv", "solaranlage", "solarstrom", "solarmodul"],
    "balcony_pv": ["balkonkraftwerk", "steckersolar", "balkonmodul"],
    "heat_pump": ["wärmepumpe", "waermepumpe", "heizungstausch"],
    "district_heat": ["fernwärme", "nahwärme", "wärmenetz", "waermenetz"],
    "energy_renovation": ["sanierung", "dämmung", "daemmung", "energieeffizienz"],
    "electric_mobility": ["elektromobilität", "elektromobilitaet", "ladeinfrastruktur", "wallbox"],
    "wind": ["windkraft", "windenergie", "windanlage"],
    "funding": ["förder", "foerder", "zuschuss", "förderprogramm", "subvention", "beihilfe", "förderantrag", "grant", "subsidy", "funding"],
    "regulation": ["richtlinie", "satzung", "verordnung", "vorschrift", "regelung"],
}
TRIGGERS = {
    "photovoltaics": ["photovolta", "solar", " pv", "dach-pv", "pv-"],
    "balcony_pv": ["balkonkraft", "steckersolar", "balcony solar"],
    "heat_pump": ["wärmepump", "waermepump", "heat pump"],
    "district_heat": ["fernwär", "nahwär", "wärmenetz", "district heat"],
    "energy_renovation": ["sanierung", "dämmung", "insulation", "renovation"],
    "electric_mobility": ["elektromobil", "electric vehicle", "wallbox", "charging station"],
    "wind": ["windkraft", "windenergie", "wind turbine"],
    "funding": ["förder", "foerder", "zuschuss", "subsid", "grant", "fund", "support", "bezuschuss"],
    "regulation": ["richtlinie", "regulation", "ordinance", "satzung", "rule", "policy"],
}
STOP = {"regensburg", "bayreuth", "fürth", "fuerth", "augsburg", "passau", "nürnberg", "nuernberg", "munich", "münchen", "stadt", "gemeinde", "does", "do", "is", "are", "the", "for", "with", "in", "on", "what", "how", "gibt", "es", "die", "der", "das", "und", "von", "für", "fuer", "wird", "eine", "einen", "welche", "welcher", "foerdert", "fördert", "fund", "funding", "förderung", "foerderung", "zuschuss", "unterstützt"}


def terms(text):
    return re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)


def fts_or(words):
    # Parameterized MATCH expressions; quoted token prevents FTS query injection.
    parts = []
    for word in dict.fromkeys(words):
        w = re.sub(r"[^\w]", "", word.casefold())
        if not w or len(w) < 2:
            continue
        parts.append('"' + w.replace('"', '') + '"' + ("" if len(w) <= 2 else "*"))
    return " OR ".join(parts)


def query_plan(question):
    lowered = " " + question.casefold() + " "
    found = [key for key, patterns in TRIGGERS.items() if any(p in lowered for p in patterns)]
    topic_keys = [key for key in found if key not in ("funding", "regulation")]
    intent_keys = [key for key in found if key in ("funding", "regulation")]
    topic_words = list(dict.fromkeys(w for key in topic_keys for w in ALIASES[key]))
    if not topic_words:
        topic_words = [w for w in terms(question) if w not in STOP and len(w) >= 4][:7]
    intent_words = list(dict.fromkeys(w for key in intent_keys for w in ALIASES[key]))
    topic_expr = fts_or(topic_words)
    intent_expr = fts_or(intent_words)
    plan = []
    if topic_expr and intent_expr:
        plan.append((f"({topic_expr}) AND ({intent_expr})", 120, "topic+intent"))
    if topic_expr:
        plan.append((topic_expr, 200, "topic"))
    if intent_expr and not topic_expr:
        plan.append((intent_expr, 120, "intent"))
    # Original project's lexical expansion remains a supplemental channel.
    try:
        existing = prior.fts_expression(question)
        if existing:
            plan.append((existing, 80, "original"))
    except (ValueError, RuntimeError):
        pass
    if not plan:
        raise ValueError("Question has no searchable terms")
    return plan, topic_words, intent_words


def fetch_evidence(question, municipality_id, count):
    plan, topic_words, intent_words = query_plan(question)
    # Keep fetches read-only and verify FTS is synchronized with the source.
    with closing(prior.readonly(prior.LEXICAL)) as fts, closing(prior.readonly(prior.SOURCE)) as src:
        n1 = fts.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        n2 = src.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if n1 != n2:
            raise RuntimeError(f"FTS/source count mismatch: {n1} vs {n2}")
        by_chunk = {}
        for expr, limit, group in plan:
            try:
                if municipality_id:
                    found = fts.execute(
                        "SELECT chunk_id, municipality_id, bm25(chunks_fts) FROM chunks_fts "
                        "WHERE chunks_fts MATCH ? AND municipality_id=? ORDER BY bm25(chunks_fts) LIMIT ?",
                        (expr, municipality_id, limit)).fetchall()
                else:
                    found = fts.execute(
                        "SELECT chunk_id, municipality_id, bm25(chunks_fts) FROM chunks_fts "
                        "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                        (expr, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                raise RuntimeError(f"FTS query failed for {group}: {expr!r}: {exc}") from exc
            print(f"SEARCH {group}: {len(found)} matches fetched", flush=True)
            for rank, (chunk_id, city, bm) in enumerate(found, 1):
                key = str(chunk_id)
                hit = by_chunk.setdefault(key, {"chunk_id": key, "municipality_id": str(city),
                                                "groups": {}, "best_bm25": float(bm)})
                hit["groups"][group] = min(rank, hit["groups"].get(group, rank))
                hit["best_bm25"] = min(float(bm), hit["best_bm25"])
        print(f"UNIQUE RETRIEVAL CANDIDATES: {len(by_chunk)}", flush=True)
        selected = []
        for hit in by_chunk.values():
            record = src.execute(
                "SELECT c.text,c.heading_context,c.document_id,d.url_canonical "
                "FROM chunks c JOIN documents_raw d ON c.document_id=d.document_id "
                "WHERE c.chunk_id=?", (hit["chunk_id"],)).fetchone()
            if record is None:
                raise RuntimeError("Candidate chunk missing from source: " + hit["chunk_id"])
            txt, heading, document_id, url = record
            hit.update(text=str(txt or ""), heading=str(heading or ""),
                       document_id=str(document_id), url=str(url or ""))
            selected.append(hit)
    # Prefer substantive topic+intent passages and diverse documents.
    topic_words = [w.casefold() for w in topic_words]
    intent_words = [w.casefold() for w in intent_words]
    for item in selected:
        text = item["text"].casefold()
        heading_url = (item["heading"] + " " + item["url"]).casefold()
        has_topic = any(w in text for w in topic_words)
        has_intent = any(w in text for w in intent_words)
        head_topic = any(w in heading_url for w in topic_words)
        head_intent = any(w in heading_url for w in intent_words)
        readable = len(terms(text))
        # Transparent heuristic for shortlist selection, not a policy evidence score.
        item["candidate_score"] = (
            (8 if "topic+intent" in item["groups"] else 0)
            + (3 if has_topic else 0) + (3 if has_intent else 0)
            + (2 if head_topic else 0) + (1 if head_intent else 0)
            + (1.5 if readable >= 30 else 0) - (3 if readable < 12 else 0)
            + sum(1.0 / (1 + rank / 20.0) for rank in item["groups"].values())
        )
    selected.sort(key=lambda r: (-r["candidate_score"], r["chunk_id"]))
    # Take strong passages first, but leave room for additional documents in the shortlist.
    shortlist, per_doc = [], defaultdict(int)
    for item in selected:
        if per_doc[item["document_id"]] >= 2:
            continue
        shortlist.append(item)
        per_doc[item["document_id"]] += 1
        if len(shortlist) >= count:
            break
    return shortlist, plan


def rerank(question, rows):
    q = prior.embed_one(question)
    for item in rows:
        vector = prior.embed_one(item["heading"] + "\n" + item["text"])
        item["bge_similarity"] = float(np.dot(q, vector))
    # Similarity leads; candidate relevance only as a tie-breaker.
    rows.sort(key=lambda r: (-r["bge_similarity"], -r["candidate_score"], r["chunk_id"]))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--municipality-id", default=None)
    parser.add_argument("--candidates", type=int, default=12, help="Max passages sent to BGE-M3 (default 12)")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--save-json", type=Path, default=None)
    args = parser.parse_args()
    if not 1 <= args.candidates <= 30 or not 1 <= args.top_k <= args.candidates:
        parser.error("Use --candidates 1..30 and --top-k 1..candidates")
    rows, plan = fetch_evidence(args.query, args.municipality_id, args.candidates)
    method = "expanded-lexical"
    if not args.lexical_only and rows:
        try:
            rows = rerank(args.query, rows)
            method = "expanded-lexical+BGE-M3"
        except RuntimeError as exc:
            print(f"WARNING: BGE-M3 unavailable; showing lexical shortlist: {exc}", file=sys.stderr)
    print(f"METHOD: {method}")
    print("RETRIEVAL ONLY. A source showing a policy reference does not prove active funding.\n")
    for i, item in enumerate(rows[:args.top_k], 1):
        print(f"[{i}] Municipality: {item['municipality_id']} | Chunk: {item['chunk_id']}")
        print("SOURCE:", item["url"])
        if item["heading"]:
            print("HEADING:", item["heading"][:200])
        if "bge_similarity" in item:
            print(f"BGE similarity: {item['bge_similarity']:.4f}")
        print("SEARCH CHANNELS:", ", ".join(sorted(item["groups"])))
        print("TEXT:", item["text"][:850].replace("\n", " "))
        print()
    if args.save_json:
        payload = {"query": args.query, "method": method, "total_in_shortlist": len(rows),
                   "results": rows, "disclaimer": "Not policy answers; source, effective date, eligibility require validation"}
        args.save_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SAVED:", args.save_json)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
