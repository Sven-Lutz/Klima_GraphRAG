#!/bin/bash
#SBATCH --job-name=klima_graph_extract
#SBATCH --output=logs/graph_extract_%j.out
#SBATCH --error=logs/graph_extract_%j.err
#
# ── Ressourcen ────────────────────────────────────────────────────────────────
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G                  # qwen2.5:14b braucht ~28 GB RAM (wenn kein VRAM genug)
#SBATCH --gres=gpu:1               # Mindestens 16 GB VRAM für qwen2.5:14b
#                                  # llama3.1:8b läuft auf 8 GB VRAM
#SBATCH --time=24:00:00            # ~1 h/1000 Chunks auf einer A100
#
# ── Partition (bitte anpassen) ───────────────────────────────────────────────
#SBATCH --partition=gpu
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate klima

cd "$PROJEKT_DIR"
mkdir -p logs

export KLIMA_DB_PATH="${PROJEKT_DIR}/crawler/data/db/crawl.sqlite"
export OLLAMA_HOME="${PROJEKT_DIR}/.ollama"
export PATH="${PROJEKT_DIR}/bin:${PATH}"

# Ollama-Modell (aus config überschreibbar)
OLLAMA_MODEL="${KLIMA_OLLAMA_MODEL:-qwen2.5:14b}"

echo "════════════════════════════════════════"
echo " Stage 2b – Graph-Extraktion via Ollama"
echo " Modell: ${OLLAMA_MODEL}"
echo " GPU:    $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'kein NVIDIA')"
echo " Start:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

# Ollama als Hintergrundprozess starten (gebunden an localhost des Compute-Nodes)
OLLAMA_PORT=11434
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
ollama serve &
OLLAMA_PID=$!

# Warten bis Ollama bereit ist (max 60 s)
echo "→ Warte auf Ollama..."
for i in $(seq 1 60); do
    curl -sf "http://${OLLAMA_HOST}/api/tags" > /dev/null 2>&1 && break
    sleep 1
done
echo "  ✓ Ollama läuft (PID ${OLLAMA_PID})"

export KLIMA_OLLAMA_URL="http://${OLLAMA_HOST}"

# Graph-Extraktion mit Gleaning
# --parallel 4: Ollama bedient mehrere Requests gleichzeitig (GPU-Auslastung);
# bei OOM auf 2 reduzieren, bei >24GB VRAM auf 8 erhöhen
python -m crawler.scripts.extract_graph_ollama \
    --model      "${OLLAMA_MODEL}" \
    --gleaning   1           \
    --chunk-mode             \
    --min-score  15          \
    --limit      999999      \
    --parallel   4

echo "→ Dokument-Zusammenfassungen..."
python -m crawler.scripts.summarize_documents \
    --min-score 20 \
    --per-doc   5

# Ollama sauber beenden
kill "${OLLAMA_PID}" 2>/dev/null || true
wait "${OLLAMA_PID}" 2>/dev/null || true

echo "✅ Graph-Extraktion abgeschlossen: $(date '+%Y-%m-%d %H:%M:%S')"
