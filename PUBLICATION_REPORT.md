# Scientific Develop Publication — geprüfter Kandidat

Öffentliche Basis `7e253e59ce0abc07d765b7e72374d0483ccb7bc2`; Quellimport `213ece365633eef0201624614413c3932f206b68`. Isolierter Worktree/Branch `sven-develop`, keine private Historie übernommen. Der Commit ist über `git rev-parse HEAD` prüfbar.

## Tatsächlich ausgeführte Prüfungen

- Frische Python-3.11-Umgebung: öffentliche PyPI-Installation PASS; konkrete Versionen in requirements-publication.lock. Tokenizer öffentlich vorbereitet, keine Inferenz.
- Erster kompletter Testversuch: **21 Collection-ERROR**, darunter fehlende E1-Lizenzkomponente, ein inzwischen entfernter main-API-Test und fehlendes PyYAML. Letztere zwei Differenzen im Kandidaten korrigiert; keine Assertions verändert.
- Unabhängiger Teilversuch: **373 PASS, 20 FAIL, 12 ERROR, 6 SKIP**. Sämtliche hier beobachteten FAIL/ERROR sind fehlende private Eingaben, die ungeklärte E1-Importabhängigkeit oder die noch nicht übernommene Disposable-Neo4j-Composefixture. Letztere ist nun geprüft enthalten. Kein Software-PASS aus einem fehlgeschlagenen Test abgeleitet.
- Expliziter öffentlicher Inputumfang nach Dependency-Prüfung: **380 PASS, 6 SKIP**, 5,37 s; fünf Neo4j-Livefälle nicht aktiviert, ein optionales archiviertes Original nicht verteilt. **Dies ist nicht die gesamte private Owner-Suite.** Alle Ausschlüsse stehen in docs/research/PUBLIC_TEST_EXCLUSIONS.tsv; Quelltests bleiben nachvollziehbar.
- Ruff über crawler/demo/tests/docs PASS; AST-Compile aller publizierten Pythonmodule PASS. Paket-Scorer noch ausgeschlossen; kein erfolgreicher vollständiger Web-/Retrievalimport behauptet.
- Gitleaks über den vorbereiteten Verzeichnisinhalt: **0 Findings**. Hashvergleich mit dem vorgeschlagenen Ausgangsmanifest geprüft; abweichend sind bewusst die neue README, Dependency-Ergänzungen, neutrales Registry-Kontaktfeld und Importformatierung.
- Studienaggregate: Hash-/Nenner-/F1-/SQL-Graph-Äquivalenzprüfung PASS. DS-006 synthetisch tatsächlich ausgeführt: A 7/10 und 0/5; B 10/10 und 4/5; 13/29 Chunks, 3532/3841 Token. Historische abweichende Parserergebnisse nicht überschrieben.
- Zweiter unabhängiger Clean Checkout von `c2cb2005e3b221b19161121c3b39dc7fa07a342a`: **PASS** für frische Installation der fixierten öffentlichen Dependencies, `pip check`, 448 Nutzdateihashes, Ruff, Compile und wissenschaftliche Aggregatprüfung. Öffentliche Suite **380 PASS / 6 SKIP**, 5,79 s; eine Starlette/httpx-Deprecation-Warnung. DS-006 neu ausgeführt: identische semantische Matrix und Zähler, Timing darf abweichen.
- Komplette Owner-Collection im Clean Checkout: **417 Tests gesammelt, 19 Collection-ERROR**, Exit 2; sämtliche aktuellen Collectionfehler sind die fehlende, ungeklärte E1-Vendor-Importabhängigkeit. Die komplette Suite wurde dadurch nicht ausgeführt und erhält keinen PASS. Private Eingabeabhängigkeiten bleiben darüber hinaus bestehen.
- Die damalige Clean-Checkout-Messung hatte 16.547.840.000 Bytes frei (~15,41 GiB), ausreichend für den tatsächlich ausgeführten begrenzten Umfang, aber unter dem 16-GiB-Exportgate. Keine Stabilitäts-/Vollreleasebehauptung aus historischen 621 PASS. Tests und Mindestreserven unverändert.
- Neue Abschlussmetadaten ändern den getesteten Source-/Fixture-/Dependency-Inhalt nicht. Nachfolgecommit ist am Report-Commit per Git prüfbar; dessen exakter Baum wird vor Push nochmals gegen dieses Nutzdateimanifest geprüft. Öffentliche Belege: docs/research/PUBLIC_CHECKS.json. Hosted GitHub Actions: zum Berichtzeitpunkt **NOT_RUN**; Status nach Push separat prüfen.

