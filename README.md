# KIT KlimaCrawler 🌍

Pipeline zur systematischen Erfassung von Energie- und Klimarichtlinien bayerischer Kommunen für die **GraphRAG-Analyse**.

📖 **Überblick über alle Pipeline-Stages (Chunking → Embeddings → Graph → Query → Evaluation): [docs/PIPELINE.md](docs/PIPELINE.md)** — Einstiegsdokument für neue Teammitglieder.

---

## 🛠 Voraussetzungen

- Python 3.9+ (empfohlen: 3.10)
- macOS, Linux oder Windows
- Stabile Internetverbindung
- Ausreichend Laufzeit (Großstädte: > 10 Stunden)

---

## 📦 Setup & Installation

### 1️⃣ Repository klonen

```bash
git clone <repository-url>
cd KIT_KlimaCrawler
```

---

### 2️⃣ Virtuelle Umgebung erstellen (wi2026)

#### Windows (PowerShell)

```powershell
python -m venv wi2026
.\wi2026\Scripts\Activate.ps1
```

#### Windows (CMD)

```cmd
python -m venv wi2026
wi2026\Scripts\activate
```

#### macOS / Linux

```bash
python3 -m venv wi2026
source wi2026/bin/activate
```

#### Alternative: Conda

```bash
conda create -n wi2026 python=3.10 -y
conda activate wi2026
```

---

### 3️⃣ Abhängigkeiten installieren

Im Ordner "crawler" (über cd crawler navigieren) dann:

```bash
pip install -r requirements.txt
```

---

## 🏃‍♂️ Crawl starten

Der Umfang wird über `--limit` und `--workers` gesteuert. Die Jobs kommen
aus der `seed_jobs`-Tabelle (atomares Claiming — mehrere Maschinen können
parallel gegen dieselbe DB-Kopie arbeiten, ohne sich Jobs wegzunehmen).

### Einzel-Lauf (eine Kommune)

```bash
python3 -m crawler.scripts.run_worker --limit 1
```

### Batch-Lauf (z. B. 100 Kommunen)

```bash
python3 -m crawler.scripts.run_worker --limit 100
```

---

## ☕ WICHTIG: Standby verhindern

Wenn der Rechner in den Ruhezustand geht, bricht die Netzwerkverbindung ab und der Crawl stoppt.

### macOS

```bash
caffeinate -i python3 -m crawler.scripts.run_worker
```

### Windows

 ### Nutze Tools wie:

- Caffeine  
- PowerToys Awake  

### 🔹 Installation

1. Gehe auf die offizielle GitHub-Seite von
Microsoft PowerToys
2. Klicke auf Releases
3. Lade die Datei
PowerToysSetup-xxx-x64.exe herunter
4. Installieren → fertig

### 🔹 Aktivieren von „Awake“

1. PowerToys öffnen
2. Links Awake auswählen
3. Aktivieren
4. Modus wählen:

Keep awake indefinitely

Keep awake for a time interval

Keep awake until expiration

-> Das ist die sauberste Lösung – kein Script, kein Hack.

---

## 📊 Monitoring & Erfolgskontrolle

### Fortschritt prüfen (Extraktions-Statistik)

```bash
sqlite3 crawler/data/db/crawl.sqlite "SELECT segment_type, COUNT(*) FROM segments GROUP BY segment_type;"
```

### Erfolgreicher Lauf

- `run_worker` beendet sich ohne Fehlermeldung  
- `seed_jobs.status` steht auf `done`  
- Tabellen `documents_raw` und `segments` sind befüllt  

- Fehler können aufkommen! Um eine Übersicht zu haben, könnt ihr euer Terminal splitten und 

```bash
while true; do
  clear;
  echo "=== CRAWLER PULS ($(date +%H:%M:%S)) ===";
  sqlite3 crawler/data/db/crawl.sqlite "
    SELECT 'Status ' || status || ': ' || count(*) FROM seed_jobs GROUP BY status;
    SELECT 'Gesamt-Dokumente: ' || count(*) FROM documents_raw;
    SELECT 'Davon PDFs:       ' || count(*) FROM documents_raw WHERE url_canonical LIKE '%.pdf';
  ";
  echo "----------------------------------------";
  echo "Zuletzt gefundene PDFs:";
  sqlite3 crawler/data/db/crawl.sqlite "SELECT url_canonical FROM documents_raw WHERE url_canonical LIKE '%.pdf' ORDER BY rowid DESC LIMIT 5;";
  sleep 30;
done
```

einfügen. 

### Oder

```bash
brew install watch
```

```bash
watch -n 30 "echo '=== CRAWLER DASHBOARD ===' && \
sqlite3 crawler/data/db/crawl.sqlite \"SELECT 'Status ' || status || ': ' || count(*) FROM seed_jobs GROUP BY status;\" && \
echo '---' && \
echo 'Gesamt-Dokumente:' && \
sqlite3 crawler/data/db/crawl.sqlite \"SELECT count(*) FROM documents_raw;\" && \
echo '---' && \
echo 'Neueste Nachhaltigkeits-PDFs:' && \
sqlite3 crawler/data/db/crawl.sqlite \"SELECT municipality_id, url_canonical FROM documents_raw WHERE (url_canonical LIKE '%klima%' OR url_canonical LIKE '%solar%' OR url_canonical LIKE '%mobil%' OR url_canonical LIKE '%haushalt%') AND url_canonical LIKE '%.pdf' ORDER BY rowid DESC LIMIT 5;\""
```
---

## 🔀 Parallel crawlen

Mehrere Kommunen gleichzeitig (je 1 Thread pro Kommune; die Politeness pro
Host bleibt erhalten, da jede Kommune ein eigener Host ist):

```bash
python3 -m crawler.scripts.run_worker --workers 8
```

Der Crawler respektiert `robots.txt` (Default) und nutzt Sitemap-Einträge
daraus als priorisierte Seeds. Transiente Fehler (Timeouts, 429/5xx) werden
automatisch mit Backoff wiederholt.

---

## 🧠 Nach dem Crawl: GraphRAG-Pipeline

Chunking → Embeddings → Graph-Extraktion (Ollama) → Neo4j → Communities →
Query Agent → Evaluation. Alle Stages, Kommandos und Tabellen:
**[docs/PIPELINE.md](docs/PIPELINE.md)**

```bash
python3 -m crawler.scripts.chunk_documents
python3 -m crawler.scripts.generate_embeddings
python3 -m crawler.scripts.extract_graph_ollama --chunk-mode --parallel 4
python3 -m crawler.scripts.query_agent "Welche Kommunen fördern Photovoltaik?"
```

DB verkleinern (Report ohne Flags — gefahrlos):

```bash
python3 -m crawler.scripts.prune_db
```

---

## 🧪 Tests & CI

```bash
pip install -r requirements-dev.txt
python -m pytest
```

GitHub Actions führt bei jedem Push Lint (ruff, kritische Fehler) und die
Test-Suite aus (`.github/workflows/ci.yml`).

---

## ⚠ Fehlerquellen

- **Netzwerk:** VPN-Abbruch oder instabiles WLAN beendet den Prozess  
- **Tools:** `pdftotext` muss im Systempfad installiert sein  
- **Pfad-Fehler:** Die Datenbank muss exakt in `crawler/data/db/` liegen — oder per `KLIMA_DB_PATH` überschreiben  

---

## 📌 Kurzfassung (TL;DR)

```bash
git clone <repository-url>
cd KIT_KlimaCrawler
python3 -m venv wi2026 && source wi2026/bin/activate
pip install -r crawler/requirements.txt
caffeinate -i python3 -m crawler.scripts.run_worker --workers 4
```

