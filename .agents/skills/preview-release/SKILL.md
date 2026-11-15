---
name: preview-release
description: Prüft den begrenzten öffentlichen Klima-Forschungssnapshot ohne private Inputs.
---
# Reproduzierbarer Snapshot
1. Branch, HEAD, Dirty-Diff und öffentliche Basis prüfen. Keine private Historie übernehmen.
2. README, PUBLICATION_SCOPE, DATA_POLICY und Forschungsstatus lesen. Unklare Artefakte ausschließen, keine Gates senken.
3. Exakten Snapshot in leeres Verzeichnis auschecken; frische Umgebung nach REPRODUCING installieren und Tokenizercache explizit vorbereiten.
4. Datei-/Hashinventar, Secrets, Lizenzhinweise, Ruff, synthetische Tests und Reproduktionsbelege prüfen. Private Eingabeabhängigkeiten separat benennen.
5. PASS/FAIL/SKIP/NOT_TESTED mit Befehlen, Runtime und tatsächlichem Commit dokumentieren. Alte 621 PASS sind kein aktueller Stabilitätsnachweis.
6. Neo4j-Livebetrieb, externe HTTPS/OTP-Abnahme und unabhängige Annotation bleiben getrennte Gates. Keine Demo-URL erfinden.
7. Diff vor Commit vollständig prüfen. Veröffentlichung nur im autorisierten Zielumfang; kein Merge oder Deployment.
