#!/bin/bash
# =============================================================================
# transfer_db.sh  –  Überträgt die lokal gecrawlte SQLite-Datenbank auf den
#                    HPC-Cluster und prüft die Integrität
#
# Verwendung (lokal ausführen, NICHT auf dem Cluster):
#   bash hpc/transfer_db.sh --host <cluster-login> [--user <username>]
#
# Beispiel:
#   bash hpc/transfer_db.sh --host bwunicluster.scc.kit.edu --user st_abc1234
#   bash hpc/transfer_db.sh --host hpc.example.de           # User = $USER
# =============================================================================
set -euo pipefail

# ── Defaults (lokal anpassen) ─────────────────────────────────────────────────
LOCAL_DB="crawler/data/db/crawl.sqlite"
REMOTE_BASE="${HOME}/KIT_KlimaCrawler"   # Pfad auf dem Cluster
HPC_HOST=""
HPC_USER="${USER}"
# ─────────────────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HPC_HOST="$2"; shift 2 ;;
        --user) HPC_USER="$2"; shift 2 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

if [[ -z "${HPC_HOST}" ]]; then
    echo "Fehler: --host <cluster-login> ist erforderlich."
    echo "Beispiel: bash hpc/transfer_db.sh --host bwunicluster.scc.kit.edu"
    exit 1
fi

if [[ ! -f "${LOCAL_DB}" ]]; then
    echo "Fehler: Datenbank nicht gefunden: ${LOCAL_DB}"
    echo "Bitte erst lokal crawlen: python -m crawler.scripts.run_worker"
    exit 1
fi

REMOTE="${HPC_USER}@${HPC_HOST}"
REMOTE_DB="${REMOTE_BASE}/crawler/data/db/crawl.sqlite"

# ── 1. Lokale DB-Integrität prüfen ───────────────────────────────────────────
echo "════════════════════════════════════════"
echo " KIT KlimaCrawler – DB Transfer"
echo " Quelle:   ${LOCAL_DB}"
echo " Ziel:     ${REMOTE}:${REMOTE_DB}"
echo "════════════════════════════════════════"

echo "[1/5] Prüfe lokale Datenbank-Integrität..."
sqlite3 "${LOCAL_DB}" "PRAGMA integrity_check;" | grep -q "^ok$" \
    && echo "  ✓ integrity_check ok" \
    || { echo "  ✗ Datenbank beschädigt! Crawl abbrechen."; exit 1; }

# Statistik ausgeben
echo "[2/5] Datenbank-Statistik:"
sqlite3 "${LOCAL_DB}" "
    SELECT '  Dokumente:  ' || COUNT(*) FROM documents_raw;
    SELECT '  Segmente:   ' || COUNT(*) FROM segments;
    SELECT '  Munis:      ' || COUNT(DISTINCT municipality_id) FROM documents_raw;
    SELECT '  doc_links:  ' || COUNT(*) FROM document_links;
" 2>/dev/null || echo "  (Tabellen noch nicht vorhanden)"

DB_SIZE=$(du -sh "${LOCAL_DB}" | cut -f1)
echo "  Dateigröße: ${DB_SIZE}"

# ── 2. Zielverzeichnis auf Cluster anlegen ────────────────────────────────────
echo "[3/5] Lege Zielverzeichnis auf ${HPC_HOST} an..."
ssh "${REMOTE}" "mkdir -p $(dirname "${REMOTE_DB}")"

# ── 3. Übertragen via rsync (komprimiert, mit Fortschrittsanzeige) ────────────
echo "[4/5] Übertrage Datenbank (rsync, komprimiert)..."
rsync \
    --archive \
    --compress \
    --progress \
    --partial \
    --human-readable \
    "${LOCAL_DB}" \
    "${REMOTE}:${REMOTE_DB}"

# ── 4. Checksumme auf beiden Seiten vergleichen ───────────────────────────────
echo "[5/5] Prüfe Checksumme..."
LOCAL_MD5=$(md5sum "${LOCAL_DB}" | awk '{print $1}')
REMOTE_MD5=$(ssh "${REMOTE}" "md5sum '${REMOTE_DB}'" | awk '{print $1}')

if [[ "${LOCAL_MD5}" == "${REMOTE_MD5}" ]]; then
    echo "  ✓ Checksumme stimmt überein: ${LOCAL_MD5}"
else
    echo "  ✗ Checksummen unterschiedlich!"
    echo "    Lokal:  ${LOCAL_MD5}"
    echo "    Remote: ${REMOTE_MD5}"
    exit 1
fi

echo ""
echo "════════════════════════════════════════"
echo " Transfer abgeschlossen."
echo ""
echo " Nächster Schritt auf dem Cluster:"
echo "   ssh ${REMOTE}"
echo "   cd ${REMOTE_BASE}"
echo "   bash hpc/00_setup_env.sh   # falls noch nicht gemacht"
echo "   bash hpc/submit_pipeline.sh"
echo "════════════════════════════════════════"
