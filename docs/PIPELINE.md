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

Zentrale Konfiguration: `crawler/core/config.py`. Jede operative CLI löst ihre
SQLite-Datenbank beim Aufruf auf; es gibt keine importzeitlich gebundene
Arbeitsdatenbank.

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
| `KLIMA_TLS_VERIFY` | `0` | TLS-Verifikation beim Crawlen (Default aus — kaputte kommunale Zertifikate) |

Neo4j und Ollama lassen sich per `docker compose up -d` starten
(GDS + APOC vorinstalliert, Zugangsdaten passen auf die Defaults).

Beispiel — komplette Pipeline gegen eine andere DB:

```bash
export KLIMA_DB_PATH=demo/demo.sqlite
python -m crawler.scripts.chunk_documents
```

Die Präzedenz ist für alle Stages identisch: ein explizites `--db` ist
autoritativ, danach folgt `KLIMA_DB_PATH`, danach der absolute
Repository-Default. Nach der Auflösung wird derselbe unveränderliche Pfad durch
den gesamten Stage-Aufruf gereicht. Operative Lese- und Schreibverbindungen
verlangen eine vorhandene Datei; nur Initialisierung, Test-Fixtures und
Snapshot-Export dürfen Datenbanken explizit anlegen. Siehe
[RUNTIME_DATABASE.md](RUNTIME_DATABASE.md).

Für den Crawl-Worker ist `--db` die autoritative Auswahl für die gesamte
Worker-Ausführung (Job-Claiming, Heartbeats und Crawl-Speicher). Ohne `--db`
wird `KLIMA_DB_PATH` erst beim Start des Workers aufgelöst. Die ausgewählte
Datei muss bereits existieren; zuvor `init_seed_jobs --db <pfad>` ausführen.
Vor dem Start prüft der Worker die `seed_jobs`-Schema-Voraussetzungen
schreibgeschützt und bricht bei Abweichungen explizit ab. Diese Schema-Prüfung
öffnet die Live-Datenbank über SQLite in `mode=ro` und `query_only`. Dadurch
prüft sie den tatsächlich committed Zustand einschließlich WAL statt nur die
möglicherweise veraltete Hauptdatei und erzeugt keine fehlende Ersatzdatenbank.

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

Verteiltes Crawlen über mehrere Maschinen: `seed_jobs` nutzt atomares
Claiming — einfach auf jeder Maschine `run_worker` gegen eine DB-Kopie
starten und die Ergebnisse anschließend zusammenführen.

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
Speichert ausschließlich in SQLite als vollständiges versioniertes Artefakt.
Provider, Modell/Revision, Dimension, Preprocessing-ID, Datenbankidentität,
Korpus-Fingerprint und Chunk-Content-Hashes werden fest gebunden. Partielle
Artefakte sind nicht retrievalfähig. Eine Vektor-Projektion ist im
kanonischen Neo4j-Modell nicht implementiert.

```bash
python -m crawler.scripts.generate_embeddings
python -m crawler.scripts.generate_embeddings --model intfloat/multilingual-e5-large --batch 64
```

