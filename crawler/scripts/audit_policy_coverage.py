from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

TARGET_TERMS = (
    "balkonkraft",
    "steckersolar",
    "steckersolarger",
    "balkon-pv",
    "balkon pv",
    "mini-pv",
    "mini pv",
)


def norm_url(url: str) -> tuple[str, str]:
    p = urlsplit(url or "")
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "/").rstrip("/").lower() or "/"
    return host, path


def load_names(municipalities_db: Path) -> tuple[dict[str, str], dict[str, str]]:
    con = sqlite3.connect(municipalities_db)
    try:
        rows = con.execute("SELECT ags, name FROM municipalities").fetchall()
    finally:
        con.close()
    by_name = {str(name): str(ags) for ags, name in rows}
    by_ags = {str(ags): str(name) for ags, name in rows}
    return by_name, by_ags


def evidence_counts(con: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    where = " OR ".join("LOWER(s.text) LIKE ?" for _ in TARGET_TERMS)
    params = [f"%{t}%" for t in TARGET_TERMS]
    sql = f"""
        SELECT d.municipality_id,
               COUNT(DISTINCT s.segment_id),
               COUNT(DISTINCT d.document_id)
        FROM segments s
        JOIN documents_raw d ON d.document_id = s.document_id
        WHERE {where}
        GROUP BY d.municipality_id
    """
    return {str(mid): (int(sn), int(dn)) for mid, sn, dn in con.execute(sql, params)}


def sample_evidence(con: sqlite3.Connection, muni_id: str, limit: int = 3) -> list[tuple[str, str]]:
    where = " OR ".join("LOWER(s.text) LIKE ?" for _ in TARGET_TERMS)
    params = [muni_id] + [f"%{t}%" for t in TARGET_TERMS] + [limit]
    sql = f"""
        SELECT COALESCE(d.url_final, d.url_canonical), s.text
        FROM segments s
        JOIN documents_raw d ON d.document_id = s.document_id
        WHERE d.municipality_id = ? AND ({where})
        ORDER BY COALESCE(s.impact_score, 0) DESC, length(s.text) DESC
        LIMIT ?
    """
    out = []
    for url, text in con.execute(sql, params):
        clean = " ".join((text or "").split())
        out.append((url or "", clean[:500]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit 9-city policy source/evidence coverage")
    ap.add_argument("--db", required=True, help="Crawl SQLite DB to audit")
    ap.add_argument(
        "--benchmark",
        default="evaluation/Balkonkraftwerk_Benchmark_Bayern.csv",
        help="Benchmark CSV (evaluation only; never used as crawl seeds)",
    )
    ap.add_argument(
        "--municipalities-db",
        default="crawler/data/db/municipalities.sqlite",
    )
    ap.add_argument("--samples", type=int, default=2)
    args = ap.parse_args()

    db = Path(args.db)
    benchmark = Path(args.benchmark)
    muni_db = Path(args.municipalities_db)
    if not db.is_file():
        raise SystemExit(f"crawl DB not found: {db}")
    if not benchmark.is_file():
        raise SystemExit(f"benchmark not found: {benchmark}")
    if not muni_db.is_file():
        raise SystemExit(f"municipalities DB not found: {muni_db}")

    name_to_ags, _ = load_names(muni_db)
    rows = list(csv.DictReader(benchmark.open(encoding="utf-8-sig")))
    con = sqlite3.connect(db)
    try:
        docs: dict[str, list[str]] = defaultdict(list)
        for mid, url in con.execute(
            "SELECT municipality_id, COALESCE(url_final,url_canonical) "
            "FROM documents_raw WHERE COALESCE(url_final,url_canonical) IS NOT NULL"
        ):
            docs[str(mid)].append(str(url))

        ev = evidence_counts(con)

        print("=== POLICY COVERAGE AUDIT ===")
        print(f"DB: {db}")
        print("Gold URLs are used only for post-crawl evaluation, never for seeding.\n")

        known_gold = 0
        domain_hits = 0
        exact_hits = 0
        evidence_munis = 0

        for row in rows:
            name = row.get("municipality", "").strip()
            label = row.get("human_label", "").strip()
            gold = row.get("url", "").strip()
            mid = name_to_ags.get(name, "")
            urls = docs.get(mid, [])
            seg_n, doc_n = ev.get(mid, (0, 0))
            if seg_n:
                evidence_munis += 1

            exact = False
            same_domain = 0
            if gold:
                known_gold += 1
                gd, gp = norm_url(gold)
                exact = any(norm_url(u) == (gd, gp) for u in urls)
                same_domain = sum(1 for u in urls if norm_url(u)[0] == gd)
                if same_domain:
                    domain_hits += 1
                if exact:
                    exact_hits += 1

            print(
                f"{name:12s} | gold={label:7s} | docs={len(urls):3d} "
                f"| gold_domain_docs={same_domain:3d} | exact={'YES' if exact else 'NO ':3s} "
                f"| evidence_segments={seg_n:3d} | evidence_docs={doc_n:2d}"
            )
            if args.samples > 0 and seg_n:
                for url, text in sample_evidence(con, mid, args.samples):
                    print(f"    SOURCE: {url}")
                    print(f"    TEXT:   {text}")

        print("\n=== SUMMARY ===")
        print(f"Known benchmark URLs: {known_gold}")
        print(f"Gold-domain coverage: {domain_hits}/{known_gold}")
        print(f"Exact-page coverage:  {exact_hits}/{known_gold}")
        print(f"Municipalities with target-policy evidence: {evidence_munis}/{len(rows)}")

        # Readiness gate: definite Yes/No rows should have at least one target-policy
        # evidence segment. Unknown rows are allowed to remain evidence-insufficient.
        definite = [r for r in rows if r.get("human_label", "").strip().lower() in {"yes", "no"}]
        missing_definite = []
        for r in definite:
            mid = name_to_ags.get(r.get("municipality", "").strip(), "")
            if ev.get(mid, (0, 0))[0] == 0:
                missing_definite.append(r.get("municipality", "").strip())

        print("\n=== READINESS GATE ===")
        if missing_definite:
            print("NOT READY for fair RAG/GraphRAG evaluation.")
            print("Definite-label municipalities without target-policy evidence:")
            for name in missing_definite:
                print(f"  - {name}")
            raise SystemExit(2)
        print("READY: every definite Yes/No municipality has target-policy evidence.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