## Wissenschaft und Grenzen

RQ1–RQ8/H1–H6/T1–T9 bleiben erhalten. Historische RQ-IDs sind keine Preview-Test-IDs. Veröffentlichte IE-/Relationszahlen stammen aus eigenen numerischen Aggregaten; Inputs/Labels bleiben vorläufig, exponiert und nicht unabhängig human-validiert. Aggregatprüfung ist kein vollständiger Originalstudien-Replay. Fehlende historische Code-SHAs werden ausdrücklich als unbekannt geführt. Kein allgemeiner GraphRAG-Vorsprung, keine neue Gesamtaccuracy, keine populationsweite Inferenz.

CONTRIBUTOR_READY = PARTIAL: der explizite synthetische Inputumfang funktioniert; Research-/Retrievalpfade mit ungeklärtem E1-Import und private Inputtests bleiben eingeschränkt. SCIENTIFIC_VALIDATION_STATUS = NOT_INDEPENDENTLY_VALIDATED. Externe OTP-/Browserabnahme = NOT_TESTED, kein Demo-Deployment.

## Veröffentlichung und Entscheidungen

Der aktuelle Nutzerauftrag autorisiert die Veröffentlichung geprüfter Dateien nach sven-develop sowie einen Draft-PR; die alte operative Pushsperre gilt nicht als Widerruf dieses konkreten Auftrags. Ungeklärte Dateien bleiben ausgeschlossen. Vor Upload wird ausschließlich der exakte neue Snapshot ohne private Historie geprüft. main wird nicht verändert.

Offen: Nachweis der Rechte am lokal gelieferten E1-Kit für sechs Vendor-Dateien und importierte Verträge; Freigabe weiterer Quellen-/Reviewartefakte nur für eine spätere Erweiterung. Unabhängige Annotation/Adjudikation und echte externe Zugangsabnahme bleiben separate wissenschaftliche bzw. operative Aufgaben, keine Voraussetzung für den hier begrenzten Code-/Aggregat-Snapshot. Keine kostenpflichtigen Requests, Crawls, Graph-Rebuilds, Merge oder Demo-Veröffentlichung.

## Hosted-CI-Korrektur

