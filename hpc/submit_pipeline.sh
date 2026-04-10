#!/bin/bash
# =============================================================================
# submit_pipeline.sh  –  Startet die komplette GraphRAG-Pipeline als verkettete
#                        SLURM-Jobs (jeder wartet auf den Abschluss des vorherigen)
#
# Verwendung:
#   bash hpc/submit_pipeline.sh [--skip-crawl] [--from STAGE]
#
# Optionen:
#   --skip-crawl     Crawl überspringen (Daten bereits vorhanden)
#   --from 2         Pipeline ab Stage 2 starten (1=crawl, 2=chunk, 3=graph, ...)
#
# Voraussetzung:
#   bash hpc/00_setup_env.sh   muss bereits ausgeführt worden sein
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
cd "$PROJEKT_DIR"
mkdir -p logs

# ── Argumente parsen ──────────────────────────────────────────────────────────
START_FROM=1
SKIP_CRAWL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-crawl) SKIP_CRAWL=1; shift ;;
        --from)       START_FROM="$2"; shift 2 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

echo "════════════════════════════════════════════════════════"
echo " KIT KlimaCrawler – SLURM Pipeline Submission"
echo " Starte ab Stage: ${START_FROM}"
echo " Projektverzeichnis: ${PROJEKT_DIR}"
echo "════════════════════════════════════════════════════════"

PREV_JOB=""   # Job-ID des vorherigen Schritts (für --dependency)

dep_flag() {
    # Gibt --dependency=afterok:<id> zurück, wenn ein Vorgänger existiert
    if [[ -n "${PREV_JOB}" ]]; then
        echo "--dependency=afterok:${PREV_JOB}"
    fi
}

submit() {
    local stage="$1"
    local script="$2"
    local label="$3"

    if [[ "${stage}" -lt "${START_FROM}" ]]; then
        echo "  ⏭  Stage ${stage} übersprungen (--from ${START_FROM})"
        return
    fi

    local dep
    dep=$(dep_flag)
    local job_id
    # shellcheck disable=SC2086
    job_id=$(sbatch ${dep} "${script}" | awk '{print $NF}')
    PREV_JOB="${job_id}"
    echo "  ✓  Stage ${stage} | ${label}"
    echo "         Job-ID: ${job_id}  Skript: ${script}"
}

# ── Stage 0: Crawl (Job Array mit 4 Workern) ─────────────────────────────────
if [[ "${SKIP_CRAWL}" -eq 0 && "${START_FROM}" -le 1 ]]; then
    CRAWL_JOB=$(sbatch hpc/01_crawl.sbatch | awk '{print $NF}')
    PREV_JOB="${CRAWL_JOB}"
    echo "  ✓  Stage 1 | Crawl (4-Worker Array)"
    echo "         Job-ID: ${CRAWL_JOB}  (Array: ${CRAWL_JOB}_0 … ${CRAWL_JOB}_3)"
else
    echo "  ⏭  Stage 1 – Crawl übersprungen"
fi

# ── Stage 2: Chunking + Embeddings ───────────────────────────────────────────
submit 2 hpc/02_chunk_embed.sbatch       "Chunking + Embeddings + Entity-Resolution"

# ── Stage 3: Graph-Extraktion via Ollama ─────────────────────────────────────
submit 3 hpc/03_extract_graph.sbatch     "Graph-Extraktion (Ollama + Gleaning)"

# ── Stage 4: Neo4j-Export ────────────────────────────────────────────────────
submit 4 hpc/04_neo4j_export.sbatch      "Neo4j Export (Triplets + Embeddings)"

# ── Stage 5: Community Detection ─────────────────────────────────────────────
submit 5 hpc/05_community_detect.sbatch  "Community Detection (Leiden + LLM Reports)"

# ── Stage 6: Evaluation ──────────────────────────────────────────────────────
submit 6 hpc/06_evaluate.sbatch          "Evaluation (AutoQ + RAGAS + AutoE)"

echo ""
echo "════════════════════════════════════════════════════════"
echo " Alle Jobs submitted. Fortschritt überwachen:"
echo "   squeue -u \$USER"
echo "   tail -f logs/crawl_<JOBID>_0.out"
echo ""
echo " Geschätzte Gesamtdauer:"
echo "   Stage 1  – Crawl (4 Worker parallel):  ~2-3 Tage"
echo "   Stage 2  – Chunk + Embed (GPU):         ~2-4 Stunden"
echo "   Stage 3  – Graph-Extraktion (GPU):      ~12-24 Stunden"
echo "   Stage 4  – Neo4j Export:                ~1-2 Stunden"
echo "   Stage 5  – Community Detection:         ~4-8 Stunden"
echo "   Stage 6  – Evaluation:                  ~2-4 Stunden"
echo "   ─────────────────────────────────────────────────────"
echo "   Gesamt:                                 ~4-6 Tage"
echo "════════════════════════════════════════════════════════"
