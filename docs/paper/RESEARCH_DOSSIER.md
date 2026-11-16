# Klima_GraphRAG — auditierbares Forschungs- und Paper-Dossier

Stand: 15.11.2026. **Materialgrundlage, kein fertiges oder eingereichtes Paper.**

Geltungsregel: **EXISTING RESULT** bezeichnet gespeicherte, begrenzte Befunde oder tatsächlich ausgeführte technische Prüfungen. **FUTURE EXPERIMENT** bezeichnet einen noch zu genehmigenden prospektiven Plan. **HYPOTHESIS** bezeichnet eine falsifizierbare Erwartung ohne bestätigten Effekt. Fehlende Angaben heißen NOT_KNOWN/NOT_EVALUATED, niemals erfundene Null. Das [Claimregister](CLAIM_EVIDENCE_REGISTER.csv), die [Experimentmatrix](EXPERIMENT_MATRIX.csv) und das [Artefaktmanifest](ARTIFACT_MANIFEST.tsv) sind Teil dieses Dossiers; ihre Statusangaben begrenzen jede Interpretation.

## 1. Executive Abstract und präzise Contributions

**EXISTING RESULT — Engineering:** Das Projekt entwickelt eine versionierte Evidenzpipeline für kommunale Klimaschutzrecherche. Die neue öffentliche Chatkomposition verbindet natürliche Fragen, persistente sitzungsgebundene Turns, explizite Antwortzustände, extraktive Quellenclaims, stabil adressierbare Passagen und technische Reviewrollen. Sie nutzt den vorhandenen kanonischen Retrieval-/Citationvertrag statt einer neuen Retrieverimplementierung. Der Vollcorpus-/Produktpfad bleibt wegen ungeklärter E1-Abhängigkeiten und Datenrechte von der öffentlichen Fixturekomposition getrennt.

Als belegbare Beiträge kommen derzeit ein implementierter Evidenz-/Konversationsvertrag, überprüfbare technische Sicherheits-/Reviewprüfungen und studiengetrennte negative Fehlerbefunde infrage. **Keine Neuheitspriorität, keine allgemeine Überlegenheit von GraphRAG und keine verbesserte wissenschaftliche Answer Accuracy werden behauptet.** Fachliche Vollständigkeit und UX-Nutzen benötigen unabhängige menschliche Prüfung. Die Paperposition muss diesen Stand abbilden; siehe [Outline](PAPER_OUTLINE.md).

## 2. Motivation und Problem

Kommunale Angaben liegen über Websites, PDFs, Zeitstände und unterschiedliche Institutionen verteilt. Ein Hostingort beweist nicht die sachliche Zuständigkeit für jede Passage. Förderphase, Beschluss, Planung, berichtete Umsetzung, Prognose und Messung sind verschiedene Aussagen. Eine recherchierende Person braucht nicht nur einen Treffer, sondern nachvollziehbare Antworten auf eine konkrete Frage und einen einfachen Weg zurück zur richtigen Originalfassung.

**HYPOTHESIS:** Ein answer-first Gespräch mit explizitem Scope und stabiler Evidence-Navigation könnte diese Aufgabe besser unterstützen als eine Rohkartenliste. Diese Erwartung legitimiert die Implementierung, ersetzt aber keine Usability- oder Fachstudie. Übervorsichtige Verweigerung wird über False Abstention und answerable-case retention ebenfalls als möglicher Fehler untersucht.

## 3. Forschungsfragen und Hypothesen

Verbindlich bleiben [RQ1–RQ8/H1–H6/T1–T9](../../research/sprints/sprint-01/RESEARCH_QUESTIONS.md) und das [Study Protocol](../../research/sprints/sprint-01/STUDY_PROTOCOL.md). RQ1 Akquisition/Identität, RQ2 Konversion, RQ3 Extraktion/Repräsentation, RQ4 Retrieval, RQ5 Relationen, RQ6 Synthese, RQ7 Verification/Abstention, RQ8 Komplexität. Die ursprünglichen gerichteten Hypothesen betreffen Recall, Wrong Scope, Konversion, SQL-Felder, vollständige gültige Belegbündel und ungestützte Aussagen bei gesonderter Coverage.

