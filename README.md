# KIT KlimaCrawler 🌍

**GraphRAG-System für kommunale Klimaschutzdaten.** Crawlt die Webseiten
aller **2.056 bayerischen Kommunen**, extrahiert Klimaschutz-relevante
Inhalte (Konzepte, Förderprogramme, Maßnahmen, CO₂-Ziele) und macht sie
über einen Wissensgraphen in natürlicher Sprache durchsuchbar.

```
Crawl → Segmente → Chunks → Embeddings → auditierter SQLite-Wissensgraph
     → kanonische Neo4j-Projektion
     → Retrieval / Community / Zitate (Migration geplant)
```

| Dokument | Inhalt |
|---|---|
| **[docs/PIPELINE.md](docs/PIPELINE.md)** | Alle Pipeline-Stages: Kommandos, Ein-/Ausgabetabellen, Konfiguration |
| **[docs/RUNTIME_DATABASE.md](docs/RUNTIME_DATABASE.md)** | Laufzeit-DB, sichere SQLite-Verbindungen und Snapshot-Manifeste |
| **[docs/NEO4J_PROJECTION.md](docs/NEO4J_PROJECTION.md)** | Snapshot-gebundene Neo4j-Projektion und Graphmodell |
| **[docs/pipeline_diagrams.md](docs/pipeline_diagrams.md)** | Architektur-Diagramme (Index- und Query-Pfad) |
| **[hpc/README.md](hpc/README.md)** | Rechenintensive Stages auf dem Cluster (SLURM) |

---

## ⚡ Schnellstart

**Demo (3 Kommunen, lokal, ohne GPU/Neo4j/Ollama, ~20 Minuten):**

```bash
bash demo/run_poc.sh
python demo/visualize_graph.py        # interaktiver Graph im Browser
python demo/query_demo.py "Wärmepumpe Förderung"
```

**Vollbetrieb:**

```bash
python3 -m venv wi2026 && source wi2026/bin/activate
pip install -r crawler/requirements.txt
python -m crawler.scripts.init_seed_jobs              # Jobs aus municipalities.sqlite
caffeinate -i python -m crawler.scripts.run_worker --workers 4
```

**Infrastruktur (Neo4j + Ollama) per Docker:**

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.1:8b
```

Der Neo4j-Browser läuft lokal unter `http://localhost:7474`. Die kanonische
Projektion verlangt Endpoint, Datenbank und Credentials trotzdem explizit.

---

## 📁 Repo-Struktur — was macht welche Datei?

