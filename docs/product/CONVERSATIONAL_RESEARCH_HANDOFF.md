# Conversational Research v1 — technische Übergabe und offene Abnahme

Basis: remote verifiziertes `6edd3d8b182867c2da1ad0e2899033d3fc4ee71c`, isolierter Branch `feat/conversational-research-v1`. Implementierungscommit `c85020d` (exakte SHA in den Papierregistern). main/sven-develop und private Historie bleiben unverändert. Neue Dokumentations-/Nachweiscommits sind im Branchlog prüfbar. Kein Live-Deployment.

## Start und tatsächlicher Funktionsumfang

```bash
python -m crawler.web --fixture --directory generated/chat-demo --port 8825
```

Loopback-URL: http://127.0.0.1:8825 — **nur bei tatsächlich laufendem lokalen Server**, kein freigegebener externer Demozugang. Der eigene QA-Server wurde nach Unterschreiten der Reserve beendet. Zuerst ausreichenden freien Platz bereitstellen. / ist Chat, /research Expertenmodus, /validation individuelle Review-Queue. Vorher fixierte Dependencies und öffentlichen Tokenizer wie REPRODUCING installieren. `--legacy` öffnet die bisherige Workbench; private Produktintegration ist nicht automatisch freigegeben.

Neu: direkte extraktive Antwort vor Treffern; stabile Conversations/Turns, Elternversionen für Retry/Edit, klarer Scope/Zeithinweis, Rückfragen, Teilantwort/Evidenzlücke, anklickbarer Evidence-Dialog, Copy/JSON-/Traceexport und getrenntes Produktfeedback. Persistente lokale SQLite-Datei Mode 0600, 24h-Sitzung, logische Löschung bei Ablauf/Logout oder explizitem Chatdelete; spätere Server-/Sessionaktivität führt die Ablaufbereinigung aus. Backups/forensisches Löschen sind nicht enthalten. Kein Streaming und kein konfiguriertes Chat-LLM.

Beispiel im erfundenen Fixture: „Welche Solarprojekte planen die Stadtwerke?“ → Originalpassage „Die Stadtwerke Beispiel planen eine Solaranlage auf dem Rathaus mit 20 kWp.“ als PLAN. Die Antwort enthält tatsächliche kanonische Evidence-/Citation-IDs; technische Run-IDs/Snapshot und Passagenhash werden mitgespeichert. Original-Bodyhash/PDF-Seite sind in diesem Fixturevertrag unbekannt und werden ausdrücklich nicht erfunden. „Wie viel Leistung?“ übernimmt den Gesprächsfokus. Günzburg-Zahlenfrage → INSUFFICIENT_EVIDENCE. „Wer plant die Solaranlage?“ öffnet eine vorhandene synthetische PLANT-Relation mit derselben Evidence-ID. Konkrete Lauf-IDs entstehen im gebundenen lokalen Store, keine erfundenen Beispiel-IDs.

Graph ist auf verwendete Belege begrenzt. Source-Provenienzlinks und EXTRACTED_UNVERIFIED-Kanten sind getrennt, keine behaupteten Humanlabels. Große Graph-/Zeit-/Gemeindefilter und vollständige freie Claim-/Aspektauflösung sind noch nicht qualifiziert. Sichtbare Cutoffs und zugängliche Alternative ersetzen keine fachliche Graphvalidierung.

Review: bestehender ReviewProtocol statt neuem Goldframework. Individuelle Rollen, zwei getrennte Ersturteile, Konflikt/Adjudikation, Originalöffnungsreceipt, Draft, unveränderliche Abgabe und Export/Audit. Eigene synthetische Reviewdokumente sind explizit getrennt von kommunalen Originalen; keine automatische Übernahme von Chatfeedback. Operator legt Konten/Material/Zuweisung über die geschützten API-/Protocolmethoden an. Im technischen Store bleibt auch die vollständig durchlaufene Prüfung human_gold=false. Öffnen allein beweist kein Lesen. Vollständige Studienmasken pro Annotationsdimension sind nicht als fertig zertifiziert ausgegeben.

## Tatsächliche bisherige Prüfungen

- Öffentliche Basis historisch: 380 PASS / 6 SKIP, CI Run 40. Kein aktueller privater 621-PASS-Nachweis.
- Neue Chat-/Security-/Graph-/Reviewtests: **13 PASS** vor den zusätzlichen vier Dimensionsfällen; danach neun reine negative/Dimensions-Qualifikationsfälle ohne Storeallokation geprüft. Die vollständige neue öffentliche Kombination wird separat durch die aktuelle CI ausgewiesen, nicht additiv zu einem erfundenen lokalen Vollpass.
- Neuer technischer Reviewtest: Original-Receiptpflicht, zwei verblindete Urteile, Konflikt, Adjudikation, Audit/Export; kein echtes Human Gold.
- Lokaler Chrome-Lauf `browser-receipts-02`: Antwort, Follow-up, Citationdialog/Escape, Abstention; 360/768/1440/640 px ohne horizontales Overflow, fokussierbare Eingabe, Research-/Validation-Navigation, keine page_errors. Erste gemessene synthetische Antwort bis DOM: ca. 0,104 s, ein Lauf, keine repräsentative Latenzverteilung/Usabilitymetrik.
- Der erste Browserharness wartete nicht auf das asynchron geöffnete Dialogelement und scheiterte; Wait korrigiert. Visuelle Inspektion des 360px-Screenshots zeigte einen überdeckenden Sticky-Composer. Layout gezielt auf eigene Composerzeile umgestellt. **Der frühere Screenshot ist kein PASS des korrigierten Layouts.** Finaler Browsernachweis über aktuelle CI/Receipt; native Zoomprüfung weiterhin getrennt.
- Ruff und Python/JS-Syntaxchecks ausgeführt. Keine neue Inferenz, kein Modell-/Graphdownload, kein Recrawl. Hardwareumfeld arm64; RAM-/Swapdaten nicht als neue Leistungsmessung ausgegeben. Tatsächliche Diskmessungen im lokalen Freeze und Logs; später ca. 14,24 GiB frei, neuer Serverstart korrekt am 15-GiB+64-MiB-Chatgate blockiert.
- Private acht Fälle tatsächlich erneut ausgeführt: siehe BASELINE_20261115. Brunn–Laaber/Amberg grounding_failed; kein neuer 3/3-Erfolg behauptet. Andere Retrieval-/Modelleinstellungen nicht gleichgesetzt.