Lokale Preview-IDs T1–T8 sind keine Umnummerierung der wissenschaftlichen Tasktaxonomie. Die neue Konversationsversion ist eine Engineeringintervention innerhalb dieser Fragen, kein neuer globaler Forschungsrahmen. Es gibt keinen nachträglich begründeten praktischen Effektabstand oder Nichtunterlegenheitsrand. Prospektive Ergänzungen sind als Entwurf mit Owner-/Reviewergenehmigung auszuweisen.

## 4. Related Work und begriffliche Abgrenzung

Die Primärquellen und Metadaten wurden am 15.11.2026 gezielt überprüft, keine vollständige systematische Literaturstudie behauptet. [Lewis et al.](https://arxiv.org/abs/2005.11401v4) verbinden parametrische Generierung mit nichtparametrischem Retrieval. Das beschreibt einen allgemeinen RAG-Ansatz, nicht automatisch die konkrete extraktive Standardkomposition dieses Projekts.

[Edge et al.](https://arxiv.org/abs/2404.16130v2) untersuchen graphgestützte globale Zusammenfassung über einen Graphindex und Communitysummaries. Die offizielle [Local-Search-Dokumentation](https://microsoft.github.io/graphrag/query/local_search/) beschreibt entityzentrierte Kontextzusammenstellung; [Global Search](https://microsoft.github.io/graphrag/query/global_search/) verarbeitet Communityberichte. Das eigene relationale Traversal über gespeicherte Kanten ist nicht deshalb Microsoft GraphRAG, weil Neo4j verfügbar ist. Eine qualifizierte globale Community-RAG-Wirkung wurde hier nicht nachgewiesen.

[RAGAs](https://aclanthology.org/2024.eacl-demo.16/) und [ARES](https://aclanthology.org/2024.naacl-long.20/) motivieren getrennte diagnostische Bewertung von Kontext und Antworten. Automatische/LM-gestützte Urteile sind keine unabhängige Originalannotation. Solche Evaluatoren sind hier weder als Gold installiert noch als neue Projektergebnisse ausgegeben. Bibliografie und geprüfte Versionen: [LITERATURE.bib](LITERATURE.bib).

## 5. Corpus, Authority, Provenienz und Datenethik

**EXISTING RESULT:** Lokale Forschungsstände enthalten echte kommunale Quellenfassungen; der öffentliche Snapshot verteilt dagegen ausschließlich ausgewählte eigene Aggregate und synthetische technische Inputs. Private Corpusstände, Seiten, Referenzpakete und personenbezogene Reviews werden nicht übernommen. Der amtliche Erfassungsrahmen ist kein qualifizierter Nenner vollständiger kommunaler Klimapolitik. Abrufdatum ist kein Beweis aktueller Realweltgeltung.

Originalbytes, Body-SHA, URL, Version, Herausgeber, Quelle-zu-Gemeinde-Rahmen und claim-spezifische Geltung sind getrennte Felder. Wo der kanonische Fixturevertrag keinen Original-Bodyhash oder keine PDF-Seite enthält, liefert der Chat ausdrücklich NOT_AVAILABLE/None und bezeichnet den vorhandenen Hash als **Passagenhash**, nicht als Originalhash. Das ist eine Einschränkung, keine heimlich erfüllte Originalprüfung. Rechte zur Verarbeitung, Auszugsanzeige und Weitergabe müssen separat feststehen. [DATA_POLICY](../../DATA_POLICY.md) bleibt verbindlich.

## 6. Parsing, IE, Entity Resolution und Schema

Die vorhandene Pipeline trennt HTML/PDF-Konversion, geordnete Units, Segmentaufnahme, Chunks und persistierte extrahierte Felder. DS-006 ist eine erfundene mechanische Kontrolle dieser Übergaben. Spätere Parseränderungen können bekannte Fixtureergebnisse verändern; historische Resultate dürfen nicht überschrieben oder als aktuelle Baseline ausgegeben werden.

Eine Zahl benötigt Einheit, Rolle, Zeit und Geltung. Null/Unknown ist nicht eine falsche Nullzahl. Entitätsauflösung darf Autor, Betreiber, Geldgeber und betroffenes Gebiet nicht durch Namensnähe gleichsetzen. Der neue Chat annotiert ungelöste Rollen oder Zeiten ausdrücklich als unresolved/None; Regex-Felder sind vorläufige Anzeigehilfen. Sie sind keine semantisch geprüfte IE. Schema-/Source-ID-Gültigkeit ist notwendig, aber nicht hinreichend für Claimkorrektheit.

## 7. Retrieval, SQL, Graph und Routing

Lexikalische BM25-, semantische, hybride und strukturierte Forschungsarme bleiben experimentgebunden. Gleiche Namen bedeuten nicht gleiche Tokenisierung, Vorverarbeitung oder Information. Ein fairer Vergleich verlangt identische Quellenfassungen, Kandidaten-/Kontextbudgets und Aufgaben. Relationskarten können die Information einer Textbaseline verändern; deren Effekt ist vom Speicherformat zu trennen.

**EXISTING RESULT:** Der öffentliche Chat ruft den bestehenden ResearchApplicationService/CanonicalRetriever auf. Ein begrenztes Intentrouting kann eine explizite Rollenfrage dem Graphmodus zuordnen; die Expertenauswahl bleibt explizit. Die kanonische synthetische Komposition hat keine serverseitigen Gemeinde-/Jahresfilter. Die Chatqualifikation prüft deshalb die zurückgegebenen Quellframes/Jahresnennungen innerhalb des begrenzten Pools. Das ist **kein** Nachweis einer vollständigen gefilterten Suche und keine Auflösung der sachlichen Claimgeltung. False Abstention bleibt möglich. Fehlende E1-Retrievalmodule werden nicht neu implementiert oder umbenannt.

## 8. Generierung, Verifikation und Abstention

Pipeline: Turn → expliziter/übernommener Scope → Routing → bounded Retrieval → Qualifikation → Quellenclaims → Darstellung/Trace. Der Standard erzeugt keine LLM-Paraphrase und keine Pseudostreams. Er verwendet verbatim Passagen mit stabiler Evidence-ID. Fachliche Plausibilität, logisches Entailment und Aspektvollständigkeit folgen aus Verbatim-Gleichheit nicht automatisch. Mehrteilige/offene Fragen bleiben konservativ PARTIAL; unqualifizierte Evidenz erzeugt INSUFFICIENT_EVIDENCE, Mehrdeutigkeit eine konkrete Rückfrage.

Die Qualifikation verwirft erkennbare Instruktions-/Scriptmuster, fehlenden Frageaspekt, nicht gestützte Zahlen-/Jahresbedingungen und Planbelege für Umsetzungs-/Messfragen. Das sind begrenzte, parametrisierte Engineeringregeln und keine allgemeine Truth-Machine. Ähnlicher Wortlaut kann trotzdem nicht antworttragend sein; Formulierungslücken können gültige Evidenz ausschließen. **FUTURE EXPERIMENT:** gleicher eingefrorener Evidence-Kontext für deterministisch extraktiv versus explizit konfigurierten vorhandenen OllamaGenerationProvider/Guardrails. Im Sprint kein Modell aktiviert, heruntergeladen oder kostenpflichtig aufgerufen. Keine Formulierungsänderung dem Retrieval zurechnen.

## 9. Konversation, Graph-UX und Validation

**EXISTING RESULT — Implementierung:** Chat ist der Standardarbeitsraum; Research zeigt Scope, Verfahren, Snapshot und exportierbaren Trace progressiv. Validation hat ein getrenntes individuelles Login, Queue, Originalöffnung, einzelnes Urteil, Begründung, Draft und Abschlussbestätigung. Chatfeedback wird nicht zu Labels oder unabhängigen Urteilen.

Turns haben persistente IDs und Elternbezüge; Retry/Edit erzeugt eine neue Version, kein Überschreiben. Sichtbarer Kontext und Rückfragen vermeiden stille Übernahme bei erkanntem Orts-/Zeitwechsel; freie Ortsauflösung bleibt begrenzt. Lokale Sessions sind keine extern geprüfte Personenidentität. Speicher-/Löschregeln und Grenzen stehen im Produktvertrag.

Graphkanten werden auf die verwendeten Beleg-IDs beschränkt und als technische Provenienzverbindung oder EXTRACTED_UNVERIFIED bezeichnet. Der synthetische PLANT-Fall ist ein ausführbarer Vertragsnachweis, keine reale kommunale Entdeckung. Visuelle Knoten/Kanten und zugängliche Alternativen müssen dieselbe Passage öffnen. Cutoffs sind sichtbar; ohne Humanurteil keine human_validated-Farbe oder implizite Kausalität.

## 10. Prospektives Design und Baselines

**FUTURE EXPERIMENT — Genehmigung ausstehend:** Baselinefreeze bindet Code, Corpus, Sources, Aufgaben, Antworten, Provider/Prompt, Tokenizer, Parameter und Traces vor Änderungen. Bisher bekannte kommunale Showcases bleiben exponiert. Neue unabhängige Holdouts nur über den freigegebenen Original-/Reviewprozess. Keine automatische Fragengenerierung als Gold.

Retrieverkontrast: starke lexikalische BM25-Baseline, Dense, RRF-Hybrid, BM25 mit identischen Relationskarten, typisiertes SQL und Graph Traversal. Optionales Toolrouting ist eigener Faktor. Formatvergleich SQL/Graph hält Relationsmengen und formalisierte Query gleich; Informationsablation und Query-Formalisierungs-/Routingablation gesondert. Generator und Evidence-Budget bleiben im Retrieververgleich konstant. Extraktiv/LLM-Kontrast erhält dieselben Evidence-IDs und Kontexttokens, keine stillen Nachretrievals. Endpunktwahl wird vor Human-Testausgaben genehmigt; kein willkürlicher Qualitätssummenscore.

## 11. Studiengetrennte bestehende Befunde

**EXISTING RESULT:** Die veröffentlichte stronger-ie01-Zusammenfassung enthält acht Quellenfälle/acht dokumentierte Komponenten mit 56 abhängigen Slots (27 positive, 29 Unknown). Regeln TP/FP/FN 2/1/25; 1.7B-Modell 16/40/11; Hybrid 5/5/22. Die F1-Arithmetik wird aus diesen Zählern geprüft, nicht als unabhängig bestätigte semantische Qualität ausgegeben. Historischer genauer Codecommit ist im Aggregat nicht belegt; vollständiger Replay benötigt private Inputs.

Der separate Retrievalreport bindet Corpus `a8e5ac8c83348e6481eb44469a40d0ae78f29d26694c19e97993a67373fb6280`: 38 Aufgaben, 35 Quellen, 108 vorläufige Relationen und 18 konservative Abhängigkeitskomponenten. SQL und Graph liefern in den formalisierten Fragen dieselben Relationsmengen. Im positiven exponierten Teilbestand von 28 Aufgaben erreichen bekannte vollständige Bündel: BM25-Kontext 23, gleiche Relationsinformation 26, Dense-Präfix 13, Dense-Fenster 14, RRF 20, Fenster-RRF 19, SQL/Graph je 28. Das ist kein isolierter Graphformatgewinn und kein populationsweiter Recall.

DS-/E2-/M4-/M5-/Bayern-Studien und der historische 300×5-Produktrun bleiben **eigene** Untersuchungen. Deren bloße Ausführung belegt weder Fachrelevanz noch allgemeine Qualität. Nicht freigegebene Originalreports bleiben als private Abhängigkeiten im Register, ohne neue Gesamtergebnisse zu bilden. Lokale neue Baseline-Traces zeigen auch Fehler; insbesondere kein neuer Brunn–Laaber-PASS in dem hier tatsächlich gefahrenen Graph-/Lexical-Lauf. Details nur im privaten Receipt, nicht als Originalauszug veröffentlicht.

## 12. Quantitative und qualitative Evaluation

**FUTURE EXPERIMENT:** Atomare fachlich korrekte Claims, passende Citations, Answer Relevance, gefragte Aspekte, Gemeinde-/Zeit-/Akteurskorrektheit, Konflikte und ungestützte Aussagen getrennt zählen. Coverage ist beantwortete/zulässige Aufgaben; Selective Risk ist Fehler unter beantworteten Aufgaben. Beide gemeinsam, einschließlich falscher Abstention und erhaltener Teilantworten. Nichtbeantwortbare Aufgaben haben keinen künstlichen Recall-Nenner null.

Recall@k benötigt vollständige verifizierte relevante IDs; Precision@k verwendet tatsächlich zurückgegebene deduplizierte IDs nach festgelegter Zählkonvention. MRR nur für sinnvollen ersten relevanten Treffer, nDCG nur mit geeigneten Gradlabels/IDCG. Alternative Bündel und Source Fidelity gesondert. Ohne qrels NOT_EVALUATED. Qualitative Fehlerklassen: falscher Geltungsbereich, Rollentausch, Plan/Messung-Verwechslung, fehlende Evidence Bridge, unvollständiger Kontext, falsche Verweigerung und nicht belegte Interpretation eines Graphpfads.

**EXISTING RESULT — Engineering:** Tatsächliche Tests werden im Produkt-Handoff mit Commit/Befehlen/Nennern geführt. Browserlatenz ist Systemzeit bis DOM-Antwort, keine Zeit zur ersten von Menschen als zuverlässig akzeptierten Antwort. Ein synthetischer Reviewerablauf darf nicht in die fachliche Ergebnisstatistik eingehen.

## 13. Ablationen und identifizierte Effekte

**FUTURE EXPERIMENT:** Information (Relationskarten ja/nein), Speicherrepräsentation (gleichinformiert SQL/Graph), Query-Formalisierung (Oracle versus tatsächlicher Planner), Routing, Generator und Antwortdarstellung werden getrennt variiert. Vorverarbeitung, Quellenumfang und Budgets fixieren. Ein Parserfix ist kein Retrievergewinn, zusätzliche Modellkontexte sind kein Formateffekt.

Vorhandene E1-/M4-Kontrollen motivieren solche Trennung, beweisen aber nicht den zukünftigen Produkteffekt. Kein nachträglich günstiger Ablationsarm als präregistriert darstellen. Negative oder uneindeutige Effekte bleiben Resultate. Ressourcen-/Methodenfehler mit Verursacherkategorie berichten, nicht aus dem Nenner entfernen.

## 14. Statistik und Threats to Validity

Beobachtungszeilen sind nicht unabhängig, nur weil sie verschiedene Frage-IDs tragen. Gemeinden, Projektfamilien, Dokumentfassungen, geteilte Quellen und Paraphrasen gruppieren; konservative verbundene Komponenten respektieren. Die 18 dokumentierten Komponenten sind keine 18 nachgewiesen zufällig unabhängigen Stichprobeneinheiten. Effekte als gepaarte Unterschiede je Taskfamilie mit Rohnennern berichten.

Intervalle/Resampling nur bei hinreichend informativen Gruppen und begründeter Clusterdefinition. Kein ungeplanter p-Wert-Suchlauf, keine verlässliche nationale Unsicherheit aus vier Gemeinden, keine Poolung historischer Kohorten. Exposition, Poolunvollständigkeit, unterschiedliche Kontextfenster/Information, gemeinsame Extraktoren, Änderungen zwischen Codeversionen und selektive Originalverfügbarkeit sind Confounder. Technische Nullfehler sind kein Nachweis semantischer Vollständigkeit.

## 15. Software, Infrastruktur und Reproduktion

Öffentlicher Startpunkt ist Commit `6edd3d8b182867c2da1ad0e2899033d3fc4ee71c`. Der Featurebranch erbt nur diesen geprüften Stand, keine private Releasehistorie. Neue Implementierungs- und Nachweiscommits stehen im Handoff. FastAPI/SQLite/Vanilla JS/Cytoscape bleiben im bestehenden Stack. Dependencies sind fixiert; der optionale Chrome-Testtreiber ist kein Produktdienst.

Die öffentliche Input-Suite bleibt getrennt von fehlenden privaten Originalen/PACKETs und E1-Importen. Bestehende Gates unverändert. [REPRODUCTION_GUIDE](REPRODUCTION_GUIDE.md) erklärt Start, Checks, Sourcehashes und fehlende Inputs. Kein öffentliches Corpusrestore aus einem Codecheckout behaupten. Forschungsskript vom 09.11. wurde lokal gesichtet; es ist ein historischer Stand, keine laufend bestätigte Produktdokumentation und wird ohne neue Rechteprüfung nicht weiterverteilt.

## 16. Kosten und Effizienz

**EXISTING RESULT:** Kein kostenpflichtiger Inferenzaufruf, kein Modell-/Graph-Rebuild oder Crawl für diesen Sprint. Synthetische Vektoren und vorhandene lokale Indizes werden genutzt. Hardwaredaten aus tatsächlicher Messung; hohe Swap-/Plattenbelegung ist keine gemessene Modellskalierungseigenschaft. Testtreiberinstallation und CPU-/Browserläufe erzeugen lokale Betriebslast, keine als null erfundene Energie-/CO2-Bilanz.

**FUTURE EXPERIMENT:** Aufbau, Updates, Query, Kontexttokens, tatsächliche Hardware/RAM und I/O, Latenzen und Fehler pro gestütztem Ergebnis protokollieren. Buildkosten nicht gegen Querykosten verschweigen. Ressourcenlimit und Anbieterbudget pro Arm vorab gleich festlegen. Ohne zuverlässige Messbarkeit keine kostenpflichtige API. Größeres Modell auf Mac M3/8GB nur nach konkretem Footprint-/Lizenz-/Speicherentscheid.

## 17. Datenschutz und Lizenzen

Sitzungstoken, persönliche Reviews, private Quellen und Accounts bleiben in ignorierten lokalen Stores. Cookiebesitz ist lokale Kontextisolation, keine unabhängige Betreiberidentität und keine externe OTP-Abnahme. Host-/Origin-/CSRF-/IDOR-Prüfungen und sichere DOM-Ausgabe ergänzen die Grenze; sie ersetzen kein Produktionsaudit.

Eigener Code/Dokumentation unter Projekt-MIT, Cytoscape unter beigefügter MIT-Lizenz; Drittanbieter-/Datenrechte bleiben getrennt. E1-Kit ohne belegte Lizenz nicht veröffentlichen oder als eigene Implementation neu etikettieren. Öffentliche Primärliteratur verlinken, keine langen Volltextauszüge. Konkrete neue Publikationsallowlist vor Featurepush prüfen; sven-develop und main nicht ändern.

## 18. Discussion

Die derzeit stärkste Position ist evidenzgebundene Forschungssoftware mit transparenten Grenzen, nicht ein neuer Sieger im Architekturvergleich. Strukturierte Daten helfen nur bei richtiger Extraktion und Scope. Graphrelationen können navigierbar sein und trotzdem falsch oder unvollständig. Gute UI darf begrenzte Evidenz nicht in größere Gewissheit übersetzen.

Das System soll belastbare Teilaspekte zeigen und Lücken benennen. Eine Sicherheitspolitik, die alle Fragen ablehnt, wäre kein Qualitätsgewinn. Ebenso wenig gilt die erfolgreiche Auflösung einer Citation-ID als Entailment. Die vorhandenen Studien zeigen, warum getrennte Fehlerdiagnostik und faire Informationskontraste notwendig sind; Humanurteile fehlen weiterhin für weitergehende Schlussfolgerungen.

## 19. Negative Findings

**EXISTING RESULT:** Frühere Retrieval-/Extraktionsbefunde enthalten Unknowns, Informationsasymmetrien und nicht qualifizierte Modellarme. Der aktuelle private Baseline-Harness hatte zunächst einen falschen QueryOptions-Aufruf; dieser Lauf wird als Harnessfehler erhalten. Der korrigierte Lauf zeigt `grounding_failed` für Brunn–Laaber/Amberg, nicht bestätigte erfolgreiche neue Showcases. Der erste neue Chrome-Harness prüfte einen asynchron geöffneten Dialog zu früh; korrigiert wurde das Wait, kein Ergebnis fingiert.

Fehlende Originalhashes im öffentlichen Fixturevertrag und unvollständige freie Orts-/Aspektauflösung sind offene Produktgrenzen. Technischer Erfolg des synthetischen Reviews ist keine tatsächliche Personenteilnahme. Keine dieser Grenzen wird durch neue Labels, Umbenennung alter Runs oder dekorative Graphdarstellung kaschiert.

## 20. Limitationen und offene Fragen

NOT_INDEPENDENTLY_VALIDATED. UX_HUMAN_VALIDATED ebenfalls offen. Manche Scope-/Zeitbedingungen werden im begrenzten zurückgegebenen Pool geprüft; damit sind False Abstention und verpasste alternative Belege möglich. Die öffentliche Fixture hat weder echtes Behördenmaterial noch vollständige Original-PDFs. Der vorhandene lokale Ollama-Pfad ist nicht als neues Chatmodell qualifiziert. Kein freigegebener externer Preview-Link.

Weitere offene Punkte: atomare Claimsegmentierung jenseits kurzer Passagen, vollständige Aspektmodelle/kontradiktorische Belege, sichere fachliche Ortsauflösung, durchgehende Originalhash-/Seitenlocators und echte assistive Nutzung. Diese sind konkrete Grenzen, kein automatisch gestarteter nächster Architekturauftrag. [OPEN_GATES](OPEN_GATES.md) trennt Rechte-/Human-/Infrastrukturentscheidungen.

## 21. Paper-Narrative, Tabellen und Figuren

Bevorzugtes Narrativ und Alternative stehen im [PAPER_OUTLINE](PAPER_OUTLINE.md). Architekturabbildung soll Informations-/Verifikationsgrenzen zeigen. Ein zweites Bild zeigt eine konkrete Antwort, Passage und vorläufige Relation. Die Reviewabbildung trennt tatsächliche Zustände von technisch simulierten Urteilen. Tabellen verwenden pro Studie eigene Nenner, Exposition und Reproduktionsgrenzen.

Kein Abstract mit bestätigter Accuracy-/Usabilitysteigerung vor Human Gold. Ein methodisch negatives oder begrenztes Softwarepaper ist wissenschaftlich seriöser als ein vorbestimmter GraphRAG-Sieg. Submission benötigt Claim-, Rechte- und unabhängigen Fachreview; Status PAPER_SUBMISSION_READY bleibt rot.

## 22. Literatur, Evidenzregister und Auditpfad

[LITERATURE.bib](LITERATURE.bib): verifizierte Primärliteratur/Implementierungsdokumentation mit Version/Zugriff. [CLAIM_EVIDENCE_REGISTER](CLAIM_EVIDENCE_REGISTER.csv): zentrale Sätze, SHA/Locator, Studien-/Expositions-/Rechtestatus. [EXPERIMENT_MATRIX](EXPERIMENT_MATRIX.csv): getrennte bestehende versus zukünftige Kontraste. [ARTIFACT_MANIFEST](ARTIFACT_MANIFEST.tsv): überprüfte Bytes und öffentliche/private Restoregrenzen. [HUMAN_REVIEW_PROTOCOL](HUMAN_REVIEW_PROTOCOL.md) und [UX_EVALUATION_PROTOCOL](UX_EVALUATION_PROTOCOL.md): noch zu genehmigende Prospektivmethodik.

Diese Register machen einen Befund prüfbar, aber nicht automatisch wahr, unabhängig oder extern zeitgestempelt. Aktualisierungen dürfen historische Resultate nicht überschreiben. Neue Befunde brauchen eigene Receipts und klare Versionen. Die Abschlussampeln im [Produkt-Handoff](../product/CONVERSATIONAL_RESEARCH_HANDOFF.md) begrenzen den tatsächlich erreichten Stand.
