"""
prune_db.py
===========
Verkleinert die Arbeits-Datenbank, nachdem nachgelagerte Stages fertig sind.

Was Platz frisst (typisch):
  1. segments.text     — Volltext jeder gecrawlten Seite (größter Posten)
  2. chunks.text/embedding — wird für Retrieval gebraucht, NICHT löschen
  3. WAL-Datei (*.sqlite-wal) — wächst bei langen Läufen

Alle destruktiven Aktionen erfordern explizite Flags. Ohne Flags wird nur
ein Größen-Report ausgegeben (immer gefahrlos).

WICHTIG: segments.text wird von analyze_topics.py (BERTopic) gelesen.
Nach --clear-chunked-segment-text funktioniert diese Analyse nur noch
auf Segmenten ohne Chunks. Vorher ein Backup anlegen!

Usage:
    python -m crawler.scripts.prune_db                       # nur Report
    python -m crawler.scripts.prune_db --prune-negative      # is_negative=1 Segmente löschen
    python -m crawler.scripts.prune_db --clear-chunked-segment-text
    python -m crawler.scripts.prune_db --vacuum-into slim.sqlite
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from crawler.core.config import DB_PATH


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    return column in cols


def report(con: sqlite3.Connection, db_path: Path) -> None:
    print(f"\n{'═' * 62}")
    print(f" DB-Größen-Report: {db_path}")
    print(f"{'═' * 62}")

    size = db_path.stat().st_size if db_path.exists() else 0
    print(f"  Datei                    : {_mb(size)}")
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            print(f"  {side.name:24s} : {_mb(side.stat().st_size)}")

    print()
    tables = [
        ("documents_raw", "length(COALESCE(url_canonical,'')) + length(COALESCE(raw_path,''))"),
        ("segments", "length(COALESCE(text,''))"),
        ("chunks", "length(COALESCE(text,'')) + length(COALESCE(embedding,''))"),
        ("graph_triplets", "length(COALESCE(graph_json,''))"),
        ("document_summaries", "length(COALESCE(summary,''))"),
        ("visited", "length(COALESCE(url_canonical,''))"),
    ]
    for table, size_expr in tables:
        if not _table_exists(con, table):
            continue
        n, byt = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM({size_expr}), 0) FROM {table}"
        ).fetchone()
        print(f"  {table:24s} : {n:>9,} Zeilen   ~{_mb(byt)} Nutzdaten")

    # Prune-Potenzial
    print()
    if _table_exists(con, "segments"):
        if _column_exists(con, "segments", "is_negative"):
            neg = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(text)),0) FROM segments "
                "WHERE COALESCE(is_negative,0)=1"
            ).fetchone()
            print(f"  → --prune-negative würde {neg[0]:,} Segmente löschen (~{_mb(neg[1])})")
        if _table_exists(con, "chunks"):
            chunked = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(s.text)),0) FROM segments s "
                "WHERE s.text IS NOT NULL AND s.text != '' AND s.segment_id IN "
                "(SELECT DISTINCT segment_id FROM chunks WHERE segment_id IS NOT NULL)"
            ).fetchone()
            print(f"  → --clear-chunked-segment-text würde {chunked[0]:,} Segment-Texte "
                  f"leeren (~{_mb(chunked[1])})")
    print(f"{'═' * 62}\n")


def mark_boilerplate(con: sqlite3.Connection, min_docs: int) -> None:
    """Markiert Segmente als is_negative, deren identischer Text (segment_hash)
    in >= min_docs Dokumenten DERSELBEN Kommune vorkommt.

    Das sind Menüs, Footer, Cookie-Banner — der Dedup-Index verhindert
    Duplikate nur innerhalb eines Dokuments, nicht seitenübergreifend.
    Markierte Segmente werden von Chunking und Graph-Extraktion übersprungen.
    """
    if not _column_exists(con, "segments", "is_negative"):
        try:
            con.execute("ALTER TABLE segments ADD COLUMN is_negative INTEGER")
        except Exception:
            pass
    cur = con.execute(
        """
        UPDATE segments SET is_negative = 1
        WHERE COALESCE(is_negative, 0) = 0 AND segment_id IN (
            SELECT s.segment_id
            FROM segments s
            JOIN documents_raw d ON d.document_id = s.document_id
            JOIN (
                SELECT d2.municipality_id AS muni, s2.segment_hash AS h
                FROM segments s2
                JOIN documents_raw d2 ON d2.document_id = s2.document_id
                GROUP BY d2.municipality_id, s2.segment_hash
                HAVING COUNT(DISTINCT s2.document_id) >= ?
            ) rep ON rep.muni = d.municipality_id AND rep.h = s.segment_hash
        )
        """,
        (min_docs,),
    )
    print(f"  ✓ {cur.rowcount:,} Boilerplate-Segmente markiert "
          f"(identischer Text in ≥{min_docs} Dokumenten einer Kommune)")


def prune_negative(con: sqlite3.Connection) -> None:
    if not _column_exists(con, "segments", "is_negative"):
        print("  [!] Spalte is_negative existiert nicht — nichts zu tun.")
        return
    cur = con.execute("DELETE FROM segments WHERE COALESCE(is_negative,0)=1")
    print(f"  ✓ {cur.rowcount:,} negative Segmente gelöscht")


def clear_chunked_segment_text(con: sqlite3.Connection) -> None:
    """Leert segments.text für Segmente, die bereits gechunkt wurden.

    Die Zeilen bleiben erhalten (Provenance + Dedup über segment_hash),
    nur der Volltext wird freigegeben. chunk_documents.py überspringt
    geleerte Segmente automatisch (length(text) >= min_len Filter).
    """
    if not _table_exists(con, "chunks"):
        print("  [!] chunks-Tabelle existiert nicht — erst Chunking laufen lassen.")
        return
    cur = con.execute(
        "UPDATE segments SET text = '' "
        "WHERE text IS NOT NULL AND text != '' AND segment_id IN "
        "(SELECT DISTINCT segment_id FROM chunks WHERE segment_id IS NOT NULL)"
    )
    print(f"  ✓ {cur.rowcount:,} Segment-Texte geleert (Zeilen + Hashes bleiben erhalten)")


def vacuum(con: sqlite3.Connection, into: str | None, db_path: Path) -> None:
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if into:
        # VACUUM INTO braucht nur Platz für die NEUE (kleinere) Datei
        target = Path(into)
        if target.exists():
            print(f"  [!] {target} existiert bereits — abgebrochen.")
            return
        print(f"  VACUUM INTO {target} … (kann dauern)")
        con.execute("VACUUM INTO ?", (str(target),))
        print(f"  ✓ Kompakte Kopie: {target} ({_mb(target.stat().st_size)})")
        print(f"    Original unverändert. Nach Prüfung manuell ersetzen:")
        print(f"    mv {target} {db_path}")
    else:
        # In-place VACUUM braucht transient bis zu 2x DB-Größe freien Platz!
        print("  VACUUM (in place) … braucht transient bis zu 2x DB-Größe an Festplatte")
        con.execute("VACUUM")
        print(f"  ✓ VACUUM fertig ({_mb(db_path.stat().st_size)})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Arbeits-DB verkleinern (Report ohne Flags)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--mark-boilerplate", action="store_true",
                    help="Seitenübergreifend identische Segmente (Menüs/Footer) als is_negative markieren")
    ap.add_argument("--boilerplate-min-docs", type=int, default=5,
                    help="Ab wie vielen Dokumenten pro Kommune ein Segment als Boilerplate gilt (default: 5)")
    ap.add_argument("--prune-negative", action="store_true",
                    help="Segmente mit is_negative=1 löschen (nie gechunkt)")
    ap.add_argument("--clear-chunked-segment-text", action="store_true",
                    help="segments.text leeren, wo bereits Chunks existieren")
    ap.add_argument("--vacuum", action="store_true",
                    help="DB in place kompaktieren (braucht 2x Platz transient)")
    ap.add_argument("--vacuum-into", metavar="PATH",
                    help="Kompakte Kopie schreiben (braucht nur Platz für die Kopie)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[!] DB nicht gefunden: {db_path}")
        return

    con = sqlite3.connect(str(db_path), timeout=60.0, isolation_level=None)
    con.execute("PRAGMA busy_timeout=60000")

    report(con, db_path)

    if args.mark_boilerplate:
        mark_boilerplate(con, args.boilerplate_min_docs)
    if args.prune_negative:
        prune_negative(con)
    if args.clear_chunked_segment_text:
        clear_chunked_segment_text(con)
    if args.vacuum or args.vacuum_into:
        vacuum(con, args.vacuum_into, db_path)
    elif args.prune_negative or args.clear_chunked_segment_text:
        print("\n  Hinweis: Gelöschte Daten geben erst nach VACUUM Festplatte frei:")
        print("    python -m crawler.scripts.prune_db --vacuum-into slim.sqlite")

    con.close()


if __name__ == "__main__":
    main()