## Dossier und Reproduzierbarkeit

Unter docs/paper: RESEARCH_DOSSIER.md (22 Kapitel), CLAIM_EVIDENCE_REGISTER.csv, EXPERIMENT_MATRIX.csv, ARTIFACT_MANIFEST.tsv, LITERATURE.bib, PAPER_OUTLINE.md, HUMAN_REVIEW_PROTOCOL.md, REPRODUCTION_GUIDE.md, UX_EVALUATION_PROTOCOL.md, OPEN_GATES.md. Register durch `scripts/qa/build_paper_registers.py` aus explizit benannten öffentlichen Artefakten erzeugt; `check_paper_dossier.py` prüft tatsächliche Hashes/Links/Statusgrenzen. Literatur am 15.11. gegen arXiv/ACL/Implementierungsprimärquellen geprüft, keine vollständige neue Literaturstudie. Lokales Forschungsskript bleibt historisch und privat.

IE-/Relationsaggregate sind eigene begrenzte Studien; kein Pooling, keine allgemeine GraphRAG-Überlegenheit. PRIVATE Originalstudien können nicht aus dem öffentlichen Code rekonstruiert werden. Echte Humanannotation und vollständige qrels fehlen. Neue prospektive Designs sind Entwürfe, nicht registrierte oder bereits ausgeführte Vergleiche.

## Separate Ampeln

| Ampel | Stand | Bedeutung |
|---|---|---|
| ENGINEERING | GELB | öffentliche synthetische Verträge und finale lokale Suite bestanden; private Integration offen |
| ANSWER_QUALITY_HUMAN_VALIDATED | ROT | keine unabhängigen fachlichen Labels; Scope-/Aspekt-/False-Abstention-Grenzen |
| UX_HUMAN_VALIDATED | ROT | technische Browserchecks, keine formative Personenstudie oder WCAG-Zertifizierung |
| RESEARCH_REPRODUCIBILITY | GELB | öffentliche synthetische Reproduktion/Aggregatprüfung; private Originalstudien nicht vollständig replaybar |
| EXTERNAL_PREVIEW | ROT | keine externe HTTPS-/OTP-/Datenfreigabe, kein Deployment |
| PAPER_SUBMISSION_READY | ROT | Dossier vorhanden; Rechte, unabhängiges Gold, zulässige Kontraste/Claims offen |

## Human-only-Gates und fünf nächste Schritte

1. Konkrete E1-Kit-Rechte belegen; keine Umbenennung oder alternative Implementierung zur Umgehung. Vollcorpus-Konversationsintegration danach gezielt prüfen.
2. Stabile Testreserve auf geeignetem physischem Datenträger bereitstellen; bestehende Gates erhalten, private lokale Läufe erst dann wiederholen.
3. Erlaubte Quellen-/Original-/Review-/Exportansicht und echte Reviewerrollen mit Datenschutz/Einwilligung festlegen.
4. Prospektive Endpunkte, unabhängige Holdouts und Kontraste vor neuen Human-/Modelloutputs genehmigen; formative UX getrennt erheben.
5. Erst danach externen HTTPS-/OTP-Pilot und eventuellen Modell-Footprint entscheiden. Kein Merge oder Deployment aus diesem Handoff ableiten.

Featurepush/Draft-PR ausschließlich gegen sven-develop nach Hash-/Rights-/Secrets-Allowlist. Endgültiger PR-Link und Hosted-CI-Nachweise werden in der GitHub-Draftbeschreibung geführt; dieses Dokument behauptet keinen vorweggenommenen CI-PASS.

## Finaler Nachweis nach Wiederherstellung der vorhandenen Testvoraussetzungen

**397 PASS / 6 SKIP, 6,34 s** im tatsächlich geprüften öffentlichen Umfang. Die vier zunächst gescheiterten Alt-Chunkingtests benötigten den im neuen Worktree noch fehlenden öffentlichen Tokenizercache; vorhandene Cachebytes lokal hashgleich wiederverwendet, dann komplette öffentliche Suite erneut ausgeführt. Keine Gateänderung, kein Netzwerk-/Modelldownload. Die Reserve war für diesen Lauf wieder ausreichend; kein Stabilitätsbeweis für die private 16-GiB-Exportsuite.

Finaler Chrome-Lauf am korrigierten Layout ebenfalls PASS (Antwort, Follow-up, Dialog/Escape, Abstention, Viewports 360/768/1440/640, Experten-/Validation-Navigation, keine page_errors). Synthetische Antwort bis DOM ca. 0,105 s in einem Lauf. Reviewvollworkflow weiterhin technisch per API geprüft; keine echte Human-/Screenreader-/native Zoom-Abnahme behauptet. Öffentliche Receipts: CONVERSATIONAL_CHECKS.json. Ein Screenshot mit synthetischen Daten zeigt die Oberfläche unter screenshots/; er ist kein Usabilitybeweis.
