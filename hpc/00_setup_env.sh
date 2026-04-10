#!/bin/bash
# =============================================================================
# 00_setup_env.sh  –  Einmalige Einrichtung der Python-Umgebung + Ollama
#
# Einmal manuell auf dem Login-Node ausführen, bevor die SBATCH-Jobs gestartet
# werden:
#
#   bash hpc/00_setup_env.sh
#
# Voraussetzungen:
#   - Conda/Miniconda auf dem Cluster verfügbar  (module load conda o.ä.)
#   - PROJEKT_DIR ist das Repo-Root (wird unten gesetzt)
# =============================================================================
set -euo pipefail

# ── Bitte anpassen ────────────────────────────────────────────────────────────
PROJEKT_DIR="${HOME}/KIT_KlimaCrawler"   # Pfad zum Repo
ENV_NAME="klima"                          # Conda-Environment-Name
PYTHON_VERSION="3.11"
OLLAMA_MODEL="qwen2.5:14b"               # Bestes DE-Modell; "llama3.1:8b" falls VRAM knapp
# ─────────────────────────────────────────────────────────────────────────────

cd "$PROJEKT_DIR"

echo "════════════════════════════════════════"
echo " KIT KlimaCrawler – Environment Setup"
echo "════════════════════════════════════════"

# 1. Conda-Environment erstellen
echo "[1/5] Erstelle Conda-Environment '$ENV_NAME' (Python $PYTHON_VERSION)..."
conda create -y -n "$ENV_NAME" python="$PYTHON_VERSION"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$ENV_NAME"

# 2. Python-Pakete installieren
echo "[2/5] Installiere Python-Abhängigkeiten..."
pip install --upgrade pip
pip install -r crawler/requirements.txt

# 3. Ollama installieren (kein Root nötig – Binary ins Projekt)
echo "[3/5] Installiere Ollama binary nach $PROJEKT_DIR/bin/ ..."
mkdir -p "$PROJEKT_DIR/bin"
curl -fsSL https://ollama.com/install.sh \
  | OLLAMA_INSTALL_DIR="$PROJEKT_DIR/bin" sh 2>/dev/null \
  || {
    # Fallback: direkt herunterladen
    curl -L "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64" \
      -o "$PROJEKT_DIR/bin/ollama"
    chmod +x "$PROJEKT_DIR/bin/ollama"
  }
export PATH="$PROJEKT_DIR/bin:$PATH"

# 4. Ollama-Modell vorab herunterladen (braucht Internet – Login-Node)
echo "[4/5] Lade LLM-Modell '$OLLAMA_MODEL' herunter (kann mehrere GB dauern)..."
OLLAMA_HOME="$PROJEKT_DIR/.ollama" ollama pull "$OLLAMA_MODEL"
echo "  ✓ Modell gespeichert in $PROJEKT_DIR/.ollama"

# 5. SQLite-Datenbank initialisieren + Seed-Jobs anlegen
echo "[5/5] Initialisiere Seed-Jobs..."
python -m crawler.scripts.init_seed_jobs

echo ""
echo "✅ Setup abgeschlossen. Starte jetzt die SBATCH-Jobs:"
echo "   bash hpc/submit_pipeline.sh"
