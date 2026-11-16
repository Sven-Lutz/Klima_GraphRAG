# Conversational Research V2 — verbindlicher Arbeitsstand

Stand 16.11.2026. Basis 0483eef9d8bb9c37559d8dcee0e0b06449d9a394, PR #18 remote bestätigt (offen, Draft, gegen sven-develop). V2 arbeitet auf feat/conversational-research-v2. RQ1–RQ8, H1–H6 und T1–T9 bleiben erhalten. Dies ist ein Engineeringprogramm, keine neue konfirmatorische Studie.

## Ist-Architektur

Browser (Vanilla JS/CSS, lokales Cytoscape) → FastAPI conversation.py → ConversationEngine → ResearchApplicationService.execute(QueryOptions) → kanonisches Retrieval/CitationRegistry/Snapshot. SQLite hält sitzungsgebundene Turns mit Elternversionen. Separater append-only ReviewProtocol hält individuelle Urteile und Adjudikation; Produktfeedback bleibt getrennt. Lokale Provideradapter existieren in core/answer_generation.py. grounded_provider.py importiert indirekt E1-abhängige Workspacekomponenten und ist daher keine freigegebene öffentliche V2-Integration.

Öffentliche Standardkomposition: zwei erfundene Passagen, kontrollierte Vektoren/Graphrelationen, deterministische extraktive Antworten. Kein echter kommunaler Recherchebetrieb. Private Worktrees und Forschungsergebnisse werden weder übernommen noch verändert.

## Lücken und priorisierte Umsetzung

| Priorität | Lücke | Umsetzung / Abnahme |
|---|---|---|
| P0 | Scope erst nach Top-k gefiltert | Reale Adapter mit Scope binden; Fixture unterstützt nur dokumentierten Postfilter; negative Regression |
| P0 | Freitextorte können unbemerkt mit altem Scope kollidieren | strukturierter versionierter Zustand; Ortswechsel fail-closed |
| P0 | Modellverfügbarkeit und Qualifikation vermischt | Registry mit vier getrennten Rollen; keine automatische Aktivierung |
| P1 | Anschlussfragen verlieren Maßnahmenbezug | gebundene vorherige Frage/Beleg-IDs, sichere Deixis, Ereignisprotokoll |
| P1 | kein eigener Grapharbeitsraum | Antwortgraph, Suche/Filter/Layout, Evidenznavigation und Tabelle |
| P1 | kein Visualisierungsvertrag | validierte beleggebundene Datensätze, Scope/Einheit/Modalität, Tabelle/Export |
| P1 | kein zentraler Status | Settings mit Snapshot, Modellrollen und IE-Lifecycle |
| P2 | Reviewerergonomie | vorhandenen Protocol erhalten; keine neue Goldlogik |
| Gate | kommunaler Vollcorpus | E1-/Originalrechte prüfen; keine Umgehung |
| Gate | generative Qualität | vorhandene lokale Gewichte inventarisieren; Qualifikation und Ressourcen separat |
| Gate | Human Gold / UX | echte unabhängige Personen; kein automatischer Ersatz |

## UX-Konzept und PRD

Primäre Journey: Frage → Antwort/Teilantwort/Rückfrage → Inline-Beleg → optional Graph/Diagramm → Originalprüfung. Wissenschaftliche Journey: Research-Trace/Export und aktive Einstellungen prüfen. Reviewer: eigener Login → zugewiesene Queue → Aussage/Original → dimensionsspezifisches Urteil → nächster Fall. Administration bleibt rollenbegrenzt im bestehenden Protocol.

Informationsarchitektur: Chat, Research Explorer, Knowledge Graph, Visualizations, Validation, Data Coverage, Settings. Graph und Charts beziehen sich zunächst ausschließlich auf den ausgewählten Antwortstand; fehlende Daten werden als Leerzustand dargestellt. Keine Vollgraph- oder Bayern-Vollständigkeitsbehauptung.

Wireframe Desktop: Navigation oben | Unterhaltungen links | Gespräch mit separater Composerzeile | eigene Arbeitsräume für Graph/Chart/Settings. Mobil: umbrechende Navigation, kompakte Chatliste, volle Breite, zugängliche Tabellen und Dialoge. Farben: Text #17342e, Hintergrund #f5f7f4, Akzent #165941, Fokus #98500c; Systemschrift, 16px, große beschriftete Bedienelemente. Komponenten: Button, Formularfeld, Statusregion, Antwortkarte, Citationbutton, nativer Dialog, Details, Tabelle. Keine ungeprüfte HTML-Ausgabe. Ladezustand benennt Retrieval statt künstlichem Streaming; Fehler erlauben Wiederholung, Leere erklärt fehlende Evidenz. Native Dialoge/Escape, sichtbarer Fokus und Skiplink; WCAG 2.2 AA bleibt Ziel ohne Zertifizierung.

## Ressourcen und Grenzen

MacBook Air M3 / 8 GB laut Owner. Am Beginn rund 16 GiB freier Datenträger, vm_stat zeigt hohe Kompression; sysctl/ps im Sandboxkontext nicht verfügbar. Kein Forschungsserver/Ollama-Listener beim initialen lsof. 15/16-GiB-Gates bleiben erhalten. Keine parallelen Modellprozesse, keine Downloads, keine externen kostenpflichtigen Requests. Lokale private Daten werden nur lesend inventarisiert, keine Migration/Neuindizierung.

## Reproduktion und Abnahme

`.venv/bin/python -m crawler.web --fixture --directory generated/chat-v2 --port 8825`

Die Fixture ist synthetisch. Testfragen: „Welche Solarprojekte planen die Stadtwerke?“, „Wie viel Leistung?“, „Wer plant die Solaranlage?“, „Welche wurden bereits umgesetzt?“. Eine fehlende Passage ist kein Beweis fehlender Maßnahmen. Keine echten kommunalen Antworten aus diesen Daten ableiten.

Engineering, Answer Quality, UX, Corpus Integration, Review Workflow, Human Gold, Reproducibility, External Preview und Paper Readiness werden im QA-Bericht separat geführt. Automatische Tests liefern keine unabhängigen fachlichen Labels. Historische Befunde und Register bleiben unverändert; neue Interventionen heißen V2 und bleiben development-exposed.
