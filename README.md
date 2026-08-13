# Klima GraphRAG

A research-grade GraphRAG platform for analysing German municipal climate-policy
information taken from official municipal web sources.

![Interactive research application in fixture mode](docs/assets/research-ui-fixture.jpg)

---

## Mission

Municipalities publish climate-policy material — concepts, funding programmes,
measures, CO₂ targets — as ordinary web pages and PDFs. That material is public
but not analysable: it is scattered across thousands of sites, unstructured, and
impossible to compare or cite reliably at scale.

This project turns that material into a corpus, a knowledge graph, and a
retrieval system in which **every generated statement can be traced back to the
municipal source document it came from**.

The repository began as a specialised municipal web crawler (`KIT KlimaCrawler`)
for the 2,056 Bavarian municipalities. It has since evolved into a
provenance-aware GraphRAG research system: the crawler is now one stage of a
pipeline whose centre of gravity is immutable evidence, reproducible artifacts,
deterministic citations, and an inspectable retrieval and answer contract.

The platform is deliberately built as **research infrastructure**, not as a
product. Its purpose is to make GraphRAG experiments on municipal data
reproducible, auditable, and honest about what they do and do not prove.

---

## Current status

### Implemented and validated

| Capability | Notes |
|---|---|
| Municipal crawler and corpus foundation | robots.txt/sitemaps, politeness per host, retry/backoff, trap detection, distributed seed-job claiming |
| SQLite source-of-truth architecture | One authoritative artifact/evidence store; every downstream stage is derived |
| Runtime database safety | Explicit resolution order (`--db` → `KLIMA_DB_PATH` → default); operational databases are never implicitly created |
| Reproducible snapshots and manifests | Consistent WAL-aware copies with content-addressed manifests |
| Provenance | Schema-enforced lineage from source URL down to individual assertions |
| Immutable evidence | Evidence snapshots are written once and resolved, never rewritten |
| Extraction lifecycle | Explicit runs with configuration, model, prompt version, seed, and terminal states |
| Repeated extraction experiments | Distinct runs coexist over the same sources without collision |
| Neo4j projection | Snapshot- and run-bound, idempotent, resumable, namespaced by projection ID |
| Live Neo4j 5 validation | Opt-in disposable-service suite; all five live tests pass, including the full UI path |
| Semantic retrieval | Embedding-artifact-bound vector retrieval over chunks |
| Graph retrieval | Bounded, parameterised Cypher over a verified projection |
| Hybrid retrieval | Reciprocal-rank fusion of the semantic and graph rankings |
| Embedding lineage | Versioned artifacts keyed by provider, model, revision, dimension, preprocessing, and corpus fingerprint |
| Deterministic citations | Stable citation identity and `C1…Cn` labels for fixed input and budget |
| Grounded answer generation | Typed, provider-independent answer contract with structural grounding validation |
| Live Ollama adapter validation | Canonical `OllamaGenerationProvider` validated against Ollama 0.32.5 / `llama3.1:8b` |
| Interactive FastAPI research UI | Semantic/graph/hybrid workflows with evidence, provenance, graph, and trace inspection |
| Fixture/demo mode | Self-contained, deterministic, never touches the production database |
| Markdown/JSON exports | Rendered by the canonical renderers, not by the UI |

### Not yet claimed

The following are **deliberately open** and are not asserted anywhere in this
repository:

- a completed scientific study on real municipal data;
- semantic faithfulness of generated answers;
- factual correctness of generated answers;
- production readiness or any operational SLA;
- global/community GraphRAG (community detection and summarisation);
- a completed large-scale or HPC experiment campaign;
- answer-quality evaluation and benchmarking results.

Structural grounding proves that citation labels resolve to real evidence and
that the sentence-coverage heuristic passed. It does **not** prove that a cited
source entails the sentence, that the source is correct, or that all relevant
evidence was retrieved.

---

## Architecture

The canonical flow, end to end:

```text
official municipal source
  -> document
  -> segment
  -> chunk
  -> embedding
  -> extraction run
  -> immutable evidence + graph artifact
  -> SQLite source of truth
  -> reproducible Neo4j projection
  -> semantic / graph / hybrid retrieval
  -> deterministic citations
  -> grounded answer
  -> interactive research UI
```

Two storage roles, kept strictly apart:

**SQLite is the authoritative artifact and evidence store.** Documents,
segments, chunks, embeddings, extraction runs, graph artifacts, and evidence
snapshots live there. It is the only place that defines what is true about the
corpus.

**Neo4j is a reproducible projection.** A projection is derived from exactly one
manifested snapshot and an explicit set of extraction runs, and is identified by
a content-addressed projection ID. It stores identity and structure for
traversal — it deliberately does not store evidence or source text. Every graph
hit is resolved back to the authoritative SQLite evidence before it can be
returned to a caller, so the graph can accelerate retrieval but can never become
a second, divergent source of truth. Rebuilding a projection from the same
snapshot and runs yields the same projection ID and the same node counts.

Detailed contracts: [docs/PIPELINE.md](docs/PIPELINE.md),
[docs/RUNTIME_DATABASE.md](docs/RUNTIME_DATABASE.md),
[docs/NEO4J_PROJECTION.md](docs/NEO4J_PROJECTION.md),
[docs/RETRIEVAL.md](docs/RETRIEVAL.md),
[docs/ANSWER_GENERATION.md](docs/ANSWER_GENERATION.md),
[docs/WEB_APPLICATION.md](docs/WEB_APPLICATION.md),
[docs/pipeline_diagrams.md](docs/pipeline_diagrams.md),
[hpc/README.md](hpc/README.md).

---

## Why provenance matters

A GraphRAG answer is only usable in research if you can reconstruct where it
came from. Every citation in this system resolves along a complete chain:

```text
Citation
  -> Evidence          (immutable snapshot of the exact text used)
  -> Segment / Chunk   (the unit that was retrieved or extracted from)
  -> Document          (the crawled artifact)
  -> Municipality      (one or more, via the document association)
  -> Extraction Run    (configuration, model, prompt version, seed)
  -> Snapshot          (content-addressed database state)
```

This makes three things possible that ordinary RAG cannot offer:

1. **Auditability.** Any sentence in an answer can be walked back to a municipal
   URL, and to the exact extraction configuration that produced the derived
   assertion behind it.
2. **Reproducibility.** Because retrieval is bound to a snapshot manifest, an
   embedding artifact, and a projection ID, an experiment can be repeated
   against exactly the state it originally ran against.
3. **Honest separation.** Authoritative source evidence and derived graph
   assertions are distinct object kinds, in the data model and in the UI. A
   derived assertion never masquerades as a source.

---

## Retrieval

Three modes, all bound to a validated snapshot manifest:

**Semantic.** The query is embedded with the same provider, model, revision,
dimension, and preprocessing identity recorded in the snapshot's embedding
artifact. A lineage mismatch fails closed rather than silently comparing
incompatible vectors.

**Graph.** Bounded, fully parameterised Cypher runs against one verified
projection and an explicit extraction-run scope. The user question never becomes
Cypher. Candidate results are capped, and each hit is resolved back to
authoritative SQLite evidence before being returned.

**Hybrid.** Reciprocal-rank fusion over the semantic and graph rankings. Each
returned item keeps its per-method traces and fusion contribution, so a result's
provenance in the ranking itself stays inspectable.

Global/community retrieval, query routing, and reranking are **not implemented**
and are not claimed. Text2Cypher is deliberately disabled.

Details: [docs/RETRIEVAL.md](docs/RETRIEVAL.md).

---

## Interactive application

A local, single-process FastAPI application for evidence-centred research. The
browser is a consumer of canonical typed contracts only: it contains no SQL, no
Cypher, no retrieval, ranking, fusion, embedding, citation-identity, grounding,
provider, or provenance-reconstruction logic.

```text
Browser presentation
  -> FastAPI boundary
  -> ResearchApplicationService
  -> CanonicalRetriever / CanonicalAnswerGenerator
  -> immutable RetrievalResult / AnswerResult
```

