# Tatsächliche Forschungsmethodik

Stand 15.11.2026. Diese Konsolidierung erklärt vorhandene Designs; sie registriert keine früheren Ergebnisse nachträglich als prospektiv. Empirische Werte, Snapshotbindungen und Grenzen stehen in [RESEARCH_FINDINGS_AND_LIMITATIONS.md](RESEARCH_FINDINGS_AND_LIMITATIONS.md). Allgemeine Methoden werden hier als solche erklärt und nicht als Projektbefund ausgegeben.

Die allgemeine Theorie stützt sich auf das bereits vorhandene Quellenregister des Readers (lokales, hier nicht mitgeliefertes Artefakt). Dessen Literaturstand bleibt 09.11.2026; eine neue Literatur- oder Metadatenprüfung wird nicht behauptet.

## Forschungsfragen: bestehende Nummerierung beibehalten

Verbindlicher Bezug für RQ1–RQ8 ist [Research Questions, 02.11.2026](research/sprints/sprint-01/RESEARCH_QUESTIONS.md), zusammen mit [Study Protocol](research/sprints/sprint-01/STUDY_PROTOCOL.md). Die ältere Foundation enthält Vorarbeiten; thematische Reader-Kategorien ersetzen die Originalnummern nicht. Der bei der Dokumentprüfung gefundene Nummerierungsfehler ist im Änderungsnachweis (lokales, hier nicht mitgeliefertes Artefakt) offengelegt.

| ID | Unveränderter Untersuchungsgegenstand | Erforderlicher Nachweis |
|---|---|---|
| RQ1 acquisition/identity | Autoritative Quellen für Gemeinde und Stichtag | Qualifizierter Abdeckungsnenner und dokumentierte Attributionsfehler |
| RQ2 conversion | Zahlen, Tabellen, Überschriften, Datumsangaben und Quellenreihenfolge erhalten | Wiederauffindbare Originalspannen und erhaltene semantische Felder |
| RQ3 extraction/representation | Status, Akteur, Maßnahme, Menge/Einheit, Bezugszeit und Geltung repräsentieren | Feldkorrektheit/-vollständigkeit mit Provenienz und Unknowns |
| RQ4 retrieval | Alle nötigen Belege auch bei Paraphrasen finden | Aufgaben-/quellbezogene Relevanzurteile und Bündelvollständigkeit |
| RQ5 relation/reasoning | Plan, Akteur und zeitliche Handlung über Passagen verbinden | Gültige Relationsketten und vollständige Belegbündel |
| RQ6 synthesis | Vergleich und historische Veränderung erklären | Gestützte Claims, vergleichbare Begriffe/Zeiten und offengelegte Konflikte |
| RQ7 verification/abstention | Fehlende, widersprechende, mehrdeutige oder ortsfremde Evidenz erkennen | Sichere Nichtbeantwortung bei Erhalt beantwortbarer Fälle |
| RQ8 complexity | Aufbau, Aktualisierung und Suche wiederholen | Stufenbezogene Kosten/Latenz je tatsächlich gestütztem Aufgabenergebnis |

### H1–H6 und T1–T9

Die ursprünglichen Hypothesen sind gerichtet und falsifizierbar. H1: semantisch/hybrid erhöht gegenüber Lexik Recall@k auf Paraphrasen. H2: Identitäts-/Zeitbedingungen senken Wrong-Scope-Fehler. H3: korrigierte Konversion erhöht originalgestützte Feldaccuracy. H4: strukturierte Extraktion plus SQL erhöht numerische/temporale Feldaccuracy gegenüber Text. H5: Graphzusatz verbessert vollständige gültige Belegbündel gegenüber gleichquelliger Nichtgraphmethode. H6: explizite Abstention senkt ungestützte Aussagen bei gesondert berichteter Beantwortungsabdeckung. Für Nutzenmetriken lautet die gerichtete Nullhypothese Δ≤0, für die genannten Fehlerraten Δ≥0; die Alternative hat das umgekehrte Vorzeichen. Δ ist der innerhalb vergleichbarer Aufgaben gepaarte, gemeindemakro-gemittelte Unterschied A−B, kein gepoolter Unterschied beliebiger Studien. Praktische Mindestabstände oder Noninferioritätsgrenzen sind nicht nachträglich begründet.

T1–T9 des Protokolls bedeuten direkte Faktenabfrage, numerischer Fakt mit Einheit/Jahr, kompatible historische Veränderung, vergleichbare Gemeindevergleiche, Akteur/Maßnahme, mehrdokumentige Kette, Quellenkonflikt, begrenzt unbeantwortbare/falsche Prämisse und mehrdeutige/falsche Gemeinde bzw. Modalität. **Die Preview-IDs T1–T8 in PREVIEW_REFERENCE_QUESTIONS.json sind andere lokale Test-IDs.** Ebenso sind die im Acceleration-Bericht lokal benannten H1–H5 keine nachträgliche Umnummerierung der Sprint-1-Hypothesen.

