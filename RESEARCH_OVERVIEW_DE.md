# Forschungsüberblick: kommunale Klimaschutzinformationen

Publikationsedition der vorhandenen Übersicht vom 15.11.2026. **Explorativer Forschungsprototyp; nicht unabhängig fachlich validiert.** Forschungsfragen und Hypothesen werden unverändert weitergeführt; keine neue Grundlagenstudie.

## Problem und Ansatz

Informationen liegen verteilt in Webseiten, Beschlüssen, Berichten und PDFs. Ein passender Suchtreffer beantwortet noch nicht, für welche Gemeinde eine Aussage gilt, ob sie aktuell ist oder ob sie Planung, Prognose, Umsetzung oder Messung beschreibt. Beispiel aus den ausdrücklich erfundenen Alpha-Fixtures: Ein Entwurfsdatum belegt nicht den Abschluss eines Wärmeplans. Quellenhost, Herausgeber, Akteur und sachlicher Geltungsbereich sind getrennt zu prüfen.

Retrieval-Augmented Generation (RAG) sucht Belege, übergibt sie einer Antwortkomposition und bindet Aussagen an Quellenstellen. Eine auflösbare Citation-ID belegt die Referenzidentität, aber nicht automatisch Relevanz oder Wahrheit. Fehlt eine antworttragende Stelle, muss eine Evidenzlücke sichtbar bleiben.

## Alternativen und Hypothese

BM25 gewichtet Wortvorkommen; Dense Retrieval vergleicht Vektorrepräsentationen. Hybrid kombiniert Ranglisten. Structured Retrieval fragt typisierte Felder etwa mit SQL ab. GraphRAG nutzt Entitäten, Relationen und Pfade, um möglicherweise verteilte Belegketten zu erschließen. Keines dieser Formate garantiert fachliche Korrektheit. Zusätzliche kuratierte Relationsinformation darf nicht dem Graphformat allein zugerechnet werden.

Die entwickelte lokale Architektur kombiniert begrenzte Quellenakquisition, versionierte Originale, HTML/PDF-Konversion, Segment-/Chunkprovenienz, Information Extraction, SQLite-Evidenz, optionale Graphprojektion, verschiedene Retrievalwege und konservative Antwort-/Zitationsprüfung. Browser- und Reviewkomponenten dienen der technischen Untersuchung. Die öffentliche Verfügbarkeit einzelner Funktionen ist enger als die lokale Architektur; siehe [Veröffentlichungsumfang](PUBLICATION_SCOPE.md).

Knowledge Graphs könnten Rollenketten wie „Organisation erstellt Studie für Projekt“ zugänglich machen. Dafür müssen die Kanten samt Belegspanne, Gemeinde und Zeit stimmen. Ein Graphpfad beweist keine Kausalität. Fehlende Kanten oder nicht gefundene Texte beweisen kein fehlendes kommunales Handeln.

## Erkenntnisse und Aussagekraft

Die veröffentlichten [Befunde](RESEARCH_FINDINGS_AND_LIMITATIONS.md) trennen synthetische Verarbeitungskontrollen, explorative Feldextraktion und relationale Retrievalvergleiche. Der SQL-/Graphvergleich hält Relationen und formale Querymuster gleich. Die Abstände zu Textretrieval verändern dagegen zugleich Information oder Formalisierung; sie identifizieren keinen allgemeinen Graphformatvorteil. Schemavalidität garantiert keine semantisch richtige Feldextraktion.

Die übrigen vorhandenen DS-/E2-/M4-/M5- und Produktstudien bleiben eigene Untersuchungen. Wo private Originale oder nicht freigegebene Referenzen nötig sind, wird kein öffentlich reproduzierter Befund behauptet. Technische Tests, agentische Entwicklungsurteile und unabhängige Humanannotation bleiben verschiedene Evidenzklassen.

## Grenzen und nächste Schritte

Exponierte Entwicklungsfälle, unvollständige Relevanzreferenzen, gemeinsame Dokumentfamilien, kleine Kohorten und veränderte Informationszugänge begrenzen Generalisierungen. Es gibt keine unabhängig belegte allgemeine GraphRAG-Überlegenheit und keine extern abgenommene Live-Preview. Ein technisch funktionierender Adjudikationsworkflow ist kein unabhängiger Goldstandard.

Nächste Forschung: unabhängige Original-first-Annotation und Adjudikation, methodenblind gepoolte Belegbündel und anschließend gepaarte Vergleiche von BM25, Dense, Hybrid, SQL und Graph unter gleichen Corpus-, Informations- und Budgetbedingungen. Repräsentation, Informationsanreicherung und Query-Formalisierung sind getrennte Faktoren. Contributor beginnen mit [CONTRIBUTING](CONTRIBUTING.md) und den vorhandenen Issues #9–16.
