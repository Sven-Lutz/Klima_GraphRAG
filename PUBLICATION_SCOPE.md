# Geprüfter Veröffentlichungsumfang

Basis ist ausschließlich die öffentliche `origin/main`-Historie bei `7e253e59ce0abc07d765b7e72374d0483ccb7bc2`. Aktuelle geprüfte Quelltexte stammen aus dem lokalen Snapshot `213ece365633eef0201624614413c3932f206b68`; dessen Commits und ungeklärte Datenhistorie werden nicht übertragen. Der bestehende private Checkout bleibt unverändert.

## Enthalten

Eigenständiger Projektcode aus dem vorhandenen PROPOSED_PUBLIC_CODE.tsv, eigene wissenschaftliche Dokumentation, RQ-/Protokolltexte, synthetische Fixtures, Hash-/Lizenz-/Reproduktionsmetadaten und zwei geprüfte Aggregate. Die Datei PUBLIC_FILES.tsv bindet den tatsächlich vorbereiteten Nutzdateiumfang per SHA256. Metadatenberichte/Manifest selbst sind zur Vermeidung zirkulärer Hashes aus diesem Nutzdateimanifest ausgenommen; Git bindet den gesamten Commit.

Cytoscape.js hat ein explizites eingebettetes MIT-Copyright, dessen separate Lizenz mitgeliefert wird. Eigene Python-/JS-/CSS-/HTML-Quellen wurden auf Secrets, lokale Personen-/Kontaktangaben, fremde Payloads und Copyright-/Importhinweise geprüft. Ein echter Hochschul-Mailkontakt im alten Registry-User-Agent wurde durch eine neutrale Konfigurationsaufforderung ersetzt. Literal-Credentials in Tests sind ausschließlich bewusst erfundene adversarielle bzw. Disposable-Fixtures, keine Betriebszugänge. Öffentliche Abhängigkeiten werden separat mit ihrer jeweiligen Lizenz installiert.

## Konkret zurückgestellt

- Sechs Pythondateien unter `research/experiments/e1_selector/vendor/` und `research/experiments/e2_offline_20261024/kit/vendor/`: aus lokalem E1-Kit importiert, ohne belegte Lizenzfreigabe. Auch importierte `test_vendor_contracts.py` bleibt zurückgestellt. Keine Umdeklaration unter Root-MIT.
- Konsequenz: `crawler.web.local_index`, `research.bavaria.workspace.core`, E1-Passageadapter und abhängige Research-/Retrievalpfade sind öffentlich **nicht vollständig ausführbar**. Vorhandener Code ist ein dokumentierter Entwicklungsstand, keine lauffähige Vollcorpus-Webdemo. Die betroffenen Tests bleiben als Quelltexte nachvollziehbar, werden aber nicht als bestandene öffentliche Suite ausgegeben. Owner muss die Rechte am E1-Kit belegen oder diese Komponente separat klären; kein Ersatzalgorithmus wurde zur Umgehung eingeführt.
- Corpus-/Index-/Originalarchive, kommunale Texte, echte Referenz-/Human-Reviewpakete, persönliche Konten und Providerzugänge: keine geprüfte Weitergabegrundlage bzw. Privacygrenze. Keine Kopie aus `generated`, Downloads, Review-Stores oder ungetrackten Arbeitsdaten.
- Historische Ergebnisdateien mit Quellenauszügen und umfangreiche Studienberichte: nur die explizit gelisteten numerischen Aggregate übernommen; vollständiger Originalstudien-Replay nicht öffentlich zugesagt.
- Bereits öffentliche Registry-DB/Seed-CSV fehlen im aktuellen Baum mangels hier überprüfter Datenlizenz. Öffentliche main-Historie bleibt unverändert; es wird keine rückwirkende Löschung behauptet.
- Alter `tests/test_graph_export.py` aus main wurde entsprechend der bereits vorhandenen lokalen API-Ablösung nicht übernommen. Die kanonischen Neo4j-Provenienz-/Projektionsverträge sind enthalten; keine Assertion wurde für Grün verändert.

EXCLUDED_FILES.tsv dokumentiert die exakten nicht übernommenen lokalen getrackten Pfade und Gründe. Private ungetrackte Daten werden als ganze lokale Bereiche zurückgestellt und nicht durch Veröffentlichung ihres Inhalts inventarisiert. PUBLIC_TEST_EXCLUSIONS.tsv listet fehlende private Inputs und ungeklärte Importabhängigkeiten; öffentliche CI ist ausdrücklich ein begrenzter Inputvertrag, kein vollständiger privater Release-PASS.
