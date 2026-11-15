# Studiengetrennte Befunde und Grenzen

Publikationsedition vorhandener Studien, keine neue Modell-/Retrievalauswertung. **NOT_INDEPENDENTLY_VALIDATED.** Nicht alle privaten Originalartefakte können hier verteilt werden. Zahlen unten sind gegen die ausdrücklich beigefügten Aggregate geprüft; Aggregate sind keine unabhängigen Labels und ermöglichen ohne private Inputs keinen vollständigen Replay der Originalexperimente. Ihre Herkunft, Originalhashes und fehlenden historischen Codebindungen stehen im [Artefaktregister](docs/research/ARTIFACTS.json).

## S1 – Synthetische Verarbeitungskontrolle (RQ2/RQ3)

DS-006-Fixtures und Ausführungscode sind öffentlich enthalten. Sie prüfen, ob definierte erfundene Belegstellen Parser, Admission, Chunking und Attribution überstehen. Eine spätere Parserreparatur verändert die heutigen Kontinuitätsergebnisse; deshalb werden historische Zahlen nicht als aktueller Replay ausgegeben. Aktuelle tatsächlich ausgeführte Ergebnisse stehen im PUBLICATION_REPORT und im synthetischen Reproduktionsartefakt. „Held out“ ist hier die ursprüngliche Fixturebezeichnung, kein unabhängiger kommunaler Testdatensatz. Mehr Text kann mehr Evidenz und zugleich mehr Distraktoren aufnehmen. Dies misst keinen Realwelt-Recall.

## B2 – Explorative Feldextraktion, gemeinsame acht Fälle (RQ3)

[Öffentliches Zahlenextrakt](docs/research/artifacts/ie-1.7b-summary.json) aus dem vorhandenen stronger-ie01-Report: acht Quellenfälle/acht dokumentierte Komponenten, 56 abhängige Feldslots, davon 27 positive und 29 Unknown-Slots. Keine unabhängige Humanannotation. Die genaue damalige Code-SHA ist aus diesem Aggregat allein nicht belegbar; der Import-Snapshot ist ausdrücklich kein Experimentcommit. Vollständiger Modellreplay ist hier NOT_REPRODUCIBLE_FROM_PUBLIC_INPUTS.

| Arm | TP | FP | FN | F1 = 2TP/(2TP+FP+FN) |
|---|---:|---:|---:|---:|
| Regeln | 2 | 1 | 25 | 0,133 |
| Qwen3:1.7B | 16 | 40 | 11 | 0,386 |
| Hybrid mit 1.7B | 5 | 5 | 22 | 0,270 |

Diese Feldslots sind keine 56 unabhängigen Untersuchungseinheiten. Unknowns und positive Felder haben unterschiedliche Bewertungsrollen. Schema-gültige Ausgabe ist keine semantische Zertifizierung. Keine allgemeine Modellrangfolge oder GraphRAG-Überlegenheit folgt daraus. Andere Modellkohorten werden nicht hinzugemittelt.

## B3 – Graphbestand und gleichinformierte relationale Abfragen

**RQ3/RQ5, Bezug zu H5.** [öffentliches, unverändertes Aggregatreport](docs/research/artifacts/retrieval03-report.json); Relationsfreeze und Analyseinputs bleiben privat. Corpus-ID des Retrievalreports: `a8e5ac8c83348e6481eb44469a40d0ae78f29d26694c19e97993a67373fb6280`.

108 vorläufige Relationen, 35 Quellfassungen, 38 Fragen, **18 konservative Abhängigkeitskomponenten**. Nicht alle Komponenten sind unabhängig gezogen oder fachlich validiert. SQL und Graph liefern auf allen 38 formalisierten Fragen identische Relationsmengen. Das ist ein technischer und kohortenspezifischer Äquivalenzbefund. Die Queries verwenden formale/Oracle-Muster; natürliche Sprachplanung und alleiniger Graphformatnutzen sind damit nicht nachgewiesen. Sechs temporale Fragen bleiben wegen bereits erfolgter Einsicht Entwicklungsmaterial, auch wenn sie zwischenzeitlich technisch quarantiniert waren.

## B4 – Kontrollierter Relationsretrieval-Vergleich

**RQ4/RQ5.** Derselbe Retrievalreport verwendet einen positiven exponierten Teilbestand von **28 Aufgaben**, 46 bekannten Evidenzbezügen und 66 bekannten Relationsbezügen. 13 Arme, gleiche gebundene Quellen; fachliche Referenzvollständigkeit und unabhängige Human-Gold-Abnahme fehlen. Die 28 Aufgaben sind keine 28 unabhängigen Komponenten.

| Arm | Vollständige bekannte Bündel /28 |
|---|---:|
| BM25 mit Kontext | 23 |
| BM25 mit derselben zusätzlichen Relationsinformation | 26 |
| Dense-Präfix | 13 |
| Dense-Fenster | 14 |
| Lexikalisch/Dense-RRF | 20 |
| Fenster-RRF | 19 |
| SQL und Graph Traversal | jeweils 28 |

Interpretation: Relationsinformation hilft hier bekannten Bündeln; die Textbaseline schließt mit denselben Relationskarten einen Teil der Lücke. SQL/Graph haben zudem formalisierte Querymuster. Deshalb ist der Abstand zu BM25 **kein isolierter Graphformat-Effekt**. Die Fensterablation zeigt einen kleinen beobachteten Bündelunterschied unter geänderter Vorverarbeitung, keine allgemeine Überlegenheit oder Nutzlosigkeit semantischer Suche. Zwei visuelle Fälle benötigen zusätzliche Originalinterpretation. Konfidenz-/Überlegenheitsansprüche werden im Originalreport verweigert.


## Zurückgestellte historische Detailbefunde

DS-005/007–009, E1/E2, M4-A/A2/A3, M4-B2/M5/SQL-Tool-Baselines, Bayern-Census, weitere Modellkohorten und Produkt-/Browser-/Validation-Prüfungen bleiben getrennte lokale Studien. Ihre vollständigen zahlenreichen Reports werden hier nicht ohne die zugehörige Inhalts-/Rechteprüfung übernommen. Die lokale Arbeit bleibt unverändert erhalten; aus fehlender Veröffentlichung folgt weder ein negatives Studienergebnis noch nachträglich unabhängige Validierung.

Es wird keine Gesamtaccuracy über Studien und keine statistische Signifikanz behauptet. Fehlende unabhängige qrels bleiben NOT_EVALUATED. Publizierte technische Testergebnisse stehen getrennt im PUBLICATION_REPORT. Priorität bleiben unabhängige Originalurteile und faire Informations-/Budgetbedingungen gemäß RESEARCH_METHODOLOGY.
