# HPC-Pipeline — KIT KlimaCrawler GraphRAG

SLURM-basierte Ausführung der kompletten 6-stufigen Pipeline auf einem
Hochleistungsrechner.

## Schnellstart

```bash
# 1. Einmalig: Umgebung einrichten (auf dem Login-Node)
bash hpc/00_setup_env.sh

# 2. Alle Stages als verkettete SLURM-Jobs abschicken
bash hpc/submit_pipeline.sh

# 3. Fortschritt überwachen
squeue -u $USER
tail -f logs/crawl_<JOBID>_0.out
```

## Pipeline-Übersicht

| Stage | Skript | Ressourcen | Dauer (est.) |
|-------|--------|------------|--------------|
| 0 Setup | `00_setup_env.sh` | Login-Node | 1× |
| 1 Crawl | `01_crawl.sbatch` | 4× CPU, 8G, 72h | 2–3 Tage |
| 2 Chunk+Embed | `02_chunk_embed.sbatch` | 1 GPU, 32G, 4h | 2–4 h |
| 3 Graph-Extraktion | `03_extract_graph.sbatch` | 1 GPU, 48G, 24h | 12–24 h |
| 4 Neo4j Export | `04_neo4j_export.sbatch` | CPU, 16G, 4h | 1–2 h |
| 5 Community Detection | `05_community_detect.sbatch` | 1 GPU, 32G, 8h | 4–8 h |
| 6 Evaluation | `06_evaluate.sbatch` | 1 GPU, 32G, 4h | 2–4 h |

**Gesamt:** ~4–6 Tage Maschinen-Zeit (kalendarisch: ~3 Tage, da Stages parallel)

## Voraussetzungen

### Cluster
- SLURM-Scheduler mit `sbatch`, `squeue`
- Partitionen `cpu` und `gpu` verfügbar (Namen ggf. in `.sbatch`-Dateien anpassen)
- Compute-Nodes mit ausgehendem HTTP/S-Zugang (für Crawl)
- Conda/Miniconda verfügbar (`module load conda` o.ä.)
- Mindestens 16 GB VRAM für `qwen2.5:14b` (oder `llama3.1:8b` mit 8 GB)

### Neo4j
Drei Optionen (in `04_neo4j_export.sbatch` dokumentiert):

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

## Interaktive Abfrage (nach Pipeline)

```bash
# Auf einem GPU-Node (interaktiv):
srun --partition=gpu --gres=gpu:1 --mem=16G --pty bash
conda activate klima
python -m crawler.scripts.query_agent --interactive
```
