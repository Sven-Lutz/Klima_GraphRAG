#!/bin/bash
#SBATCH --job-name=klima_chunk_embed
#SBATCH --output=logs/chunk_embed_%j.out
#SBATCH --error=logs/chunk_embed_%j.err
#
# ── Ressourcen ────────────────────────────────────────────────────────────────
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --gres=gpu:1               # 1 GPU für SentenceTransformers
#SBATCH --time=04:00:00            # Chunking ~30 min; Embeddings ~2-3 h (GPU)
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

echo "════════════════════════════════════════"
echo " Stage 1+2a – Chunking & Embeddings"
echo " GPU:    $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'kein NVIDIA')"
echo " Start:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

# ── Stage 1: Tiktoken-Chunking (CPU) ─────────────────────────────────────────
echo "[1/3] Tiktoken-Chunking (600 Tok / 60 Tok Overlap)..."
python -m crawler.scripts.chunk_documents \
    --chunk-size 600 \
    --overlap    60  \
    --min-score  15

# ── Stage 2a: Entity-Auflösung (bevor Extraktion läuft) ──────────────────────
echo "[2/3] Entity-Resolution..."
python -m crawler.scripts.resolve_entities \
    --similarity 0.85 \
    --min-freq   2

# ── Stage 2a: Vektor-Embeddings (GPU) ────────────────────────────────────────
echo "[3/3] SentenceTransformer-Embeddings (paraphrase-multilingual-MiniLM-L12-v2)..."
python -m crawler.scripts.generate_embeddings \
    --model paraphrase-multilingual-MiniLM-L12-v2 \
    --batch 128
# Neo4j-Vektorprojektion ist im kanonischen Modell nicht implementiert.

echo "✅ Chunk+Embed abgeschlossen: $(date '+%Y-%m-%d %H:%M:%S')"
