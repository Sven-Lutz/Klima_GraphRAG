#!/bin/bash
# =============================================================================
# run_poc.sh  —  Lokaler Proof-of-Concept (kein GPU, kein Neo4j, kein Ollama)
#
# Was bewiesen wird:
#   ✓ Crawl:       3 bayerische Kommunen (Ingolstadt, Regensburg, Augsburg)
#   ✓ Chunking:    Tiktoken 600/60 auf allen relevanten Segmenten
#   ✓ Embeddings:  SentenceTransformers (CPU, all-MiniLM-L6-v2, ~90 MB)
#   ✓ Retrieval:   Cosine-Similarity Suche direkt auf SQLite-BLOBs
#
# Optional (falls Ollama läuft):
#   ~ Graph-Extraktion mit llama3.1:8b
#
# Dauer: ~15-30 Minuten auf einem normalen Laptop
# Speicher: ~500 MB (Modell + DB)
#
# Verwendung:
#   bash demo/run_poc.sh
#   bash demo/run_poc.sh --query "PV-Anlage Förderung"
# =============================================================================
set -euo pipefail

DEMO_DB="demo/demo.sqlite"
QUERY="${1:---query}"
if [[ "$QUERY" == "--query" ]]; then
    QUERY="Klimaschutz Maßnahmen Solarenergie Förderung"
fi
# Wenn erstes Arg --query ist, nimm das zweite als Query-Text
if [[ "${1:-}" == "--query" && -n "${2:-}" ]]; then
    QUERY="$2"
fi

cd "$(dirname "$0")/.."

echo "════════════════════════════════════════════════════════"
echo " KIT KlimaCrawler — GraphRAG Proof of Concept"
echo " Kommunen:  Ingolstadt · Regensburg · Augsburg"
echo " Datenbank: $DEMO_DB"
echo " Start:     $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════"

# ── 0. Abhängigkeiten installieren ────────────────────────────────────────────
echo ""
echo "[ 0/5 ] Abhängigkeiten prüfen & installieren..."

# NumPy: muss < 2 sein — PyTorch 2.x wurde gegen NumPy 1.x kompiliert
NUMPY_OK=$(python3 -c "import numpy as np; ok = tuple(int(x) for x in np.__version__.split('.')[:2]) < (2,0); print('yes' if ok else 'no')" 2>/dev/null || echo "no")
if [[ "$NUMPY_OK" != "yes" ]]; then
    echo "  → NumPy >= 2 gefunden, downgrade auf numpy<2 ..."
    python3 -m pip install --quiet "numpy<2"
fi

# Torch: mind. 2.4 nötig für aktuelle sentence-transformers
TORCH_OK=$(python3 -c "import torch; v=tuple(int(x) for x in torch.__version__.split('.')[:2]); print('yes' if v>=(2,4) else 'no')" 2>/dev/null || echo "no")
if [[ "$TORCH_OK" != "yes" ]]; then
    echo "  → torch < 2.4 oder fehlt – upgrade via conda..."
    if command -v conda &>/dev/null; then
        conda install -y "pytorch>=2.4" cpuonly -c pytorch -q 2>/dev/null \
            || python3 -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
    else
        python3 -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
    fi
fi

python3 -m pip install --quiet \
    tiktoken \
    pydantic \
    httpx \
    "httpx[http2]" \
    aiohttp \
    beautifulsoup4 \
    lxml \
    "sentence-transformers>=3.0.0"

echo "  ✓ Pakete bereit"

# ── 1. Demo-DB initialisieren ─────────────────────────────────────────────────
echo ""
echo "[ 1/5 ] Demo-Datenbank initialisieren..."