```
crawler/core/            Crawler-Kern (von allen Stages importiert)
  config.py              ★ Single Source of Truth: Pfade, Ollama, Neo4j, Seed/Temperatur
                           — alles per KLIMA_*-Umgebungsvariablen überschreibbar
  engine.py              Crawl-Loop: Frontier, Scoring, robots.txt/Sitemaps,
                           Retry/Backoff, Autostop, Politeness pro Host
  storage.py             SQLite-Layer: Upsert per url_canonical, atomare
                           Transaktionen, seed_jobs-Claiming (verteiltes Arbeiten)
  scheduler.py           Prioritäts-Queue für die Crawl-Frontier
  canonical.py           URL-Kanonisierung (Dedup-Grundlage, LRU-gecacht)
  traps.py               Crawl-Fallen-Erkennung (Kalender, Paginierung, Galerien)
  models.py              Datenklassen (CrawlTask, FetchResult, Segment, …)
  seeds.py / seed_jobs.py  Seed-Verwaltung: CSV → seed_jobs-Tabelle, atomares Claiming
  parsers/html_parser.py Segmente + Meta + Outlinks aus HTML
  parsers/pdf_parser.py  Segmente aus PDFs (pdftotext, seitenweise)
  segmentation/chunker.py Tiktoken-Chunking 600/60 (cl100k_base, gecacht)

crawler/scripts/         Pipeline-Stages (je ein Kommando, siehe PIPELINE.md)
  run_worker.py          Stage 0 — Crawl-Worker (--workers N, --limit N)
  init_seed_jobs.py      Stage 0 — befüllt seed_jobs aus municipalities.sqlite
  chunk_documents.py     Stage 1 — Segmente → Chunks (600 Tok / 60 Overlap)
  generate_embeddings.py Stage 2a — SentenceTransformers → chunks.embedding (SQLite)
  extract_graph_ollama.py Stage 2b — LLM-Extraktion Entitäten/Beziehungen (--parallel N)
  resolve_entities.py    Stage 2c — Entity Resolution (kuratierte Aliase + Fuzzy)
  project_graph_to_neo4j.py Stage 3 — kanonische Snapshot/Run-Projektion
  export_graph_to_neo4j.py veralteter Alias; delegiert an die kanonische Projektion
  detect_communities.py  Stage 3b — deaktiviert bis zum projektionsgebundenen Lifecycle
  summarize_documents.py Stage 3c — Dokument-Zusammenfassungen (Community-Layer)
  query_agent.py         Stage 4 — Router: Local Search / Global Search / Text2Cypher
  evaluate.py            Stage 5 — Benchmarking: AutoQ/AutoE, Fixtures, eval_runs/
  analyze_topics.py      Ergänzend — BERTopic-Themenanalyse (HPC Stage 5)
  prune_db.py            Wartung — DB-Report, Boilerplate-Markierung, VACUUM
  generate_municipalities_csv.py  Stammdaten-Provenienz: erzeugt municipalities-
                           Datensatz aus Wikipedia/Wikidata (Reproduzierbarkeit)

crawler/data/
  db/municipalities.sqlite  Stammdaten aller 2.056 Kommunen (AGS, URL, Domains, …)
  seeds/municipalities.csv  CSV-Quelle für seeds.py / init_seed_jobs

demo/                    Lauffähiger PoC ohne GPU/Neo4j/Ollama
  run_poc.sh             Ende-zu-Ende: Crawl (3 Kommunen) → Chunks → Embeddings
                           → spaCy-Graph → Suche; nutzt die Produktions-Module
  init_demo_db.py        Demo-DB mit 3 Kommunen-Seeds anlegen
  extract_graph_spacy.py Offline-Graph-Extraktion (spaCy NER) — Fallback ohne LLM
  inspect_graph.py       Graph-Statistiken im Terminal
  visualize_graph.py     Interaktiver HTML-Graph (pyvis) — visuelles Validierungswerkzeug
  query_demo.py          Cosine-Retrieval direkt auf SQLite (gleiches Embedding-Modell)

docs/
  PIPELINE.md            Stage-für-Stage-Referenz (Einstiegsdokument)
  pipeline_diagrams.md   Mermaid-Diagramme der Architektur
  *.pptx + create_onepagers.py  Projekt-OnePager (Vorarbeiten & Ausschreibung) + Generator

hpc/                     SLURM-Pipeline für bwUniCluster (Stages 2–6)
tests/                   Unit-/Integrationssuite: Provenienz, Extraktion,
                           Projektion, Storage, Chunking und Sicherheitsverträge
.github/workflows/ci.yml Lint (ruff) + Tests bei jedem Push
```

---

## 🕷️ Crawl

```bash
python -m crawler.scripts.run_worker                  # alle pending Jobs
python -m crawler.scripts.run_worker --limit 10       # 10 Kommunen
python -m crawler.scripts.run_worker --workers 8      # 8 Kommunen parallel
```

- **Politeness:** `robots.txt` wird respektiert (Default), Mindestabstand pro
  Host; Sitemap-Einträge aus robots.txt werden als priorisierte Seeds genutzt
- **Robustheit:** Timeouts/429/5xx → 3 Versuche mit Backoff; Re-Fetches
  aktualisieren bestehende Dokumente (kein Duplikat)
- **Verteiltes Arbeiten:** `seed_jobs` nutzt atomares Claiming — mehrere
  Maschinen/Worker nehmen sich keine Jobs weg
- **Standby verhindern:** macOS `caffeinate -i …`, Windows PowerToys Awake

**Fortschritt beobachten:**

```bash
watch -n 30 'sqlite3 crawler/data/db/crawl.sqlite \
  "SELECT status, COUNT(*) FROM seed_jobs GROUP BY status; \
   SELECT COUNT(*) FROM documents_raw;"'
```

---

## 🧠 GraphRAG-Pipeline

Nach dem Crawl (Details: [docs/PIPELINE.md](docs/PIPELINE.md)):

```bash
python -m crawler.scripts.chunk_documents --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.generate_embeddings --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.extract_graph_ollama --db crawler/data/db/crawl.sqlite --chunk-mode --parallel 4
python -m crawler.scripts.resolve_entities --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.create_database_snapshot --db crawler/data/db/crawl.sqlite \
  --output snapshots/crawl.sqlite
# Danach Run-ID(s), Neo4j-Ziel und Ledger explizit an project_graph_to_neo4j übergeben.
# Retrieval und Community Detection sind noch nicht auf dieses Modell migriert.
```

**Evaluation** (reproduzierbar: fester Seed, Prompt-Version, Run-Historie):

```bash
python -m crawler.scripts.evaluate --questions 50 --save-questions questions.json
python -m crawler.scripts.evaluate --load questions.json     # eingefrorenes Fixture
```

**DB verkleinern** (ohne Flags nur Report — gefahrlos):

