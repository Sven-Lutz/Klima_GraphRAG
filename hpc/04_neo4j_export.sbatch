#!/bin/bash
#SBATCH --job-name=klima_neo4j_export
#SBATCH --output=logs/neo4j_export_%j.out
#SBATCH --error=logs/neo4j_export_%j.err
#
# ── Ressourcen ────────────────────────────────────────────────────────────────
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#
# ── Partition ────────────────────────────────────────────────────────────────
#SBATCH --partition=cpu
#
# ── WICHTIG: Neo4j-Optionen ──────────────────────────────────────────────────
#
# Option A (empfohlen für HPC): Neo4j läuft auf einem dedizierten Server/VM.
#   → Setze KLIMA_NEO4J_URI auf den Server, z.B.:
#        export KLIMA_NEO4J_URI="bolt://neo4j-server.cluster.intern:7687"
#
# Option B: Neo4j als Apptainer/Singularity-Container auf dem Compute-Node.
#   → Kommentare unten einkommentieren.
#
# Option C: SQLite-only-Modus (kein Neo4j).
#   → Setze NEO4J_SKIP=1; Community Detection läuft dann via BERTopic.
# =============================================================================
set -euo pipefail

PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate klima

cd "$PROJEKT_DIR"
mkdir -p logs

export KLIMA_DB_PATH="${PROJEKT_DIR}/crawler/data/db/crawl.sqlite"

# ── Option B: Neo4j via Apptainer starten ────────────────────────────────────
# NEO4J_SIF="${PROJEKT_DIR}/neo4j.sif"
# NEO4J_DATA="${PROJEKT_DIR}/data/neo4j"
# mkdir -p "${NEO4J_DATA}/data" "${NEO4J_DATA}/logs" "${NEO4J_DATA}/plugins"
#
# # GDS-Plugin herunterladen (einmalig):
# # https://neo4j.com/deployment-center/#gds-tab → neo4j-graph-data-science-*.jar
# # → nach ${NEO4J_DATA}/plugins/ kopieren
#
# apptainer run \
#   --bind "${NEO4J_DATA}/data:/data" \
#   --bind "${NEO4J_DATA}/logs:/logs" \
#   --bind "${NEO4J_DATA}/plugins:/plugins" \
#   --env NEO4J_AUTH=neo4j/password \
#   --env NEO4J_PLUGINS='["graph-data-science"]' \
#   docker://neo4j:5.20-community &
# NEO4J_PID=$!
#
# echo "→ Warte auf Neo4j (max 120s)..."
# for i in $(seq 1 120); do
#     curl -sf http://localhost:7474 > /dev/null 2>&1 && break
#     sleep 1
# done
# export KLIMA_NEO4J_URI="bolt://localhost:7687"
# ─────────────────────────────────────────────────────────────────────────────

echo "════════════════════════════════════════"
echo " Stage 2c – Neo4j Graph-Export"
echo " Neo4j:  ${KLIMA_NEO4J_URI:-bolt://localhost:7687}"
echo " Start:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

python -m crawler.scripts.export_graph_to_neo4j

# Embeddings auch nach Neo4j pushen (Vektor-Index)
echo "→ Embeddings nach Neo4j..."
python -m crawler.scripts.generate_embeddings \
    --model paraphrase-multilingual-MiniLM-L12-v2 \
    --neo4j

# ── Apptainer aufräumen (Option B) ───────────────────────────────────────────
# kill "${NEO4J_PID}" 2>/dev/null || true
# ─────────────────────────────────────────────────────────────────────────────

echo "✅ Neo4j-Export abgeschlossen: $(date '+%Y-%m-%d %H:%M:%S')"