if [[ -f "$DEMO_DB" ]]; then
    DOCS=$(python3 -c "
import sqlite3
con = sqlite3.connect('$DEMO_DB')
try:
    n = con.execute('SELECT COUNT(*) FROM documents_raw').fetchone()[0]
    print(n)
except:
    print(0)
con.close()
")
    if [[ "$DOCS" -gt 0 ]]; then
        echo "  ↳ DB existiert mit $DOCS Dokumenten — Crawl überspringen (--fresh zum Reset)"
        SKIP_CRAWL=1
    else
        SKIP_CRAWL=0
    fi
else
    SKIP_CRAWL=0
fi

if [[ "${SKIP_CRAWL:-0}" -eq 0 ]]; then
    python3 demo/init_demo_db.py
fi

# ── 2. Crawl (3 Kommunen, max 120 Seiten je) ─────────────────────────────────
echo ""
echo "[ 2/5 ] Crawle 3 Kommunen (max. 120 Seiten je, Tiefe 4)..."
echo "        Dies dauert ca. 5-15 Minuten..."

if [[ "${SKIP_CRAWL:-0}" -eq 0 ]]; then
    # Crawler mit engen Limits für den Demo-Lauf
    python3 - <<'PYEOF'
import sys, sqlite3
sys.path.insert(0, ".")

# Monkey-patch DB_PATH VOR dem Import, damit Storage die Demo-DB nutzt
import crawler.core.storage as _storage_mod
from pathlib import Path
_storage_mod.DB_PATH = Path("demo/demo.sqlite")

from crawler.core.engine import Engine, EngineLimits

DB_PATH = Path("demo/demo.sqlite")

LIMITS = EngineLimits(
    max_depth=4,
    max_pages_per_muni=120,
    max_file_size_mb=50,
)

KEYWORDS = {
    "positive": [
        "klima", "klimaschutz", "energie", "solar", "photovoltaik",
        "co2", "waerme", "wärme", "förder", "foerder", "nachhaltig",
        "erneuerbar", "nki", "kfw", "mobilitaet", "mobilität",
    ],
    "negative": [
        "impressum", "datenschutz", "login", "karriere", "stellenangebot",
        "tourismus", "museum", "sport",
    ],
}

SEED_PATHS = ["/klima", "/klimaschutz", "/energie", "/umwelt", "/nachhaltigkeit"]

con = sqlite3.connect(str(DB_PATH), isolation_level=None)
jobs = con.execute(
    "SELECT municipality_id, seed_url FROM seed_jobs WHERE status='pending'"
).fetchall()
con.close()

if not jobs:
    print("  Alle Jobs bereits erledigt.")
    sys.exit(0)

for muni_id, seed_url in jobs:
    print(f"\n  → Crawle {muni_id}: {seed_url}")

    engine = Engine(
        keywords=KEYWORDS,
        limits=LIMITS,
        seed_additional_paths=SEED_PATHS,
    )
    # Default-Argument DB_PATH ist beim Import gebunden → nach der
    # Konstruktion überschreiben, damit die Demo-DB genutzt wird
    from crawler.core.storage import Storage as _Storage
    engine.storage = _Storage(DB_PATH)
    engine.run([(muni_id, seed_url)])

    # Markiere als done
    con2 = sqlite3.connect(str(DB_PATH), isolation_level=None)
    con2.execute("UPDATE seed_jobs SET status='done' WHERE municipality_id=?", (muni_id,))
    con2.close()

    c = sqlite3.connect(str(DB_PATH))
    docs = c.execute("SELECT COUNT(*) FROM documents_raw WHERE municipality_id=?", (muni_id,)).fetchone()[0]
    segs = c.execute("SELECT COUNT(*) FROM segments WHERE document_id IN "
                     "(SELECT document_id FROM documents_raw WHERE municipality_id=?)", (muni_id,)).fetchone()[0]
    c.close()
    print(f"     ✓ {muni_id}: {docs} Dokumente, {segs} Segmente")
PYEOF
else
    echo "  ↳ Crawl übersprungen (Daten vorhanden)"
fi

# Statistik
python3 - <<'PYEOF'
import sqlite3
con = sqlite3.connect("demo/demo.sqlite")
docs  = con.execute("SELECT COUNT(*) FROM documents_raw").fetchone()[0]
segs  = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
munis = con.execute("SELECT COUNT(DISTINCT municipality_id) FROM documents_raw").fetchone()[0]
print(f"\n  Ergebnis: {docs} Dokumente · {segs} Segmente · {munis} Kommunen")
con.close()
PYEOF

# ── 3. Tiktoken-Chunking ──────────────────────────────────────────────────────
echo ""
echo "[ 3/5 ] Tiktoken-Chunking (600 Tok / 60 Overlap)..."

python3 - <<'PYEOF'
import sys; sys.path.insert(0, ".")
from crawler.scripts.chunk_documents import main
main(db_path="demo/demo.sqlite", chunk_size=600, overlap=60, min_score=0)
PYEOF

python3 - <<'PYEOF'
import sqlite3
con = sqlite3.connect("demo/demo.sqlite")
try:
    n = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"  ✓ {n} Chunks erstellt")