```bash
python -m crawler.scripts.prune_db
python -m crawler.scripts.prune_db --mark-boilerplate        # Menüs/Footer markieren
```

---

## ⚙️ Konfiguration

Zentrale Konfiguration in `crawler/core/config.py`, überschreibbar per
Umgebungsvariable — kein Skript hat hartkodierte Pfade:

| Variable | Default | Zweck |
|---|---|---|
| `KLIMA_DB_PATH` | `crawler/data/db/crawl.sqlite` | Arbeits-Datenbank |
| `KLIMA_OLLAMA_URL` / `KLIMA_OLLAMA_MODEL` | `localhost:11434` / `llama3.1:8b` | LLM |
| `KLIMA_NEO4J_URI` / `_USER` / `_PASSWORD` | Legacy-Pfade | Von der kanonischen Projektion nicht verwendet |
| `KLIMA_LLM_SEED` / `KLIMA_LLM_TEMPERATURE` | `42` / `0.0` | Reproduzierbarkeit |
| `KLIMA_TLS_VERIFY` | `0` | TLS-Zertifikate beim Crawlen prüfen (Default aus: viele kommunale Server haben kaputte Zertifikatsketten) |

Alle operativen CLIs lösen die Datenbank erst beim Aufruf auf. Die Rangfolge ist
`--db` → `KLIMA_DB_PATH` → Repository-Default; der aufgelöste absolute Pfad
bleibt für den gesamten Aufruf autoritativ. Fehlende operative Datenbanken
werden nicht implizit angelegt. Eine konsistente, WAL-bewusste Kopie mit
content-adressiertem Manifest lässt sich explizit erzeugen:

```bash
python -m crawler.scripts.create_database_snapshot \
  --db crawler/data/db/crawl.sqlite \
  --output snapshots/crawl-2026-08-13.sqlite
```

Details und Validierungsvertrag: [docs/RUNTIME_DATABASE.md](docs/RUNTIME_DATABASE.md).

Die kanonische Neo4j-Projektion ist für verifizierte Extraktionsartefakte
implementiert. Retrieval, Community Detection, deterministische Zitate,
Vektor-Projektion und Text2Cypher sind noch nicht an das neue Graphmodell
angebunden. Details: [docs/NEO4J_PROJECTION.md](docs/NEO4J_PROJECTION.md).

**Text2Cypher ist experimentell und standardmäßig deaktiviert.** Eine Aktivierung
erfordert `KLIMA_TEXT2CYPHER_ENABLED=1`, separat konfigurierte Credentials für
einen **datenbankseitig read-only** Neo4j-Principal sowie die explizite
Bestätigung `KLIMA_TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED=1`. Die Anwendung
akzeptiert nur einen kleinen read-only-Cypher-Unterraum und erzwingt Timeout
sowie Ergebnislimit; dies ersetzt keine Neo4j-Berechtigungen.

Worker und Query Agent führen vor der Ausführung eine nicht-mutierende
Preflight-Prüfung aus. Der Worker benötigt eine vorhandene, vollständige
`seed_jobs`-Tabelle; der Query Agent benötigt eingebettete Chunks. Text2Cypher
prüft zusätzlich die konfigurierte read-only-Neo4j-Verbindung und vorhandene
`Entity`-Knoten. Fehler verlangen ausdrücklich den jeweiligen Vorgänger-Schritt
und erzeugen weder Ersatzdatenbanken noch partielle Stage-Ergebnisse.
Die Worker-Schema-Prüfung liest die SQLite-Datei direkt, damit ein Datenbank-
Artefakt im WAL-Modus keine `-wal`- oder `-shm`-Dateien durch einen bloßen
Preflight erhält.

---

## 🧪 Tests & CI

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest --collect-only
.venv/bin/python -m pytest
```

`requirements-dev.txt` enthält den schlanken, vollständigen Abhängigkeitssatz
für Tests und CLI-Importprüfungen, aber nicht den GPU-/BERTopic-Laufzeitstack.
GitHub Actions installiert denselben Satz und führt bei jedem Push Collection,
ruff und die vollständige Test-Suite unter Python 3.11 aus.

---

## 🛠 Voraussetzungen & Fehlerquellen

- Python 3.10+, stabile Internetverbindung, `pdftotext` im Systempfad
- Große Crawls: Standby des Rechners verhindern (sonst bricht der Lauf ab)
- Datenbank liegt unter `crawler/data/db/` — oder per `KLIMA_DB_PATH` umbiegen
- Ollama nötig für Graph-Extraktion/Query/Evaluation; die Demo läuft ohne

---

## 📄 Lizenz

[MIT](LICENSE)