## Akquisition, Abdeckung und Provenienz

Die amtliche Gemeinde-ID bildet den Bearbeitungsrahmen. Akquisition protokolliert begrenzte Abrufe, Ausfälle, Zurückstellungen und Quelleversionen. URL und Bodyhash sind getrennte Identitäten. Eine Quellenfassung besitzt Originalbytes, SHA-256, Abrufkontext, zugeordnete Gemeinden und Parserunits. Die Zuordnung eines Dokuments zu einer Gemeinde ist eine Quellenrahmeninformation, kein automatisch bestätigter Claim-Scope. Herausgeber-/Authority- und Stichtagsurteile erfordern eigene Prüfung.

Quellenbestände sind nicht austauschbar: E2 v1.1 hat vier Gemeinden/57 Fassungen, die spätere Bayern-Federation mehrere historische und ergänzende Ledger. Das Hinzufügen neuer Quellen, Reparieren eines Parsers oder Verändern eines Scopes ist eine neue Bedingung und muss in einem neuen Manifest erscheinen. Keine fehlende Quelle darf durch eine andere Version unter derselben URL ersetzt werden. Rechte zur Verarbeitung, Anzeige von Auszügen und Archivweitergabe sind gesonderte Betriebsfragen.

## Parsing, Chunking und Information Extraction

HTML und PDF werden in Einheiten mit Reihenfolge und Locator überführt. Parsergesamttext, Segmentliste, Admission und Chunking sind getrennte Übergabepunkte. Eine Quote im Gesamttext kann bei Segmentierung oder thematischem Filter verloren gehen. Die M4-A-Kontraste halten Originalbytes fest: P0/P1 unterscheiden Parserbedingungen; P1/P2 isolieren den zusätzlichen bestehenden Aufnahmefilter. M4-A2 variiert Segmentierung und Aufnahme in einem 2×2-Design; der nachgelagerte A3-Audit beurteilt hinzugefügte Inhalte und Mehrdeutigkeiten. Er ist kein neuer unverzerrter Qualitätsbenchmark.

IE extrahiert typisierte Felder und Relationen. Zahl, Einheit, Betragsrolle und Bezugszeit gehören zusammen. „Förderquote“ ist keine Auszahlung, Prognose keine Messung. Regel-, lokale Modell- und Hybridarme nutzen jeweils dokumentierte Quellen und Parametersätze. Schema-Gültigkeit ist eine Ausführungsvoraussetzung; sie ist keine semantische Präzision. Unknowns werden separat bewertet, damit ein Arm durch permanentes Null-Ausgeben keine scheinbar hohe Feldaccuracy gewinnt.

## Repräsentation, Identität und Graph

Die lokale Evidenzbasis persistiert Quellen-/Unit-/Claimbindungen in SQLite. Entity Resolution darf Herausgeber, Fördergeber, Projektträger, Autor und Betreiber nicht allein wegen Namensähnlichkeit vereinigen. Ungeklärte Identitäten und Zeitbezüge bleiben explizit. Vorläufige Relationskanten tragen ihre Originalbelege und können in unterschiedliche Queryrepräsentationen projiziert werden. Neo4j ist eine optionale Implementierungsgrenze, nicht die Definition von GraphRAG oder Voraussetzung der SQLite-Preview.

Der 38-Fragen-Kontrast SQL/Graph hält kuratierte Relationen und logische Suchmuster gleich. Er identifiziert die funktionale Ergebnisäquivalenz dieser beiden Implementierungen in dieser Kohorte. Der Abstand zu Textretrieval verändert dagegen zusätzlich Informationszugang und Query-Formalisierung. Er identifiziert keinen reinen Speicherformateffekt und keine allgemeine natürlichsprachliche Planungsfähigkeit.

## Retrieval und Query Planning

BM25 ist eine lexikalische Rangfunktion, beispielsweise Σ IDF(t)·f(t,d)(k1+1)/(f(t,d)+k1(1−b+b|d|/avgdl)); konkrete Tokenisierung, IDF-Definition und Parameter gehören zur jeweiligen implementierten Baseline. FTS5-Kontext-BM25, Streaming-BM25 und erweiterte Wortregeln sind deshalb keine ungekennzeichnet identische Methode. Dense Retrieval vergleicht Encoderrepräsentationen; bei normierten Vektoren ist das Skalarprodukt die Cosine-Ähnlichkeit. Präfixtrunkierung und Fenster verändern die tatsächlich sichtbaren Informationen.

