# Öffentliche Reproduktion versus private Forschungsabhängigkeiten

## Öffentliche technische Komposition

Python 3.11, Node für JS-Verträge; bestehendes requirements-publication.lock. In frischem Checkout eigene venv installieren, Tokenizercache wie in der Root-REPRODUCING vorbereiten. Kein Modelldownload.

```bash
python -m crawler.web --fixture --directory generated/chat-demo --port 8825
# Im Browser: http://127.0.0.1:8825
python -m pytest @tests/public-tests.txt -ra -o tmp_path_retention_policy=failed -o tmp_path_retention_count=1
ruff check crawler demo tests docs
python scripts/qa/check_public_research.py
python scripts/qa/check_paper_dossier.py
```

Chat ist die Standardansicht; /research öffnet Experteneinstellungen, /validation trennt individuelle Prüfkonten. Der kanonische Fixturebestand ist erfunden. Beispiel: Solarplanung → 20 kWp als Plan, nicht Messung; Günzburg-Zahlenfrage → Evidenzlücke. Original-Bodyhash und PDF-Seite werden nicht erfunden. Für jeden Claim derselbe Evidence-Endpunkt, stabile Turn-/Snapshotbindung. Wiederstart liest denselben unveränderten Fixture-Snapshot; alte Turns werden nicht überschrieben.

Technische Konten/Material in der lokalen Reviewkomposition verwalten; automatisch erzeugte Owner-Credentials liegen nur im lokalen Reviewverzeichnis, Mode 0600. Echte Reviewer müssen individuell mit Einwilligung angelegt und zugewiesen werden. Der synthetische Store bleibt technical=true. Keine Credentials aus dem Store in Logs/Git kopieren. Der Fixture-Reviewpfad registriert ausschließlich eigene synthetische Dokumentbytes, keine Originale aus Chatfeedback.

Chrome-E2E optional mit öffentlichem Pythonpaket playwright und vorhandenem Chrome:

```bash
python -m pip install playwright==1.65.2
python scripts/qa/conversation_browser.py --output generated/browser-run-new
```

Kein Browser-/LLM-Modell automatisch laden. Browserloopback benötigt lokale Socketberechtigung; Sandboxfehler sind kein App-PASS. Viewport-Reflow und echte native Browser-Vergrößerung getrennt ausweisen. Receiptverzeichnis muss neu sein, frühere Fehlversuche erhalten.

## Wissenschaftliche Artefakte

Öffentliche IE-/Retrievalaggregate unter docs/research/artifacts erlauben Arithmetik-/Nennerprüfung. Der synthetische DS-006-Replay ist Code-/Fixture-reproduzierbar. Der historische Experimentcommit einzelner Aggregate ist NOT_KNOWN; Import-Snapshot ist keine nachträgliche Bindung an damals ausgeführten Code. Kein voller Modell-/Corpusreplay ohne Originalinputs behauptet.

Private Baseline der acht exponierten kommunalen Fälle wurde im bestehenden privaten Checkout mit vorhandenen Indizes, ohne Semantikmodell oder Inferenz, separat ausgeführt. Modell-/Routingbedingungen nicht mit alten Browserreceipts gleichsetzen. Private Traces enthalten Originalauszüge und bleiben lokal. Ihr Hash kann im Handoff gebunden werden, nicht ihr Inhalt. Ein öffentliches Reproduktionspaket benötigt erst Rechte- und Quellenfreigabe.

Keine 15/16-GiB-Gates abschwächen. Zuerst Repo/TMPDIR/Output messen. Bei fehlender Reserve private Vollsuite BLOCKED_BY_STORAGE; bei ungeklärtem E1-Import BLOCKED_BY_E1, unabhängig vom Platz. Historische 621 PASS sind kein neuer stabiler Pass. Keine Goldlabels oder Originale löschen. Fünf Neo4j-Livetests benötigen separat autorisierte Disposable-Instanz; externe HTTPS/OTP und unabhängige Menschen sind separate Gates.
