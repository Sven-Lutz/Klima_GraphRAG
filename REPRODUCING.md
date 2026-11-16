# Reproduktion des öffentlichen Snapshots

Python 3.11, Node.js 22 für die JS-Vertragstests. Frische isolierte Umgebung; keine produktiven Secrets oder Datenbankpfade übernehmen. Öffentliche Dependencies sind in requirements-publication.lock auf die tatsächlich installierten Versionen fixiert (keine Garantie identischer transitativer Wheels auf jeder Plattform).

```bash
git clone --branch sven-develop https://github.com/Sven-Lutz/Klima_GraphRAG.git
cd Klima_GraphRAG
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-publication.lock
TIKTOKEN_CACHE_DIR="$PWD/.venv/tokenizer-cache" .venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
.venv/bin/python -m ruff check crawler demo tests docs
.venv/bin/python -m compileall -q crawler research scripts deployment
.venv/bin/python -m pytest @tests/public-tests.txt -ra -o tmp_path_retention_policy=failed -o tmp_path_retention_count=1
.venv/bin/python scripts/qa/check_public_research.py
.venv/bin/python -m research.experiments.ds006.continuity --output generated/ds006-current
```

`generated/ds006-current` muss neu sein. Erst Tokenizer vorbereiten, danach verbieten die synthetischen Kontinuitätstests Netzwerkzugriff. Der Tokenizerdownload ist keine Inferenz. Seine Dateien werden nicht verteilt; Paket-/Tokenizer-/Codehashes stehen im Reproduktionsmanifest. Timingdaten dürfen zwischen Maschinen abweichen. Semantische Matrix und Zähler des Replays sind mit dem freigegebenen DS-006-Artefakt vergleichbar. Aktueller Replay: A 7/10 Entwicklungs-, 0/5 Challenge-Belege; B 10/10 und 4/5. A/B 13/29 Chunks und 3532/3841 Token. Die Abweichung gegenüber historischen 3/5 bzw. 28 Chunks bleibt offengelegt.

Die öffentliche Antwortdatei `tests/public-tests.txt` umfasst nur Fälle mit vorhandenen Inputs und geklärten Importabhängigkeiten. Sie senkt keine Produktgates und ändert keine Assertions. Die Ausschlussliste benennt private PACKET-/Quellen-/Katalogabhängigkeiten und E1-Lizenzgrenze. Mit `python -m pytest tests -ra` kann die komplette Owner-Suite versucht werden; im nackten öffentlichen Baum ist das **kein erfolgreicher Lauf**, weil die aufgeführten Eingaben/Importe fehlen. CI heißt deshalb Public snapshot CI.

Fünf Disposable-Neo4j-Tests sind opt-in und hier nicht live ausgeführt; zusätzlich fehlt ein optionales archiviertes Original. Keine Produktionsdatenbank verbinden. Eine externe OTP-/Browserabnahme oder unabhängige Humanannotation wird durch diesen Testpfad nicht ausgeführt.

Schreibende Produkt-/Workspace-/Studio-Tests verlangen weiterhin die ursprüngliche 15-GiB-Basisreserve plus Outputbudget (teilweise insgesamt 16 GiB). Vorher `df -h .` und den TMPDIR-Datenträger prüfen; auf anderem physischem Datenträger ausreichend Platz vorhalten. Ein weiterer APFS-Band im selben Container schafft keinen freien Platz. Keine Gates deaktivieren, Forschungsarchive löschen oder Ressourcenfehler als PASS zählen.

Die veröffentlichten historischen IE-/Retrievalaggregate erlauben Arithmetik-/Nennerprüfung. Originalinputs, vollständige qrels und teils damalige Code-SHAs fehlen öffentlich; vollständiger historischer Replay und unabhängige Qualität bleiben nicht nachgewiesen. Kein neues Modell, Crawl oder Retrievalbenchmark ist für den Contributor-Einstieg erforderlich.


V2: `scripts/qa/conversation_v2_browser.py --output generated/ci-v2-browser` prüft synthetische Arbeitsräume mit der vorhandenen CI-Version Playwright 1.65.2 und Chrome. Native Zoom-/Screenreader-/Humanabnahme wird nicht ausgeführt. V1-Dossierbindungen benötigen den expliziten Commit 0483eef9d8bb9c37559d8dcee0e0b06449d9a394 in der lokalen Git-Historie (CI fetch-depth 20); V2-Bindungen werden separat über `scripts/qa/refresh_v2_registers.py --code-commit <V2-code-SHA>` aktualisiert. Der alte Builder überschreibt eingefrorene V1-Zeilen nicht. Aktuelle Ressourcengrenzen, Fehlversuche und Startstatus: docs/product/V2_QA_REPORT.md.
