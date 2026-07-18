# KlimaCrawler — GraphRAG-Pipeline

Dieses Dokument beschreibt alle Stages der Pipeline: was sie tun, welche
Tabellen sie lesen und schreiben, und wie man sie startet. Es ist das
Einstiegsdokument für neue Teammitglieder und Studierende.

```
Crawl → Chunking → Embeddings → Graph-Extraktion → Entity Resolution
     → Neo4j-Export → Community Detection → Query Agent → Evaluation
```

---

## Konfiguration

Zentrale Konfiguration: `crawler/core/config.py`. Alle Module importieren von
dort — es gibt keine hartkodierten Pfade in den Skripten.

Überschreiben per Umgebungsvariable:

| Variable | Default | Bedeutung |
|---|---|---|
| `KLIMA_DB_PATH` | `crawler/data/db/crawl.sqlite` | SQLite-Arbeitsdatenbank |
| `KLIMA_OLLAMA_URL` | `http://localhost:11434` | Ollama-Server |
| `KLIMA_OLLAMA_MODEL` | `llama3.1:8b` | Extraktions-/Query-LLM |
| `KLIMA_NEO4J_URI` | `bolt://localhost:7687` | Neo4j |
| `KLIMA_NEO4J_USER` / `KLIMA_NEO4J_PASSWORD` | `neo4j` / `password` | Neo4j Login |
| `KLIMA_LLM_SEED` | `42` | Seed für alle LLM-Calls (Reproduzierbarkeit) |
| `KLIMA_LLM_TEMPERATURE` | `0.0` | Einheitliche Temperatur aller LLM-Calls |

Beispiel — komplette Pipeline gegen eine andere DB:

```bash
export KLIMA_DB_PATH=demo/demo.sqlite
python -m crawler.scripts.chunk_documents
```

---

## Stage 0 — Crawl

**Skript:** `crawler/scripts/run_worker.py` · **Engine:** `crawler/core/engine.py`

Crawlt kommunale Webseiten (Seeds aus `municipalities.sqlite`, 2.056 bayerische
Kommunen). Relevanz-Steuerung über positive/negative Keywords und Impact-Scoring.
Läuft **lokal** (nicht auf dem HPC — Compute-Nodes brauchen kein Internet).

```bash
python -m crawler.scripts.run_worker              # alle pending Jobs
python -m crawler.scripts.run_worker --limit 10   # nur 10 Kommunen
python -m crawler.scripts.run_worker --workers 8  # 8 Kommunen parallel
```

`robots.txt` wird respektiert (Default) und Sitemap-Einträge daraus werden
als priorisierte Seeds genutzt. Transiente Fehler (Timeout, 429/5xx) werden
mit Backoff wiederholt; Re-Fetches aktualisieren bestehende Dokumente
(Upsert per `url_canonical`) statt Duplikate anzulegen.

| Schreibt | Inhalt |
|---|---|
| `documents_raw` | HTML/PDF-Dokumente, kanonische URLs, Hashes |
| `segments` | Extrahierte Textabschnitte mit `impact_score`, `heading_context` |
| `seed_jobs` | Job-Status pro Kommune (`pending` → `done`) |

Verteiltes Crawlen: `split_workload.py` teilt pending Jobs in 20 Pakete
(`distribution_packages/pkg_XX/`), Koordination über `Tracker.md`.

---

## Stage 1 — Chunking

**Skript:** `crawler/scripts/chunk_documents.py`

Zerlegt Segmente tokengenau (tiktoken, cl100k_base) in **600-Token-Chunks mit
60 Token Overlap**. Kurze Segmente bleiben als Ein-Chunk erhalten.

```bash
python -m crawler.scripts.chunk_documents
python -m crawler.scripts.chunk_documents --min-score 10 --limit 50000
```

| Liest | Schreibt |
|---|---|
| `segments`, `documents_raw` | `chunks` (chunk_id, text, token_count, impact_score, …) |

---

## Stage 2a — Embeddings

**Skript:** `crawler/scripts/generate_embeddings.py`

