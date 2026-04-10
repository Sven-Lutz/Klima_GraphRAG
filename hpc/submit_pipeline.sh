#!/bin/bash
# =============================================================================
# submit_pipeline.sh  –  Startet die GraphRAG-Pipeline als verkettete
#                        SLURM-Jobs (Crawl läuft lokal, DB wird per
#                        hpc/transfer_db.sh übertragen)
#
# Verwendung:
#   bash hpc/submit_pipeline.sh [--from STAGE]
#
# Optionen:
#   --from 3   Pipeline ab einer bestimmten Stage starten
#              (2=chunk+embed, 3=graph, 4=neo4j, 5=community, 6=eval)
#
# Voraussetzung:
#   1. Lokal crawlen + DB übertragen:
#        python -m crawler.scripts.run_worker   (lokal)
#        bash hpc/transfer_db.sh --host <cluster>
#   2. Umgebung einrichten (einmalig auf dem Cluster):
#        bash hpc/00_setup_env.sh
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
cd "$PROJEKT_DIR"
mkdir -p logs

# ── Argumente parsen ──────────────────────────────────────────────────────────
START_FROM=2   # Crawl läuft lokal → Pipeline startet immer ab Stage 2
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) START_FROM="$2"; shift 2 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

# ── Voraussetzungen prüfen ────────────────────────────────────────────────────
DB_PATH="${KLIMA_DB_PATH:-${PROJEKT_DIR}/crawler/data/db/crawl.sqlite}"
if [[ ! -f "${DB_PATH}" ]]; then
    echo "FEHLER: Datenbank nicht gefunden: ${DB_PATH}"
    echo "Bitte zuerst lokal crawlen und mit hpc/transfer_db.sh übertragen."
    exit 1
fi

DB_DOCS=$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM documents_raw;" 2>/dev/null || echo "0")
echo "════════════════════════════════════════════════════════"
echo " KIT KlimaCrawler – SLURM Pipeline"
echo " Datenbank:  ${DB_PATH}"
echo " Dokumente:  ${DB_DOCS}"
echo " Starte ab:  Stage ${START_FROM}"
echo "════════════════════════════════════════════════════════"

PREV_JOB=""

dep_flag() {
    [[ -n "${PREV_JOB}" ]] && echo "--dependency=afterok:${PREV_JOB}" || echo ""
}

submit() {
    local stage="$1"
    local script="$2"
    local label="$3"

    if [[ "${stage}" -lt "${START_FROM}" ]]; then
        echo "  ⏭  Stage ${stage} übersprungen"
        return
    fi

    local dep
    dep=$(dep_flag)
    local job_id
    # shellcheck disable=SC2086
    job_id=$(sbatch ${dep} "${script}" | awk '{print $NF}')
    PREV_JOB="${job_id}"
    echo "  ✓  Stage ${stage} | ${label}"
    echo "         Job-ID: ${job_id}"
}

submit 2 hpc/02_chunk_embed.sbatch      "Chunking + Embeddings + Entity-Resolution"
submit 3 hpc/03_extract_graph.sbatch    "Graph-Extraktion (Ollama + Gleaning)"
submit 4 hpc/04_neo4j_export.sbatch     "Neo4j Export (Triplets + Embeddings)"
submit 5 hpc/05_community_detect.sbatch "Community Detection (Leiden + LLM Reports)"
submit 6 hpc/06_evaluate.sbatch         "Evaluation (AutoQ + RAGAS + AutoE)"

echo ""
echo "════════════════════════════════════════════════════════"
echo " Alle Jobs submitted. Fortschritt:"
echo "   squeue -u \$USER"
echo "   tail -f logs/chunk_embed_<JOBID>.out"
echo ""
echo " Geschätzte Gesamtdauer (ab jetzt):"
echo "   Stage 2  – Chunk + Embed (GPU):         ~2-4 Stunden"
echo "   Stage 3  – Graph-Extraktion (GPU):       ~12-24 Stunden"
echo "   Stage 4  – Neo4j Export (CPU):           ~1-2 Stunden"
echo "   Stage 5  – Community Detection (GPU):    ~4-8 Stunden"
echo "   Stage 6  – Evaluation (GPU):             ~2-4 Stunden"
echo "   ─────────────────────────────────────────────────────"
echo "   Gesamt:                                  ~20-40 Stunden"
echo "════════════════════════════════════════════════════════"