Erster PR-Lauf [43729293811](https://github.com/Sven-Lutz/Klima_GraphRAG/actions/runs/43729293811): **FAIL**, Exit 4 bei Testauswahl. Ein aus macOS übernommener parametrischer Node-ID enthielt EADDRINUSE=48 statt Linux=98. Nur der Selektor in tests/public-tests.txt wurde auf die gesamte vorhandene Testfunktion umgestellt; beide unveränderten Parameterfälle werden ausgeführt. Keine Assertion oder fachliche Prüfung entfernt. Neuer Hosted-Lauf nach diesem Commit wird separat ausgewiesen.


## Conversational V2 delta (16.11.2026)

Public V1 base 0483eef9d8bb9c37559d8dcee0e0b06449d9a394. Architecture commit 729f55c; implementation d951d7e plus generation-audit binding 1e75042. Separate V2 own-code/MIT-library scope; no E1, original corpus, model weights, real accounts or Human labels. Canonical current limitations/results: docs/product/V2_QA_REPORT.md and V2_CHECKS.json. Historical intermediate receipt (superseded by final source receipt below): prior local full snapshot 414 PASS / 6 SKIP; latest full attempt 400 PASS / 7 FAIL / 8 ERROR / 6 SKIP at unchanged disk reserve gates. Current pure V2 contracts 22 PASS / 3 store cases deselected. No current complete local PASS, running server, qualified LLM or full programme completion. Hosted CI is checked separately after the authorized feature push. Historical numbers above remain historical.


V2 Hosted-Zwischenstand: Run 44 am a324496 hat die saubere Linux-Installation, 422 PASS / 6 SKIP, statische/wissenschaftliche Checks und V1-Chrome bestätigt, scheiterte aber an der kleinen V2-Displayhöhe. Be0daf7 korrigiert das Layout; isolierte statische Layoutprüfung 18/18 PASS. Aktuelle reine V2-Verträge 23 PASS / 3 Storefälle abgewählt. Neuer Gesamt-Hosted-Status wird separat ausgewiesen; kein Grün aus dem fehlgeschlagenen Run abgeleitet.


V2 Ressourcenwiederherstellung: der lokale Server läuft wieder ohne Gateänderung. Integrierter öffentlicher Lauf 427 PASS / 6 SKIP (12,54 s), vollständiger Chrome-Workflow einschließlich geöffneter mobiler Original-/Auditansicht PASS. Eine weitere atomare Löschregression besteht; finale Clean-Checkout-/Hosted-Nachweise folgen getrennt. Neuer Reviewer-Queuepfad ist paginiert, per-unit Enums bleiben getrennt, und 400 synthetische Objekte ohne Urteile wurden auf demselben unveränderten DB-Hash mit identischem Queueergebnis geprüft. Keine neue fachliche Accuracy, kein Human-Gold- oder Graphformatvorteil.

Final source 00847c1: 431 PASS / 6 SKIP (7.82 s), zero failed/error; final V2 Chrome workflow PASS. Prior storage and hosted failures remain historical negative receipts, not the current local state. Clean reproduction and final hosted status are reported separately. No LLM inference, independent human labels or public original-corpus integration.


## Research Workbench reorientation — current delta

P0 session recovery, primary method choice/compare and account-free local notes implemented on V2. E1 code origin clarified by owner statement; six original code files restored with exact hashes and provenance record, no relabelled replacement. Real existing municipal corpus, FTS/dense indexes and resident ONNX encoder run locally read-only; no source/index changes, rebuild, model download or public original-data upload. Public suite 439 PASS / 6 SKIP (10.43 s); fresh source checkout checked separately. Synthetic and actual private real-source Chrome journeys pass, including owner-scoped hash-verified archived original; zero real human labels. Native graph extra-information confound explicit; no winner or independent answer quality inferred. Public source-only scan of this delta: 328,181 bytes, zero leaks. Current detailed acceptance: WORKBENCH_REORIENTATION.md and V2_QA_REPORT.md. Private real outputs never included in CI artifact paths. Final head CI is linked in PR19, without circular metadata commits.

Workbench test-boundary re-audit: 197 formerly import-blocked technical cases reinstated after actual execution; 12 concrete missing-CANDIDATE_FACTS cases remain excluded. Expanded selection **636 PASS / 6 SKIP (23.59 s)**; raw audit **197 PASS / 12 FAIL**, no assertion weakening. Pinned Protego 0.7.0 added only for reproducible imports/fixtures, no crawl. Final public-only fresh environment and head CI are separately reported.

Fresh public-boundary correction: the intermediate 636-owner run depended on five unchanged BM25 tests reading private-only git object b84db20. Fresh run: 631 PASS / 5 FAIL / 6 SKIP (21.80 s). Those five exact node IDs now have an explicit nonpublic-history exclusion; no private history/function substituted or published, no assertion weakened. Self-contained reinstated cases: 192. Current final public denominator is reported separately below.

Current final public selection at 5bd258e: **631 PASS / 6 SKIP**, fresh public-only venv **20.47 s**, owner environment **23.14 s**. Static/compile/pip checks PASS. This supersedes the intermediate 636-owner-only boundary; failure receipts remain above.