### Fixture mode

```bash
python -m crawler.web --fixture --host 127.0.0.1 --port 8000
```

Fixture mode generates its own disposable source database, embedding artifact,
manifested snapshot, projection plan, and answer-run log in a temporary
directory, and uses a deterministic in-process generation provider. It never
reads `crawler/data/db/crawl.sqlite` and never contacts Ollama. Every result is
visibly labelled as controlled test data.

### Configured mode

Configured mode binds the application to a real manifested snapshot and fails
closed unless the database file is exactly the one the manifest binds:

```bash
mkdir -p generated/answer-runs
export KLIMA_UI_SNAPSHOT_MANIFEST=/absolute/path/snapshot.sqlite.manifest.json
export KLIMA_UI_DB_PATH=/absolute/path/snapshot.sqlite
export KLIMA_UI_ANSWER_RUN_LOG=$PWD/generated/answer-runs/ui.jsonl
python -m crawler.web --host 127.0.0.1 --port 8000
```

Graph and hybrid retrieval additionally require an explicit projection ID, an
extraction-run scope, and a dedicated read-only Neo4j principal. Answer
generation is off by default and is enabled explicitly with
`KLIMA_UI_GENERATION_ENABLED=1`. Full variable reference:
[docs/WEB_APPLICATION.md](docs/WEB_APPLICATION.md).

### What the interface shows

- **Evidence explorer** — ranked evidence cards, a filter, and a detail dialog
  that separates authoritative source evidence from derived graph assertions and
  lists citation identity, source/document/segment/chunk identity, municipality
  context, extraction runs, graph artifacts, snapshot, and the canonical URL.
- **Inline citations** — every `[C1]`-style label in an answer is a control that
  opens exactly the evidence it resolves to.
- **Graph view** — the retrieved entity/assertion/evidence/document
  neighbourhood, keyboard-navigable, with an explicit notice that connectivity is
  a derived navigation aid and not proof.
- **Retrieval trace** — per-item method traces, fusion contributions, final rank,
  deduplication identity, and snapshot/projection/run scope.
- **Provenance panel** — snapshot, mode, embedding lineage, projection,
  extraction runs, provider/model, prompt version, generation parameters,
  grounding status, provider acceptance, and observed provider runtime state.
- **Exports** — Markdown and JSON produced by the canonical renderers.

Provider **acceptance** (a project-level record that the adapter was validated
against a real service) and provider **runtime availability** (what this process
has actually observed) are reported as separate fields. A closed acceptance gate
is never displayed as live availability.

---

## Quickstart

Requirements: Python 3.11 for the test and application environment (the package
itself targets 3.10+), `pdftotext` on `PATH` for PDF parsing, and — only for the
full pipeline — Neo4j 5 and Ollama.

```bash
git clone https://github.com/Sven-Lutz/Klima_GraphRAG.git
cd Klima_GraphRAG
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m crawler.web --fixture --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, pick semantic, graph, or hybrid, and execute the
prefilled question. `requirements-dev.txt` is the lean set needed for the tests,
the CLI import smokes, and the interactive application. The heavyweight
extraction, embedding, and topic-model stack lives in
`crawler/requirements.txt`.

Full pipeline (crawl → chunk → embed → extract → resolve → snapshot → project):

```bash
.venv/bin/python -m pip install -r crawler/requirements.txt
python -m crawler.scripts.init_seed_jobs
python -m crawler.scripts.run_worker --workers 4
python -m crawler.scripts.chunk_documents   --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.generate_embeddings --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.extract_graph_ollama --db crawler/data/db/crawl.sqlite --chunk-mode --parallel 4
python -m crawler.scripts.resolve_entities  --db crawler/data/db/crawl.sqlite
python -m crawler.scripts.create_database_snapshot \
  --db crawler/data/db/crawl.sqlite --output snapshots/crawl.sqlite
