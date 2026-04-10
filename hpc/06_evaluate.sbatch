#!/bin/bash
#SBATCH --job-name=klima_evaluate
#SBATCH --output=logs/evaluate_%j.out
#SBATCH --error=logs/evaluate_%j.err
#
# ── Ressourcen ────────────────────────────────────────────────────────────────
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#
# ── Partition ────────────────────────────────────────────────────────────────
#SBATCH --partition=gpu
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate klima

cd "$PROJEKT_DIR"
mkdir -p logs results

export KLIMA_DB_PATH="${PROJEKT_DIR}/crawler/data/db/crawl.sqlite"
export OLLAMA_HOME="${PROJEKT_DIR}/.ollama"
export PATH="${PROJEKT_DIR}/bin:${PATH}"

OLLAMA_MODEL="${KLIMA_OLLAMA_MODEL:-qwen2.5:14b}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

echo "════════════════════════════════════════"
echo " Stage 5 – Evaluation (AutoQ + RAGAS)"
echo " Modell: ${OLLAMA_MODEL}"
echo " Start:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

# Ollama starten
OLLAMA_PORT=11434
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
export KLIMA_OLLAMA_URL="http://${OLLAMA_HOST}"
ollama serve &
OLLAMA_PID=$!
for i in $(seq 1 60); do
    curl -sf "http://${OLLAMA_HOST}/api/tags" > /dev/null 2>&1 && break
    sleep 1
done

python -m crawler.scripts.evaluate \
    --questions 50 \
    --llm       ollama \
    --output    "results/eval_${TIMESTAMP}.json"

kill "${OLLAMA_PID}" 2>/dev/null || true
wait "${OLLAMA_PID}" 2>/dev/null || true

echo "✅ Evaluation abgeschlossen: results/eval_${TIMESTAMP}.json"
