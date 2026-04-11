"""
init_demo_db.py  —  Legt eine frische Demo-Datenbank mit 3 Kommunen an.

Zielkommunen: Ingolstadt, Regensburg, Augsburg
(Kreisfreie Städte mit bekannt guten Klimaschutz-Webseiten)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# ── Bitte anpassen ────────────────────────────────────────────────────────────
DEMO_DB   = Path("demo/demo.sqlite")
MUNI_CSV  = Path("crawler/data/seeds/municipalities.csv")
TARGET_MUNIS = {"Ingolstadt", "Regensburg", "Augsburg"}
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent.parent))

import csv
from crawler.core.seeds import upsert_seed_jobs

DEMO_DB.parent.mkdir(parents=True, exist_ok=True)

# Lese nur Zielkommunen aus der CSV
seeds = []
allowed = {}
with open(MUNI_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["name"] in TARGET_MUNIS:
            muni_id = row["ags"]
            url     = row["homepage_url"].strip()
            seeds.append((muni_id, url))
            allowed[muni_id] = set(row.get("allowed_domains", "").split("|"))
            print(f"  ✓  {row['name']:15s}  {url}")

if not seeds:
    print("FEHLER: Keine Kommunen in CSV gefunden.")
    sys.exit(1)

n = upsert_seed_jobs(seeds, crawl_db_path=DEMO_DB)
print(f"\nDemo-DB erstellt: {DEMO_DB}  ({n} Seed-Jobs für {len(seeds)} Kommunen)")
