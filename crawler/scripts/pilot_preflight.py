"""Read-only inspection of a pilot crawl DB; does not assume graph was built.

Unlike grep of log messages, this script reports whether downstream artifacts
actually exist and whether the required SQLite relationships are populated.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def inspect(path: Path, expected_ags: list[str] | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    try:
        tables = _tables(conn)
        findings: list[dict[str, str]] = []
        counts: dict[str, int] = {}
        for table in ["seed_jobs", "documents_raw", "municipality_documents", "segments", "chunks", "graph_triplets", "graph_entities", "entity_canonical", "document_summaries", "embedding_artifacts", "extraction_runs"]:
            if table in tables:
                counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for required in ("seed_jobs", "documents_raw", "municipality_documents", "segments", "chunks"):
            if required not in tables:
                findings.append({"severity": "critical", "issue": f"Missing required pipeline table {required}"})
        if counts.get("chunks", 0) == 0:
            findings.append({"severity": "high", "issue": "No chunks available to index/retrieve"})
        if counts.get("documents_raw", 0) and not counts.get("segments", 0):
            findings.append({"severity": "critical", "issue": "Raw documents exist but no segments"})
        if "chunks" in tables:
            cols = _columns(conn, "chunks")
            if "embedding" in cols:
                unembedded = int(conn.execute('SELECT COUNT(*) FROM "chunks" WHERE embedding IS NULL').fetchone()[0])
                counts["chunks_without_embedded_vector"] = unembedded
                if counts["chunks"] and unembedded == counts["chunks"]:
                    findings.append({"severity": "high", "issue": "All chunks lack inline embeddings; check if a separate artifact store is used"})
            if "segment_id" in cols and "segments" in tables:
                missing = int(conn.execute('SELECT COUNT(*) FROM chunks c LEFT JOIN segments s ON s.segment_id=c.segment_id WHERE c.segment_id IS NOT NULL AND s.segment_id IS NULL').fetchone()[0])
                counts["chunks_with_missing_parent_segment"] = missing
                if missing:
                    findings.append({"severity": "critical", "issue": f"{missing} chunks refer to absent segments"})
        municipalities: dict[str, dict[str, int]] = {}
        if "documents_raw" in tables:
            cols = _columns(conn, "documents_raw")
            if "municipality_id" in cols:
                for ags, n in conn.execute('SELECT municipality_id, COUNT(*) FROM documents_raw GROUP BY municipality_id'):
                    municipalities.setdefault(str(ags), {})["documents_primary"] = int(n)
        if "municipality_documents" in tables:
            cols = _columns(conn, "municipality_documents")
            if {"municipality_id", "document_id"}.issubset(cols):
                for ags, n in conn.execute('SELECT municipality_id, COUNT(DISTINCT document_id) FROM municipality_documents GROUP BY municipality_id'):
                    municipalities.setdefault(str(ags), {})["documents_linked"] = int(n)
        for ags in expected_ags or []:
            if not (municipalities.get(ags, {}).get("documents_linked", 0) or municipalities.get(ags, {}).get("documents_primary", 0)):
                findings.append({"severity": "high", "issue": f"No crawled documents found for expected AGS {ags}"})
        # No confidence about graph provenance if the graph is on a remote Neo4j instance.
        if not counts.get("graph_triplets", 0):
            findings.append({"severity": "info", "issue": "No graph_triplets rows in this SQLite DB; graph may exist externally, verify separately"})
        return {"database": str(path), "tables": sorted(tables), "row_counts": counts,
                "municipalities": municipalities, "expected_ags": expected_ags or [], "findings": findings,
                "note": "Read-only structural check only; does NOT measure crawl recall, policy correctness, currentness, or Neo4j state."}
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only pilot SQLite preflight")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--ags", default="", help="Comma-separated expected 8-digit AGS IDs")
    p.add_argument("--report", type=Path)
    a = p.parse_args()
    ags = [i.strip() for i in a.ags.split(",") if i.strip()]
    if any(len(i) != 8 or not i.isdigit() for i in ags):
        p.error("--ags must contain 8-digit AGS IDs")
    result = inspect(a.db, ags)
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(output, encoding="utf-8")
    print(output)
    if any(f["severity"] == "critical" for f in result["findings"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
