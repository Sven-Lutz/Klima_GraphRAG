#!/bin/bash
#SBATCH --job-name=klima_crawl
#SBATCH --output=logs/crawl_%A_%a.out
#SBATCH --error=logs/crawl_%A_%a.err
#
# ── Array: 4 parallele Worker (0-3) ──────────────────────────────────────────
#SBATCH --array=0-3
#
# ── Ressourcen ────────────────────────────────────────────────────────────────
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4          # async I/O-bound; 4 CPUs genügen
#SBATCH --mem=8G
#SBATCH --time=72:00:00            # 3 Tage; 2057 Munis / 4 Worker ≈ 2-3 Tage
#
# ── Partition (bitte anpassen) ───────────────────────────────────────────────
#SBATCH --partition=cpu
#
# ── Netzwerk: Compute-Nodes brauchen ausgehenden HTTP/S-Zugang ───────────────
# Falls dein Cluster das blockiert: --partition=login oder proxy setzen
# (z.B. #SBATCH --constraint=internet)
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate klima

cd "$PROJEKT_DIR"
mkdir -p logs

# SQLite auf schnellem Shared-Filesystem (Lustre/GPFS) lassen –
# WAL-Mode ist bereits aktiv; busy_timeout=60s verhindert Lock-Konflikte
export KLIMA_DB_PATH="${PROJEKT_DIR}/crawler/data/db/crawl.sqlite"

# Worker-ID aus Array-Index ableiten (worker_0 … worker_3)
WORKER_ID="hpc_worker_${SLURM_ARRAY_TASK_ID}"

echo "════════════════════════════════════════"
echo " Stage 0 – Crawl Worker ${WORKER_ID}"
echo " Hostname:  $(hostname)"
echo " Start:     $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

python -m crawler.scripts.run_worker \
    --worker-id "${WORKER_ID}" \
    --max-municipalities 600   # jeder Worker übernimmt ~600 von 2057

# Nachdem alle Munis gecrawlt: ausgehende Links auflösen
# (nur Worker 0 macht das, die anderen überspringen es harmlos)
if [[ "${SLURM_ARRAY_TASK_ID}" == "0" ]]; then
    echo "→ Löse document_links auf..."
    python - <<'EOF'
import sqlite3, os
db = os.environ["KLIMA_DB_PATH"]
con = sqlite3.connect(db, isolation_level=None)
con.execute("""
    UPDATE document_links
    SET target_document_id = (
        SELECT document_id FROM documents_raw
        WHERE url_canonical = document_links.target_url_canonical
        LIMIT 1
    )
    WHERE target_document_id IS NULL
""")
con.close()
print("  ✓ document_links aufgelöst")
EOF
fi

echo "✅ Crawl Worker ${WORKER_ID} abgeschlossen: $(date '+%Y-%m-%d %H:%M:%S')"