except:
    print("  ! chunks-Tabelle fehlt")
con.close()
PYEOF

# ── 4. Embeddings (CPU, all-MiniLM-L6-v2) ────────────────────────────────────
echo ""
echo "[ 4/5 ] Embeddings berechnen (CPU, ~5-10 Minuten)..."
echo "        Modell: all-MiniLM-L6-v2 (~90 MB Download beim ersten Mal)"

python3 - <<'PYEOF'
import sys, sqlite3, struct
sys.path.insert(0, ".")
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np

DB = "demo/demo.sqlite"
MODEL = "all-MiniLM-L6-v2"   # schnell auf CPU, gutes DE+EN

print(f"  Lade Modell {MODEL}...")
model = SentenceTransformer(MODEL)

con = sqlite3.connect(DB, isolation_level=None)
con.execute("PRAGMA journal_mode=WAL")

# Chunks ohne Embedding laden
rows = con.execute(
    "SELECT chunk_id, text FROM chunks WHERE embedding IS NULL LIMIT 10000"
).fetchall()

if not rows:
    print("  ✓ Alle Chunks bereits eingebettet")
    con.close()
    sys.exit(0)

print(f"  Berechne Embeddings für {len(rows)} Chunks...")

BATCH = 64
for i in range(0, len(rows), BATCH):
    batch = rows[i:i+BATCH]
    ids   = [r[0] for r in batch]
    texts = [r[1] for r in batch]
    vecs  = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    for cid, vec in zip(ids, vecs):
        blob = struct.pack(f"{len(vec)}f", *vec.tolist())
        con.execute("UPDATE chunks SET embedding=? WHERE chunk_id=?", (blob, cid))

    if (i // BATCH) % 5 == 0:
        done = min(i + BATCH, len(rows))
        print(f"  ... {done}/{len(rows)} ({100*done//len(rows)}%)")

con.close()
print(f"  ✓ Embeddings abgeschlossen")
PYEOF

# ── 5. Demo-Suche ────────────────────────────────────────────────────────────
echo ""
echo "[ 5/5 ] GraphRAG Demo-Suche..."
echo "        Anfrage: \"$QUERY\""
echo ""

python3 demo/query_demo.py \
    --db "demo/demo.sqlite" \
    --top 5 \
    "$QUERY"

echo "════════════════════════════════════════════════════════"
echo " PoC abgeschlossen!"
echo ""
echo " Weitere Anfragen:"
echo "   python demo/query_demo.py --db demo/demo.sqlite \"Wärmepumpe Förderung\""
echo "   python demo/query_demo.py --db demo/demo.sqlite \"Photovoltaik Ausbau Ziel\""
echo "   python demo/query_demo.py --db demo/demo.sqlite \"CO2 Reduktion Maßnahmen\""
echo ""
echo " Nächster Schritt: Ollama installieren für Graph-Extraktion"
echo "   curl -fsSL https://ollama.com/install.sh | sh"
echo "   ollama pull llama3.1:8b"
echo "   python -m crawler.scripts.extract_graph_ollama --db demo/demo.sqlite"
echo "════════════════════════════════════════════════════════"