Berechnet Vektor-Embeddings lokal (keine API-Calls).
**Modell:** `paraphrase-multilingual-MiniLM-L12-v2` (384 Dim., deutschtauglich).
Optional Push nach Neo4j inkl. nativem Vektor-Index (`--neo4j`).

```bash
python -m crawler.scripts.generate_embeddings
python -m crawler.scripts.generate_embeddings --model intfloat/multilingual-e5-large --batch 64
```

| Liest | Schreibt |
|---|---|
| `chunks` (embedding IS NULL) | `chunks.embedding` (float32-BLOB), optional Neo4j `Chunk`-Nodes |

---

## Stage 2b — Graph-Extraktion

**Skript:** `crawler/scripts/extract_graph_ollama.py`

Extrahiert Entitäten und Beziehungen per LLM (Ollama, Pydantic-validiertes
JSON-Schema). Entitätstypen: Akteur, Infrastruktur, Förderprogramm, Maßnahme,
Konzept/Ziel, Dokument. Kategorien: Mobilität, Wärme, Strom, Finanzen,
Governance, Sonstiges.

```bash
python -m crawler.scripts.extract_graph_ollama --chunk-mode
python -m crawler.scripts.extract_graph_ollama --chunk-mode --parallel 4   # HPC
```

| Liest | Schreibt |
|---|---|
| `chunks` | `graph_triplets` (graph_json, entity_count, relationship_count) |

**Alternativen:**
- `demo/extract_graph_spacy.py` — Offline-Fallback (spaCy NER + Keywords), gleiche Zieltabelle, deutlich geringere Qualität. Nur für Demos ohne GPU/Ollama.
- `legacy_extract_graph_openai.py` — Alte OpenAI-Version, nicht Teil der Pipeline.

---

## Stage 2c — Entity Resolution

**Skript:** `crawler/scripts/resolve_entities.py`

Normalisiert Entitätsnamen aus `graph_triplets`, erkennt Duplikate
(SequenceMatcher-Ähnlichkeit) und schreibt eine kanonische Zuordnung. Der
Neo4j-Export nutzt sie, um Varianten-Knoten zu einer Entität zu verschmelzen.

```bash
python -m crawler.scripts.resolve_entities --min-freq 2 --similarity 0.85
```

| Liest | Schreibt |
|---|---|
| `graph_triplets` | `entity_canonical` |

---

## Stage 3 — Neo4j-Export

**Skript:** `crawler/scripts/export_graph_to_neo4j.py`

Überträgt den Graphen nach Neo4j: `Municipality`-, `Document`-, `Chunk`-,
`Entity`-Nodes samt Beziehungen. Merge-Key: normalisierter Name + Typ +
Kategorie (fragmentierungsarm).

```bash
python -m crawler.scripts.export_graph_to_neo4j
```

---

## Stage 3b — Community Detection

**Skript:** `crawler/scripts/detect_communities.py`

1. Leiden-Algorithmus (Neo4j GDS) findet thematische Cluster im Entity-Graph.
2. Pro Community generiert ein LLM einen **Community Report** (Titel,
   Zusammenfassung, Importance, Key Claims).
3. Reports landen in Neo4j (`CommunityReport`) und SQLite.