# Then pass run IDs, the Neo4j target, and a ledger explicitly to
# python -m crawler.scripts.project_graph_to_neo4j
```

Supporting infrastructure:

```bash
docker compose up -d                                # Neo4j + Ollama
docker compose exec ollama ollama pull llama3.1:8b
```

Stage-by-stage reference: [docs/PIPELINE.md](docs/PIPELINE.md).

---

## Testing

**Default suite** — no external services, no network, no production database:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

**Opt-in disposable Neo4j suite** — a throwaway loopback-only Neo4j 5 container
with tmpfs storage. It is skipped unless explicitly enabled and refuses any
non-loopback endpoint:

```bash
docker compose -f tests/integration/docker-compose.neo4j.yml up -d --wait
GRAPHRAG_NEO4J_TEST_DISPOSABLE=1 \
  .venv/bin/python -m pytest -m neo4j_integration tests/test_neo4j_projection_live.py
docker compose -f tests/integration/docker-compose.neo4j.yml down --volumes
```

The live suite covers idempotent projection and constraints, interrupted-batch
resume, scope coexistence, graph→SQLite provenance resolution, the hybrid
grounded-answer path, and the complete path up to the FastAPI UI contract. See
[tests/integration/README.md](tests/integration/README.md).

GitHub Actions installs the same lean dependency set and runs collection, ruff,
and the default suite under Python 3.11 on every push.

---

## Repository structure

```text
crawler/core/       Canonical domain: crawl engine, storage, provenance, snapshots,
                    extraction, Neo4j projection, retrieval, citations, grounding,
                    answer generation, Cypher safety
crawler/scripts/    Pipeline stages, one CLI each (see docs/PIPELINE.md)
crawler/web/        Interactive FastAPI research application
                      app.py      HTTP boundary and safe error mapping
                      service.py  Application adapter over canonical contracts
                      runtime.py  Configured-mode composition root
                      fixture.py  Deterministic fixture composition root
                      static/     Dependency-free HTML/CSS/JS presentation
crawler/data/       Municipality master data and crawl seeds
demo/               Historical standalone PoC (no GPU/Neo4j/Ollama required)
docs/               Contract documentation and architecture diagrams
hpc/                SLURM pipeline for compute-heavy stages
tests/              Unit, contract, and opt-in live-integration suites
```

---

## Security and limitations

- **Source text is untrusted evidence.** Crawled text is passed to the model
  inside explicit data blocks, never as system or tool input. Angle brackets are
  escaped so evidence cannot forge a delimiter, and citation-like text inside a
  source never joins the allowed-citation list. Instructions embedded in
  retrieved text have no control role.
- **Text2Cypher is disabled** and is not imported by the retrieval or answer
  paths. User questions become embedding inputs or bound Cypher parameters —
  never query syntax.
- **Deterministic citations are not semantic faithfulness.** Citation identity,
  labels, and structural validation are deterministic. They do not establish
  entailment, factual correctness, or retrieval completeness.
- **The production database is protected.** Snapshot connections and Neo4j
  sessions are read-only, operational databases are never implicitly created,
  fixture mode uses its own disposable database, and the live test harness
  accepts only a loopback endpoint behind an explicit opt-in flag.
- **Provider errors are sanitised.** Endpoints, credentials, and stack traces
  never reach the UI or the run log, and no fallback provider is attempted.
- **This is a research platform, not a production SLA system.** The interactive
  application is a local single-process research tool; its in-memory export store
  is not a multi-user history service. The answer-run JSONL is an operational
  lifecycle log, not a tamper-evident audit ledger.

---

## Thesis handover and research directions

The technical platform is a baseline. It provides reproducible artifacts,
inspectable contracts, and a working end-to-end path so that scientific work can
start from a stable foundation rather than from infrastructure.

It is intended to support future research into real municipal-data experiments,
retrieval-strategy comparison, GraphRAG methodology, evaluation design,
faithfulness, graph design, and benchmarking.

The research question, corpus selection, evaluation methodology, and all
scientific conclusions are deliberately left open.

---

## Ownership

Maintainer and author: **Sven Lutz**

---

## License

[MIT](LICENSE)