| Liest | Schreibt |
|---|---|
| `chunks` (vollständiger Korpus) | `embedding_artifacts`, `chunk_embeddings` |

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
python -m crawler.scripts.extract_graph_ollama --chunk-mode --resume-run RUN_ID
```

| Liest | Schreibt |
|---|---|
| `chunks` oder `segments` | `graph_triplets`, immutable Evidenz, Run-Input-Ledger und Source-Outcomes |

Der Ollama-Pfad ist der kanonische Graph-Writer. Vor dem ersten Provider-Aufruf
legt er den Run und die deterministisch sortierte Input-Menge in
`extraction_run_sources` an. Segment-/Chunk-Hierarchie, Dokument,
Kommunalzuordnung, URL und Text werden vor jedem LLM-Aufruf validiert.

Ein erfolgreicher Source-Schreibvorgang persistiert Evidenz, Graph-Artefakt,
`inserted`-Outcome und neu abgeleitete Zähler atomar in einem Savepoint.
`skipped`, `rejected` und `failed` sind explizite Outcomes; es gibt kein
`INSERT OR IGNORE` im kanonischen Graph-Writer. Die Lifecycle-API committet
keine Transaktion des Aufrufers.

Run-Zustände sind `running`, `completed`, `completed_with_errors`, `failed`,
`cancelled` und `interrupted`. Der Terminalzustand und alle Zähler werden aus
persistierten Outcomes abgeleitet. Auch eine leere Auswahl erzeugt einen
deterministischen `completed`-Run. `KeyboardInterrupt` wird als `interrupted`
persistiert; `--resume-run` setzt denselben Run fort und überspringt Sources mit
bereits vorhandenem Outcome. Ein noch `running` markierter Run ist erst nach
der mit `--stale-after` konfigurierten Heartbeat-Frist resumierbar.

Neue Experimente schließen historische Sources nicht aus. Mehrere Runs über
denselben Source bewahren ihre Artefakte; Modell, Prompt, Parameter, Seed und
Snapshot dürfen variieren. Identische Evidenz wird content-addressed
wiederverwendet, geänderter Text erzeugt einen neuen unveränderlichen Snapshot.
Ein manifestierter Snapshot wird als `database_snapshot_id` gespeichert. Bei
einer unmanifestierten, schreibbaren DB bleibt dieses Feld bewusst `NULL`; die
Live-State-ID wird separat erfasst und nicht als wissenschaftlicher Snapshot
ausgegeben.

Legacy-Zeilen ohne vollständige Angaben bleiben erhalten, werden aber nicht als
zitierfähige Evidenz behandelt.

**Experimentelle Demo:**
- `demo/extract_graph_spacy.py` — Offline-Heuristik (spaCy NER + Keywords) für
  Demos ohne GPU/Ollama. Sie schreibt ausschließlich `demo_graph_triplets` und
  erzeugt keine kanonischen Runs, Outcomes, Evidenz oder Produktionsartefakte.

**Bekannte Grenzen:** laufende Ollama-HTTP-Aufrufe werden nicht remote
abgebrochen, sondern bleiben durch den Request-Timeout begrenzt. Die
Lifecycle-Migration ist fixture-validiert; eine Anwendung auf die
Produktionsdatenbank ist nicht Teil dieses Stands. Neo4j-Projektion, Retrieval
und Citation-Rendering werden durch diesen Stage-2b-Stand nicht als bereit
ausgewiesen.

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

## Stage 3 — kanonische Neo4j-Projektion

**Skript:** `crawler/scripts/project_graph_to_neo4j.py`

Projiziert ausschließlich verifizierte Artefakte ausgewählter, abgeschlossener
Extraktions-Runs aus einem validierten SQLite-Snapshot. SQLite bleibt Source of
Truth. Explizite `Evidence`-, `EntityMention`- und `Assertion`-Knoten bewahren
die Provenienz mehrerer Aussagen zwischen demselben Entitätspaar. Ein lokaler
SQLite-Ledger protokolliert Batches, Fehler und Abschlusszustand.

```bash
export GRAPHRAG_NEO4J_PROJECTION_PASSWORD='replace-with-a-secret'
python -m crawler.scripts.project_graph_to_neo4j \
  --snapshot-manifest snapshots/crawl.sqlite.manifest.json \
  --run-id RUN_ID \
  --ledger generated/projections/neo4j-ledger.sqlite \
  --neo4j-uri neo4j+s://graph.example.test:7687 \
  --neo4j-database neo4j \
  --neo4j-user graphrag_projection_writer \
  --neo4j-password-env GRAPHRAG_NEO4J_PROJECTION_PASSWORD
