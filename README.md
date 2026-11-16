# Klima_GraphRAG — wissenschaftlicher Entwicklungsstand

Wie lassen sich kommunale Klimaschutzinformationen korrekt, vollständig, nachvollziehbar und ressourceneffizient erschließen? Dieses Forschungsprojekt vergleicht autoritative Quellenbeschaffung, Parsing, Information Extraction, strukturierte Evidenz und Retrieval. **GraphRAG ist eine prüfbare Alternative zu BM25, Dense, Hybrid und SQL, kein vorausgesetzter Gewinner.**

Dieser Branch ist ein selektierter Code-/Forschungs-Snapshot auf öffentlicher `main`-Basis. Private Entwicklungshistorie, Originalarchive, echte Reviewdaten und nicht geklärte Datenrechte werden nicht übernommen. **Keine unabhängige wissenschaftliche Validierung; keine extern abgenommene HTTPS-/OTP-Preview.**

## Einstieg

Python 3.11 und Node.js für die JS-Vertragstests. Die tatsächlich geprüften Befehle und exakten Einschränkungen stehen in [REPRODUCING.md](REPRODUCING.md), Ergebnisse in [PUBLICATION_REPORT.md](PUBLICATION_REPORT.md).

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-publication.lock
TIKTOKEN_CACHE_DIR="$PWD/.venv/tokenizer-cache" .venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
.venv/bin/python -m pytest @tests/public-tests.txt -ra
```

Die Test-Fixtures sind erfundene technische Fälle, kein kommunaler Goldstandard. Für schreibende Workspace-/Exporttests gelten unveränderte Speicherreservegates; fehlender Platz ist kein Test-PASS. Kein API-Key und keine kostenpflichtige Inferenz nötig.

## Architektur und Funktionsgrenzen

Quellenfassungen und SHA256-Provenienz → HTML/PDF-Konversion → Segmente/Chunks → typisierte Evidenz und optionale Relationen → lexikalisches/semantisches/hybrides/strukturiertes Retrieval → belegte Antwort oder explizite Abstention. SQLite bildet den lokalen Evidenzkern; Neo4j-Projektion und read-only Text2Cypher sind optionale Grenzen. Browser- und technische Reviewkomponenten sind Forschungssoftware. Ein synthetischer Adjudikationslauf erzeugt kein unabhängiges Human Gold.

Die vollständige lokale Corpus-Preview wird durch diesen Quellcodeexport nicht als öffentlich lauffähige Demo zugesagt. Fehlende Quellen, Modellgewichte, Rights-Register und ungeklärte optionale Komponenten werden in [PUBLICATION_SCOPE.md](PUBLICATION_SCOPE.md) konkret benannt. Originaldokumente und aktuelle Realweltzustände können nicht aus leeren Beispieldaten abgeleitet werden.

## Forschung und Mitarbeit

- [Verständlicher Forschungsüberblick](RESEARCH_OVERVIEW_DE.md)
- [Methodik und bestehende RQ1–RQ8/H1–H6](RESEARCH_METHODOLOGY.md)
- [Studiengetrennte Befunde und Grenzen](RESEARCH_FINDINGS_AND_LIMITATIONS.md)
- [Reproduktion](REPRODUCING.md), [Beiträge](CONTRIBUTING.md), [Lizenz-/Datenpolitik](DATA_POLICY.md)
- [Bestehender Contributor Hub #15](https://github.com/Sven-Lutz/Klima_GraphRAG/issues/15), [Release-Cutline #16](https://github.com/Sven-Lutz/Klima_GraphRAG/issues/16)

Bitte keine zweite Grundlagenphase und keine neuen parallelen Issues für bereits vorhandene Module. Unabhängige Originalannotation, methodenblind gepoolte Referenzen und faire Vergleichsbedingungen bleiben nächste wissenschaftliche Aufgaben. Technische Funktionsprüfungen und explorative Bestandszahlen ersetzen diese nicht.

## Conversational Research v1 (Featurebranch)

```bash
python -m crawler.web --fixture --directory generated/chat-demo --port 8825
```

Öffnet die lokale synthetische Chatkomposition auf http://127.0.0.1:8825, sofern die unveränderte Speicherreserve erfüllt ist. Chat, Research und Validation bleiben getrennte Arbeitsräume. [Technische Übergabe und echte Grenzen](docs/product/CONVERSATIONAL_RESEARCH_HANDOFF.md), [Paper-Dossier](docs/paper/RESEARCH_DOSSIER.md). Keine neue fachliche Accuracy-/UX-Überlegenheit und kein externer Preview-Zugang behauptet. Vollcorpusintegration bleibt wegen E1-/Datenrechten eingeschränkt.
