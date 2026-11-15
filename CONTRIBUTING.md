# Mitwirken

Beginne mit README, REPRODUCING, PUBLICATION_SCOPE und dem tatsächlichen Branch-/Commitstand. Verwende eigene Compute-Ressourcen; der Offline-Testpfad benötigt keine Anbieterzugänge. Arbeitspakete stehen in den vorhandenen Issues #9–16. Prüfe vor Übernahme, ob der öffentliche Umfang die benötigten Komponenten und Inputs enthält.

#9 betrifft Onboarding; #10 unabhängige Annotation; #11 synthetische fachliche QA. #12 soll die vorhandene Relevance-/Abstention-Implementierung prüfen, keine zweite bauen. #13 braucht identische freigegebene Daten/Splits; #14 den tatsächlich verfügbaren Provenienzvertrag. Fehlende private Inputs nicht rekonstruieren, keine neuen Benchmarks oder Crawls zur Umgehung starten.

Erstelle einen kleinen Featurebranch und einen PR mit konkretem Problem, Änderungen, Commit-/Fixture-IDs, ausgeführten Befehlen, echten PASS/FAIL/SKIP und Grenzen. Keine direkten main-Änderungen, kein selbstständiger Merge. Syntax/Lint, Offline-Tests und Negativfälle für die betroffene Schnittstelle müssen erhalten bleiben. Gates nicht senken und Assertions nicht für einen grünen Lauf entfernen.

Echte Quellen benötigen überprüfte Rechte. Keine Original-PDFs, Volltexte, Zugangsdaten, personenbezogenen Reviewer-IDs oder Reviewexports einchecken. Synthetische Fixtures müssen als erfunden gekennzeichnet sein; Plan/Prognose/Messung und fehlende/negative Evidenz unterscheiden. Agentische Bewertung ist kein unabhängiges Gold. Wissenschaftliche Aussagen brauchen Studie, Snapshot, Nenner, Expositionsstatus, Abhängigkeitsstruktur und prüfbares Artefakt.

Die projektspezifischen Arbeitsabläufe stehen unter `.agents/skills/`. Für kostenpflichtige Inferenz braucht es ein eigenes ausdrückliches, messbares Budget; die CI führt keine Inferenz aus.