```

Vollständiger Vertrag: [NEO4J_PROJECTION.md](NEO4J_PROJECTION.md). Der alte
`export_graph_to_neo4j`-Name ist nur noch ein veralteter CLI-Alias. Die
Embedding-Stage schreibt nicht mehr parallel nach Neo4j.

---

## Stage 3b — Community Detection

**Skript:** `crawler/scripts/detect_communities.py`

**Status: unsupported.** Die frühere Implementierung schrieb direkt in das
veraltete Neo4j-Modell. Sie wurde entfernt; das Kommando bricht vor jedem
SQLite-, Neo4j- oder LLM-Zugriff mit einer Erklärung ab. Community Reports und
Global Search bleiben geplant, bis eine eigene projektionsgebundene Lifecycle-
und Provenienzimplementierung vorliegt.

---

## Stage 4 — Canonical Retrieval

**Skript:** `crawler/scripts/query.py`

Status: **implemented** for explicit manifested snapshots.

| Modus | Mechanik | Status |
|---|---|---|
| `semantic` | vollständiges, lineage-validiertes Chunk-Artefakt; Cosine-Rang | implemented |
| `graph` | begrenztes anwendungseigenes 1-Hop-Cypher; SQLite-Auflösung | implemented |
| `hybrid` | deterministische Reciprocal Rank Fusion, Deduplikation per `evidence_id` | implemented |
| global/community | kein genehmigter Report-/Projektions-Lifecycle | deferred |
| Text2Cypher | keine Ausführung nutzergenerierter Queries | unsupported |

Jede Anfrage verlangt `--db` und `--manifest`; beide müssen dieselbe validierte
Snapshot-Datei bezeichnen. Graph/Hybrid verlangt zusätzlich die exakte
Projection-ID und einen explizit bestätigten read-only Neo4j-Principal. Fragen
werden ausschließlich als Parameter/Werte behandelt und werden nie SQL oder
Cypher.

```bash
python -m crawler.scripts.query \
  --db snapshots/work.sqlite \
  --manifest snapshots/work.sqlite.manifest.json \
  --mode semantic --top-k 8 --output markdown \
  "Welche Kommunen beschreiben Solaranlagen?"
```

Die Ausgabe enthält getypte Provenienz und deterministische Markdown-/JSON-
Zitate; sie erzeugt noch keine LLM-Antwort. Der vollständige Architektur-,
Sicherheits- und Limitationsvertrag steht in [RETRIEVAL.md](RETRIEVAL.md).
`query_agent.py` ist nur ein veralteter CLI-Alias; seine frühere Python-API
bricht explizit ab.

---

## Stage 5 — Evaluation

**Skript:** `crawler/scripts/evaluate.py`

**Status: unsupported für das kanonische Retrieval.** Das vorhandene
Benchmarking-Gerüst stammt aus dem deaktivierten Query-Agent-Pfad. Es darf erst
wieder ausgeführt werden, wenn Fragen, Retrieval-Trace, Citation Registry,
Grounding-Report und LLM-Run-Metadaten an den neuen Vertrag gebunden sind. Die
folgenden Beschreibungen dokumentieren den historischen Entwurf und sind keine
aktuell verifizierten Befehle.

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

Die Demo nutzt Pipeline-Module gegen `demo/demo.sqlite` (via `KLIMA_DB_PATH`),
hält spaCy-Graphartefakte aber bewusst in `demo_graph_triplets` getrennt vom
kanonischen Ollama-Writer.

---

## Reproduzierbarer SQLite-Snapshot

```bash
python -m crawler.scripts.create_database_snapshot \
  --db crawler/data/db/crawl.sqlite \
  --output snapshots/crawl.sqlite
```

Der Export nutzt die SQLite-Backup-API und erfasst damit committed WAL-Inhalte,
ohne die Quelldatenbank zu checkpointen. Das Manifest enthält Prüfsumme, Größe,
SQLite-/Provenance-Schemastand und nur explizit vorhandene Stage-Abschlüsse. Der
deterministische `snapshot_id` kann später Runs, Caches, Exporte und Berichte an
genau diesen Datenstand binden.

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