Hybridarme kombinieren Ranglisten, etwa mit Reciprocal Rank Fusion Σ 1/(c+rank). c, Kandidatenpools und Filter sind rungebunden und dürfen für eine Ergebnisinterpretation nicht geraten werden. Structured Retrieval nutzt vorhandene Felder und begrenzte Querymuster; Graphretrieval nutzt vorhandene Kanten/Pfade. Query Planning klärt Gemeinde und Kontext und kann Einschränkungen setzen. Oracle-Patterns in einer kontrollierten Studie sind kein Nachweis, dass freie Nutzerfragen genauso sicher aufgelöst werden.

## Antwort, Citation Support und Abstention

Eine Antwort wird an einen tatsächlichen Retrievalstand gebunden. Die Zitatregistry prüft Identität; spanbasierte Prüfungen sichern Textstützung. Darüber hinaus müssen Fragebezug, Zuständigkeit, Zeit und Aussageart geprüft werden. Die aktuelle extraktive Satzselektion weist Kandidaten ohne gefragten Antwortanker zurück, bevor semantische Ähnlichkeit sie sortiert. Die Regeln sind konservativ und können zusätzliche False Abstentions erzeugen. Ein kanonisch abgewiesener Entwurf ist keine erfolgreich beantwortete Frage; ein preflightgestoppter Modellarm hat keine vom Modell selbst gezeigte Abstentionskompetenz.

## Evaluation, Labels und mathematische Größen

Sei Rq die unabhängig als relevant beurteilte Menge, Lq,k die deduplizierte zurückgegebene Liste und nq=|Lq,k|. Das Sprint-Protokoll verwendet Precision@k=|Rq∩Lq,k|/nq bei ausdrücklich genanntem Rückgabenenner. Andere Publikationen verwenden einen festen Nenner k; beide Konventionen sind auszuweisen und nicht zu vermischen. Recall@k=|Rq∩Lq,k|/|Rq| benötigt eine hinreichend vollständige positive Referenzmenge. Bei fehlenden oder ungeklärten Labels sind diese Größen NOT_EVALUATED, nicht null. Bei echten negativen Aufgaben wird Abstention bewertet, kein Recall mit Nenner null.

MRR mittelt 1/rank des ersten relevanten Treffers und bildet mehrteilige Belegbündel nur unzureichend ab. DCG@k=Σ(2^rel_i−1)/log2(i+1); nDCG normalisiert durch die ideale Rangliste unter tatsächlich vorhandenen Gradlabels. Ohne IDCG oder beurteilte Alternativen ist keine sinnvolle Null zu erfinden. Bundle Completion verlangt alle im Referenzbündel erforderlichen Belege; „bekanntes Bündel vollständig“ ist bei nicht erschöpfenden Referenzen kein vollständiger Realwelt-Recall.

Für IE gelten P=TP/(TP+FP), R=TP/(TP+FN), F1=2TP/(2TP+FP+FN), jeweils mit ungewerteten/Unknown-Feldern gesondert. Micro-Pooling über Feldentscheidungen ist kein neuer unabhängiger Stichprobenumfang. Für Claims sind gestützte Claims/getrennt beurteilte Claims, ungestützte Claims, korrekte Zitatzuordnung, Scope/Zeit und Vollständigkeit getrennte Dimensionen. Nicht bewertete Claims fehlen im jeweiligen Nenner. Selective Risk misst Fehler unter beantworteten Fällen, Coverage den beantworteten Anteil; beide gehören zusammen. Eine Verweigerung aller Fragen wäre kein Erfolg von H6.

Human Validation verlangt tatsächliche Originalinspektion, individuelle Rollen, unabhängige Ersturteile und begründete Adjudikation von Konflikten. Cohen-Kappa ist (po−pe)/(1−pe) für gepaarte Urteile bei gültigem Nenner; wenige oder nicht unabhängige Paare begrenzen seine Interpretation. Ein synthetischer technischer Store bleibt nach Adjudikation `human_gold=false`. Owner-genehmigter, exponierter Pilot-Gold-Text ist nicht unabhängiges Research Gold.

## Vergleichbarkeit, Exposition und Unsicherheit

Beobachtungen sind Aufgabe×Methode; Abhängigkeitsgruppen entstehen aus Gemeinde, Dokumentfamilie, Projekt, wiederverwendetem Original, Paraphrase und geteilter Evidenz. Alle verbundenen Aufgaben gehören grundsätzlich gemeinsam in einen Split. Die 18 konservativen Relationskomponenten sind nicht automatisch 18 unabhängig zufällig gezogene Populationseinheiten. Vier E2-Gemeinden und 16 Fragen liefern keine belastbare nationale Fehlerverteilung.

