# HPC-Pipeline — KIT KlimaCrawler GraphRAG

Der Crawl läuft **lokal** (kein Netzwerk-Overhead auf dem Cluster, keine
Internet-Freigabe nötig). Die fertige SQLite-DB wird per `transfer_db.sh`
übertragen. Der HPC übernimmt dann die rechenintensiven Stages 2–6.

## Schnellstart

```bash
# Lokal: crawlen
python -m crawler.scripts.run_worker

# Lokal → Cluster: DB übertragen + Integrität prüfen
bash hpc/transfer_db.sh --host bwunicluster.scc.kit.edu --user st_abc1234

# Auf dem Cluster: Umgebung einrichten (einmalig)
bash hpc/00_setup_env.sh

# Auf dem Cluster: Pipeline starten
bash hpc/submit_pipeline.sh

# Fortschritt überwachen
squeue -u $USER
tail -f logs/chunk_embed_<JOBID>.out
```

## Pipeline-Übersicht

| Stage | Wo | Skript | Ressourcen | Dauer (est.) |
|-------|----|--------|------------|--------------|
| 0 Setup | Cluster (Login) | `00_setup_env.sh` | — | einmalig |
| 1 Crawl | **lokal** | `run_worker.py` | CPU + Internet | Tage |
| — Transfer | lokal→Cluster | `transfer_db.sh` | rsync | Minuten |
| 2 Chunk+Embed | Cluster (GPU) | `02_chunk_embed.sbatch` | 1 GPU, 32G, 4h | 2–4 h |
| 3 Graph-Extraktion | Cluster (GPU) | `03_extract_graph.sbatch` | 1 GPU, 48G, 24h | 4–12 h (`--parallel 4`) |
| 4 Neo4j Projection | **unsupported** | `04_neo4j_export.sbatch` | exits safely | — |
| 5 Community Detection | **unsupported with canonical model** | `05_community_detect.sbatch` | — | — |
| 6 Evaluation | Cluster (GPU) | `06_evaluate.sbatch` | 1 GPU, 32G, 4h | 2–4 h |

**Gesamt auf dem Cluster:** ~20–40 Stunden Maschinen-Zeit

## Voraussetzungen

### Cluster
- SLURM-Scheduler mit `sbatch`, `squeue`
- Partitionen `cpu` und `gpu` verfügbar (Namen ggf. in `.sbatch`-Dateien anpassen)
- Compute-Nodes mit ausgehendem HTTP/S-Zugang (für Crawl)
- Conda/Miniconda verfügbar (`module load conda` o.ä.)
- Mindestens 16 GB VRAM für `qwen2.5:14b` (oder `llama3.1:8b` mit 8 GB)

### Neo4j

Die HPC-Stufen 4–6 sind noch nicht auf die kanonische Snapshot/Run-Projektion
migriert und dürfen nicht als vollständige Pipeline ausgeführt werden. Das
Stage-4-Skript bricht deshalb absichtlich mit einer Erklärung ab. Der lokale
Projektionsvertrag steht in `docs/NEO4J_PROJECTION.md`; HPC-Enablement ist
separat geplant.

Die folgenden Optionen dokumentieren nur die historische Infrastruktur und
sind derzeit kein ausführbarer Projektionspfad:

**Option A (empfohlen):** Neo4j läuft auf einem dedizierten Server/VM
```bash
export KLIMA_NEO4J_URI="bolt://neo4j-server.cluster.intern:7687"
```

**Option B:** Neo4j als Apptainer/Singularity-Container auf dem Compute-Node
```bash
# Kommentare in 04_neo4j_export.sbatch einkommentieren
# GDS-Plugin .jar nach data/neo4j/plugins/ kopieren
```

**Option C:** SQLite-only (kein Neo4j)
```bash
export NEO4J_SKIP=1   # Community Detection läuft dann nur via BERTopic
```

## Partitions- und Ressourcen-Anpassung

Jedes `.sbatch`-Skript enthält am Anfang die SLURM-Direktiven.
Häufige Anpassungen:

```bash
# Partition umbenennen (z.B. "a100" statt "gpu"):
sed -i 's/--partition=gpu/--partition=a100/g' hpc/*.sbatch

# LLM-Modell global setzen (sparsameres Modell für kleinen VRAM):
export KLIMA_OLLAMA_MODEL="llama3.1:8b"

# Nur bestimmte Stages laufen lassen:
bash hpc/submit_pipeline.sh --from 3        # ab Graph-Extraktion
bash hpc/submit_pipeline.sh --skip-crawl    # ohne Crawl (Daten vorhanden)
```

## Ergebnisse

Nach erfolgreichem Durchlauf:

| Artefakt | Pfad |
|----------|------|
| Rohdaten (SQLite) | `crawler/data/db/crawl.sqlite` |
| Evaluation-Report | `results/eval_<timestamp>.json` |
| Logs | `logs/*.out` / `logs/*.err` |
| Neo4j Graph | Bolt-Server (persistiert) |

## Abfrage nach der Pipeline

Eine interaktive UI ist derzeit nicht implementiert. Das kanonische CLI
verlangt eine explizite Snapshot-Datei und deren Manifest. Graph-/Hybrid-Modi
benötigen außerdem die exakte Projection-ID und einen separaten read-only
Neo4j-Principal. Siehe [../docs/RETRIEVAL.md](../docs/RETRIEVAL.md). Die Befehle
für konkrete HPC-Snapshot- und Servicepfade sind umgebungsspezifisch und hier
nicht verifiziert.
