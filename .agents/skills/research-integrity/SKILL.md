---
name: research-integrity
description: Prüft wissenschaftliche Aussagen und Referenzherkunft in Klima-Research-Ergebnissen.
---
# Research integrity
1. Bestehendes Forschungsprotokoll und zugehörige Ergebnisdateien lesen; keine neuen Forschungsfragen oder Goldlabels erfinden.
2. Jede Aussage einer Klasse zuordnen: technische Ausführung, exploratives Engineeringurteil oder unabhängige fachliche Validierung. Herkunft und Exposition der Fragen nennen.
3. Für empirische Zahlen Commit, Corpus-/Indexversion, Split, Methode, k, Nenner und Ergebnisartefakt angeben. Verfügbarkeit/Latenz sind keine Relevanz-/Antwortmetriken. Fehlende unabhängige Qrels bleiben NOT_EVALUATED.
4. Claim, Originalspanne, Quellenhash, Gemeinde und Zeitbezug prüfen. Prognose, Planung und gemessene Wirkung trennen. Nichtfund bedeutet keine Nichtexistenz; Graphpfad keine Kausalität.
5. Reviewer-Unabhängigkeit, zwei Ersturteile, erforderliche Adjudikation und Auditbindung prüfen. Synthetische Stores bleiben technical=true und human_gold=false nach jedem Export.
6. Holdout und eingefrorene Studien unverändert lassen. Keine Überlegenheit von GraphRAG gegenüber BM25, Dense, Hybrid oder strukturierten Verfahren ohne passende unabhängige Vergleichsdaten behaupten.
7. Abweichungen mit konkretem Artefakt und begrenzter Aussagekraft im Releasebericht dokumentieren; keine fehlenden Zahlen ergänzen.