Corpus, Fragen, Labels, Information, Generator und Budget müssen für den beanspruchten Kontrast übereinstimmen. E1 zeigt, dass gleiche Zeichenbudgets bei unterschiedlichen Passage-/URL-Caps keinen fair isolierten Spanvergleich ergeben. Mehr Textfenster verändern Information und Kosten zugleich. Posthoc-Kontrollen bleiben posthoc. Bereits betrachtete Ergebnisse, quarantinierte aber eingesehene Aufgaben oder agentisch erzeugte Referenzen werden durch spätere Hash-Freeze nicht blind. Die 300 Produkt-Regressionsfragen sind keine 300 unabhängigen fachlichen Goldurteile.

Keine neue Signifikanzanalyse wurde für diese Konsolidierung durchgeführt. Gepaarte, clusterbezogene Unterschiede sind bei passender Ground Truth sinnvoll; kleine Clusterzahlen, Quotenstichproben und unvollständige Labels begrenzen Intervalle und Generalisierung. Die Forschungsstufen hängen zusammen: Akquisitions-/Parserfehler verändern Extraktion, Kanten und alle Retrievalarme. Ihre Erfolgsraten dürfen weder addiert noch unter einer Unabhängigkeitsannahme multipliziert werden.

## Welcher Effekt wird tatsächlich identifiziert?

| Untersuchung | Identifizierter Gegenstand | Nicht identifiziert |
|---|---|---|
| DS-006 | Mechanischer Erhalt synthetischer Orakelbelege unter zwei Admissionbedingungen | Kommunale Retrievalqualität |
| E1-Kapazitätskontrolle | Sensitivität bekannter Quote-Recovery gegenüber Caps | Reiner Span-/Graphvorteil |
| E2-Referenzbasis | Quellintegrität, Umfang und fehlende Bewertungsnenner | Claimwahrheit oder unabhängige Relevanzleistung |
| M4-A/A2/A3 | Stufenverluste und Nebenwirkungen auf exponierten Originalstellen | Generative Antwort-/GraphRAG-Leistung |
| M4-B2/Acceleration | Zugriff auf bekannte Feldslots/Bündel bei gemeinsamem Extraktor | Allgemeine Architektur-/LLM-Überlegenheit |
| Gleiche Relationen in SQL/Graph | Ergebnisäquivalenz formalisierter Abfragen | End-to-End-Planungs- oder Speicherformatvorteil |
| Relationskarten zu BM25 | Zusatzinformation unter geänderter Repräsentation | Graphformatkausalität |
| Gleicher Kontext für Antworten | Kandidatenverhalten nach gegebenem Retrieval und Preflight | Retrievalgewinn oder unabhängige Richtigkeit |
| Produktbrowser/Studio | Funktions-/Transport-/Rollenverträge | Fachvalidierung oder externe Zugangsabnahme |

## Reproduktion und Ressourcen

Pro Studie sind Code-/Corpus-/Task-/Label-/Konfigurationshash, Seeds, Modell-/Prompt-/Tokenizerversion, k, tatsächlich zurückgegebene Items, Build-/Querykosten, Fehler und Ausschlüsse zu erhalten. Die [Befunddatei](RESEARCH_FINDINGS_AND_LIMITATIONS.md) verlinkt Originalreports; das Evidenzmanifest (lokales, hier nicht mitgeliefertes Artefakt) sichert deren gelesenen Dateistand. Es ersetzt keine Rohdatenrechte und keinen externen vertrauenswürdigen Zeitstempel.

Diese Konsolidierung verwendet gespeicherte Resultate statt neuer Modelle, Crawls oder Benchmarks. Festplattengates, RAM, API-Ausgaben und technische Abbrüche werden getrennt dokumentiert. Die acht bisherigen Exportfehler sind Festplattenvorbedingungen bei 15 GiB Reserve plus 1 GiB Ausgabebudget, kein beobachtetes RAM-OOM. Ein Replay kann wegen fehlender privater Originale oder Tokenizer-Caches nicht vollständig aus einem reinen Codecheckout gelingen; die minimale synthetische Contributor-Suite bleibt davon getrennt.

## Öffentlicher Snapshot

Publikationsedition des vorhandenen Methodentexts, kein neuer Studienplan. Historische Daten, Quelltexte und nicht freigegebene Referenzpakete bleiben privat. Die veröffentlichten Aggregatberichte ermöglichen Zahlen-/Nennerprüfung, nicht den vollständigen Replay der Originalstudien. Der Code-/Fixture-Replay wird separat in REPRODUCING.md beschrieben. Fehlender historischer Codecommit wird als unbekannt ausgewiesen; der Importcommit ist nicht der damalige Experimentcommit.
