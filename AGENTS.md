# Verbindlicher Vertrag für diesen öffentlichen Snapshot

Arbeite im vorhandenen Scope. Lies README, PUBLICATION_SCOPE, DATA_POLICY, REPRODUCING und PUBLICATION_REPORT vor Änderungen. Forschungsfragen RQ1–RQ8 und Hypothesen H1–H6 bleiben erhalten. Keine neue Grundlagenphase, Architekturinitiative, Massencrawls oder Graph-Rebuilds ohne konkreten Auftrag. Vor Änderungen Branch, HEAD und Working Tree prüfen; vorhandene Änderungen und private Entwicklung bewahren.

Lokale technische Änderungen, Tests und Commits sind erlaubt. Push, PR-Publikation, externe Kommunikation, Deployment, produktive Systeme und Datenfreigaben benötigen einen entsprechenden Nutzerauftrag; ein früherer Upload ersetzt keine Freigabe späterer Daten. Kein Force-Push, Merge, privater Corpus oder ungeprüfte Commit-Historie. Aktuelle Nutzeranweisungen gehen vor.

Verwende `.agents/skills/research-integrity/SKILL.md` für wissenschaftliche Aussagen und `.agents/skills/preview-release/SKILL.md` für reproduzierbare Snapshot-Prüfung. Gewöhnliche Schritte autonom abarbeiten; unklare Rechte nur am betroffenen Artefakt stoppen, unabhängige Aufgaben fortsetzen.

## Technische Regeln

- Ein expliziter Datenbankpfad gilt für die gesamte Operation. Bei fehlender DB keine implizite Ersatzdatenbank anlegen.
- Quellen-ID, Originalversion/Hash, Gemeinde, Unit-/Chunkherkunft, Modell/Prompt und Belegspanne durch Extraktion, Graph, Retrieval und Citation erhalten.
- Text2Cypher nur mit eigener read-only Rolle, Allowlist, Zeit-/Ergebnislimits; Mutationen und unbekannte Prozeduren ablehnen.
- Schema, Provenienz, Modell-/Indexdimensionen und Vorgängerstufen vor Verarbeitung prüfen. Teilstände/Fehler sichtbar lassen.
- Run-Metadaten müssen Inputs, Konfiguration, Kosten und tatsächlichen Status binden. Keine fehlgeschlagenen Inputs als fertig markieren.
- Testdaten bleiben isoliert, ohne echte Credentials/Produktionsdatenbank. Reservegates und Assertions unverändert lassen.
- Generierte Modelle, Corpus, Exporte und Logs bleiben ignoriert. Nur bewusst freigegebene kleine Fixtures/Artefakte mit Herkunft und Hash versionieren.

## Wissenschaft und Abschluss

Technische Ausführung, explorative Bewertung und unabhängige Fachvalidierung strikt trennen. Kein künstliches Gold und keine allgemeine GraphRAG-Überlegenheit. Fehlende Evidenz ist kein Beweis von Nichtexistenz; Quelle ist nicht automatisch antworttragend; Graphpfad beweist keine Kausalität. Original-RQ- und lokale Preview-IDs nicht vermischen. Exposition und verbundene Quellenfamilien bei Splits berücksichtigen.

Vor Commit vollständigen Diff und Secrets-/Rechteumfang prüfen, passende Tests ausführen und betroffene Dokumentation aktualisieren. Echte Testergebnisse, ausgelassene Prüfungen und konkrete Grenzen melden. Keine Attributionstrailer hinzufügen. Kein stilles Relabeln historischer Befunde.