Community Reports sind das Rückgrat der **Global Search** (Map-Reduce über
Reports → landesweite/aggregierende Antworten, „Sensemaking").

```bash
python -m crawler.scripts.detect_communities
python -m crawler.scripts.detect_communities --algorithm louvain --min-size 3
python -m crawler.scripts.detect_communities --parallel 4   # Reports parallel
```

**Voraussetzungen:** Neo4j 5.x + GDS-Plugin ≥ 2.6, Stage 3 abgeschlossen, Ollama läuft.

---

## Stage 4 — Query Agent

**Skript:** `crawler/scripts/query_agent.py`

Retriever-Router: Ein LLM entscheidet pro Anfrage, welches Werkzeug passt —

| Modus | Für | Mechanik |
|---|---|---|
| **Local Search** | Spezifische Fragen zu einer Kommune | Vektorsuche + Graph-Nachbarschaft |
| **Global Search** | Aggregierende/vergleichende Fragen | Map-Reduce über Community Reports |
| **Text2Cypher** | Exakte strukturelle Abfragen | LLM generiert Cypher |

Ein Answer Critic prüft die Antwort auf Vollständigkeit und stößt bei Bedarf
eine neue Suchrunde an (max. `MAX_CRITIC_LOOPS = 3`).

```bash
python -m crawler.scripts.query_agent "Welche Kommunen fördern Photovoltaik?"
python -m crawler.scripts.query_agent --llm openai "..."   # Cloud-Fallback
```

---

## Stage 5 — Evaluation

**Skript:** `crawler/scripts/evaluate.py`

Benchmarking-Gerüst (Ausbau ist Kern der Bachelorarbeit):

- **AutoQ** — generiert Testfragen über Komplexitätsstufen (lokal → global)
- **AutoE** — LLM-as-a-Judge Head-to-Head-Vergleich zweier Systeme
- **RAGAS** — Context Recall, Faithfulness, Answer Correctness *(geplant — Paket in requirements.txt aktivieren)*
- **Claimify** — Antworten in atomare, prüfbare Claims zerlegen

```bash
# Fragen generieren + als Fixture einfrieren (kuratieren, dann einchecken!)
python -m crawler.scripts.evaluate --questions 50 --save-questions questions.json

# Reproduzierbarer Lauf gegen eingefrorenes Fixture
python -m crawler.scripts.evaluate --load questions.json

# Fragen mit ANDEREM Modell generieren (vermeidet Zirkularitäts-Bias)
python -m crawler.scripts.evaluate --autoq-model qwen2.5:14b

# A/B-Vergleich mit Positions-Swap-Kontrolle
python -m crawler.scripts.evaluate --compare system_a.json system_b.json
```

Reports landen unter `eval_runs/eval_report_<run_id>.json` (nie überschrieben)
und enthalten Seed, Temperatur, Prompt-Version, Modell und Git-Commit.

---

## Hilfs-Skripte (nicht Teil der Kern-Pipeline)

| Skript | Zweck | Hinweis |
|---|---|---|
| `analyze_topics.py` | BERTopic-Themenanalyse über Segmente | Output nach `topic_out/` |
| `analyze_finances.py` | Geldbeträge/Förder-Keywords per Regex finden | Reporting-Tool |
| `summarize_documents.py` | Dokument-Zusammenfassungen per LLM | Ollama (Default), `--llm openai` als Cloud-Fallback |
| `generate_municipalities_csv.py` | Kommunen-Stammdaten aus Wikipedia/Wikidata | erzeugt `municipalities.sqlite` |
| `prune_db.py` | Arbeits-DB verkleinern (Report → gezieltes Löschen → VACUUM) | `--mark-boilerplate` markiert seitenübergreifend identische Segmente; siehe `--help` |

---

## Demo (lokal, ohne GPU/Neo4j/Ollama)

```bash
bash demo/run_poc.sh                     # Crawl 3 Kommunen → Chunks → Embeddings → spaCy-Graph → Suche
python demo/inspect_graph.py             # Graph-Statistiken
python demo/visualize_graph.py           # interaktives HTML (pyvis)
python demo/query_demo.py "Wärmepumpe Förderung"
```

Die Demo nutzt dieselben Pipeline-Module gegen `demo/demo.sqlite`
(via `KLIMA_DB_PATH`).

---

## HPC (bwUniCluster)

Rechenintensive Stages (2–6) laufen per SLURM, siehe `hpc/README.md`:

```bash
bash hpc/transfer_db.sh --host bwunicluster.scc.kit.edu --user st_abc1234
bash hpc/00_setup_env.sh        # einmalig
bash hpc/submit_pipeline.sh     # Stages 2-6 mit Abhängigkeiten einreihen
```

| SQLite-Tabelle | Erzeugt von | Gelesen von |
|---|---|---|
| `documents_raw`, `segments`, `seed_jobs` | Stage 0 (Crawl) | Stage 1 |
| `chunks` (+ `embedding`) | Stage 1 / 2a | Stage 2b, 4 |
| `graph_triplets` | Stage 2b | Stage 2c, 3 |
| `entity_canonical` | Stage 2c | Stage 3 |
| `document_summaries` | summarize_documents | Retrieval (optional) |
