"""
inspect_graph.py  —  Show what was extracted into graph_triplets.

Usage:
    python demo/inspect_graph.py
    python demo/inspect_graph.py --db demo/demo.sqlite --top 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

DEMO_DB = Path("demo/demo.sqlite")


def main(db: Path, top: int) -> None:
    con = sqlite3.connect(str(db))

    # ── overview ──────────────────────────────────────────────────────────────
    n_rows = con.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0]
    if n_rows == 0:
        print("[!] graph_triplets ist leer. Bitte zuerst extract_graph_spacy.py ausführen.")
        con.close()
        return

    total_e = con.execute("SELECT SUM(entity_count) FROM graph_triplets").fetchone()[0] or 0
    total_r = con.execute("SELECT SUM(relationship_count) FROM graph_triplets").fetchone()[0] or 0
    model   = con.execute("SELECT model_name FROM graph_triplets LIMIT 1").fetchone()[0]

    print(f"\n{'═'*60}")
    print(f" Graph-Statistiken  (Modell: {model})")
    print(f"{'═'*60}")
    print(f"  Chunks mit Extraktion : {n_rows}")
    print(f"  Gesamt Entitäten      : {total_e}")
    print(f"  Gesamt Beziehungen    : {total_r}")

    # ── per-municipality ───────────────────────────────────────────────────────
    rows = con.execute(
        "SELECT municipality_id, COUNT(*), SUM(entity_count), SUM(relationship_count) "
        "FROM graph_triplets GROUP BY municipality_id ORDER BY SUM(entity_count) DESC"
    ).fetchall()
    print("\n  Pro Kommune:")
    for muni, chunks, ents, rels in rows:
        print(f"    {muni:12s}  chunks={chunks:4d}  entities={ents:5d}  rels={rels:4d}")

    # ── entity type breakdown ─────────────────────────────────────────────────
    entity_types: Counter = Counter()
    entity_cats:  Counter = Counter()
    entity_names: Counter = Counter()
    rel_types:    Counter = Counter()

    for (graph_json,) in con.execute("SELECT graph_json FROM graph_triplets"):
        try:
            kg = json.loads(graph_json)
        except json.JSONDecodeError:
            continue
        for ent in kg.get("entities", []):
            entity_types[ent.get("type", "?")] += 1
            entity_cats[ent.get("category", "?")] += 1
            entity_names[ent.get("name", "")] += 1
        for rel in kg.get("relationships", []):
            rel_types[rel.get("relation_type", "?")] += 1

    print("\n  Entitätstypen:")
    for etype, cnt in entity_types.most_common():
        bar = "█" * min(cnt // 5 + 1, 30)
        print(f"    {etype:20s}  {cnt:5d}  {bar}")

    print("\n  Kategorien:")
    for cat, cnt in entity_cats.most_common():
        print(f"    {cat:15s}  {cnt:5d}")

    print("\n  Beziehungstypen:")
    for rtype, cnt in rel_types.most_common():
        print(f"    {rtype:25s}  {cnt:5d}")

    print(f"\n  Top-{top} häufigste Entitäten:")
    for name, cnt in entity_names.most_common(top):
        print(f"    {cnt:4d}×  {name}")

    # ── sample relationships ───────────────────────────────────────────────────
    print("\n  Beispiel-Beziehungen (erste 10 mit evidence):")
    shown = 0
    for (graph_json,) in con.execute(
            "SELECT graph_json FROM graph_triplets WHERE relationship_count > 0"):
        kg = json.loads(graph_json)
        for rel in kg.get("relationships", []):
            if shown >= 10:
                break
            print(f"    [{rel.get('relation_type','?'):20s}]  "
                  f"{rel.get('source_entity','?')!r}  →  {rel.get('target_entity','?')!r}")
            ev = rel.get("evidence", "")
            if ev:
                print(f"       ↳ {ev[:120].replace(chr(10),' ')}")
            shown += 1
        if shown >= 10:
            break

    print(f"\n{'═'*60}\n")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db",  default=str(DEMO_DB))
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    main(Path(args.db), args.top)
