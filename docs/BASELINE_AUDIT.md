# Technical Baseline Audit

## Scope and method

This audit records the repository baseline before remediation work. It is based
on static source inspection, read-only SQLite inspection, and the configured
development checks. No production logic or database artifact was modified.

Finding labels used below:

- **Confirmed defect**: directly reproducible from source or local artifacts.
- **Architectural weakness**: implemented behavior works in some cases but has
  an unsafe, inconsistent, or non-scalable design.
- **Security risk**: a realistic path permits unsafe access or mutation.
- **Documentation mismatch**: documentation contradicts implementation.
- **Unverified hypothesis**: plausible but not proven by this audit.

## Validation environment

| Item | Baseline |
| --- | --- |
| Declared Python | `>=3.10` in `pyproject.toml` |
| Available Python | CPython 3.9.6 |
| CI runtime | Python 3.11 |
| Dev dependencies | `pytest`, `ruff`, `tiktoken`, `pydantic` |
| Type-check configuration | None found |
| Formatter configuration | None found; `ruff format --check` was run as a baseline check |

The audit installed only `requirements-dev.txt` into a temporary environment
outside the repository. The test suite could not be executed on the available
Python 3.9 runtime because `dataclasses.dataclass(slots=True)` requires Python
3.10. This is an environment incompatibility, not a test failure on a
supported runtime.

| Check | Result |
| --- | --- |
| `ruff check crawler demo tests docs` | Pass |
| `ruff format --check crawler demo tests docs` | Fail: 38 files would be reformatted |
| `pytest -p no:cacheprovider` | Collection blocked on unsupported Python 3.9 |
| Type check | Not configured |
| Shell syntax check | To be run on a supported development environment together with the full test suite |

## Architecture map

```text
Municipality registry (municipalities.sqlite / CSV)
  -> init_seed_jobs.py
  -> seed_jobs in crawl SQLite
  -> run_worker.py
  -> Engine: crawl frontier, robots, sitemap, fetch, parse, score
  -> documents_raw, segments, visited, document_links
  -> chunk_documents.py -> chunks
  -> generate_embeddings.py -> chunks.embedding (+ optional Neo4j Chunk index)
  -> extract_graph_ollama.py -> graph_triplets
  -> resolve_entities.py -> entity_canonical
  -> export_graph_to_neo4j.py -> Entity / Segment / Document graph
  -> detect_communities.py -> CommunityReport / community_reports
  -> query_agent.py
       -> local vector search + graph expansion
       -> global map-reduce over community reports
       -> Text2Cypher
       -> answer critic
  -> evaluate.py
```

### Components and entry points

| Area | Entry points / modules | Current responsibility |
| --- | --- | --- |
| Registry | `generate_municipalities_csv.py`, `core/seeds.py` | Builds and reads the municipality registry and domain allowlists. |
| Crawl setup | `init_seed_jobs.py`, `core/seed_jobs.py` | Creates and atomically claims municipality jobs. |
| Crawl workers | `run_worker.py`, `core/engine.py` | Threads claim jobs; each engine fetches, parses, scores, and persists pages. |
| Ingestion | `core/parsers/html_parser.py`, `pdf_parser.py` | HTML segments, metadata, and links; PDF text via `pdftotext`. |
| Storage | `core/storage.py` | SQLite schema initialization, raw payload persistence, segments, links, and visited state. |
| Chunking | `chunk_documents.py`, `segmentation/chunker.py` | Tiktoken 600/60 overlapping chunks. |
| Embeddings | `generate_embeddings.py` | SentenceTransformers vectors stored as SQLite BLOBs; optional Neo4j vector index. |
| Graph extraction | `extract_graph_ollama.py` | Ollama structured entity/relation extraction plus gleaning. |
| Entity resolution | `resolve_entities.py` | Curated aliases plus conservative fuzzy matching. |
| Graph persistence | `export_graph_to_neo4j.py` | Neo4j schema and batch export of documents, segments, entities, relations, and links. |
| Community layer | `detect_communities.py`, `summarize_documents.py` | GDS Leiden/Louvain reports and per-document LLM summaries. |
| Retrieval and answering | `query_agent.py` | Router, brute-force local vector search, global map-reduce, Text2Cypher, and critic loop. |
| Evaluation | `evaluate.py` | Synthetic question generation and custom LLM-as-a-judge metrics. |
| Demo UI | `demo/run_poc.sh`, `query_demo.py`, `inspect_graph.py`, `visualize_graph.py` | Local proof of concept, SQLite cosine retrieval, and PyVis graph rendering. |
| HPC | `hpc/*.sbatch`, `submit_pipeline.sh` | Slurm orchestration for embedding through evaluation stages. |

### CLI inventory

The documented principal commands are:

```bash
python -m crawler.scripts.init_seed_jobs
python -m crawler.scripts.run_worker [--db PATH] [--workers N] [--limit N]
python -m crawler.scripts.chunk_documents [--db PATH]
python -m crawler.scripts.generate_embeddings [--model MODEL] [--neo4j]
python -m crawler.scripts.extract_graph_ollama [--chunk-mode] [--parallel N]
python -m crawler.scripts.resolve_entities
python -m crawler.scripts.export_graph_to_neo4j
python -m crawler.scripts.detect_communities
python -m crawler.scripts.summarize_documents
python -m crawler.scripts.query_agent QUERY
python -m crawler.scripts.evaluate
```

Supporting commands include `prune_db.py`, `analyze_topics.py`, and
`generate_municipalities_csv.py`; the demo and Slurm scripts are separate
execution surfaces.

## Pipeline data flow

1. The municipality registry supplies AGS identifiers, seed URLs, and allowed
   domains. `seed_jobs` provides at-least-once job claiming with heartbeats.
2. `Engine` prioritizes URLs, observes robots rules and per-host delays,
   persists raw payload metadata, then parses HTML or PDF content into scored
   segments. Canonical URLs and `document_links` provide crawl deduplication
   and a document link graph.
3. High-scoring non-negative segments become token windows in `chunks`.
   Embeddings are normalized float32 vectors held in `chunks.embedding`.
4. Graph extraction processes segments or chunks and serializes a Pydantic
   knowledge graph into `graph_triplets`. Entity resolution builds a mapping
   from raw variants to canonical names.
5. Neo4j export constructs municipality, document, segment, and entity nodes;
   it exports mention, entity-entity, and document-link relationships.
6. Community detection clusters entity relationships and stores LLM-generated
   reports in Neo4j and SQLite. Per-document summaries form an additional
   coarse context layer.
7. The query router selects local search, global search, or Text2Cypher. A
   critic can request up to three additional retrieval rounds. Evaluation calls
   the same query agent and scores responses with LLM prompts.

## Dependency and configuration map

### Runtime dependencies

- Crawl/parser: `requests`, `httpx`, BeautifulSoup, `lxml`, `pandas`, and the
  external `pdftotext` executable.
- Indexing: `tiktoken`, `sentence-transformers`, `torch`, `numpy`.
- Graph/LLM: `pydantic`, `neo4j`, optional `openai`; Ollama is accessed via
  HTTP rather than a Python package.
- Analysis/demo: BERTopic/scikit-learn and optional spaCy/PyVis.

### Configuration

`crawler/core/config.py` centrally exposes `KLIMA_DB_PATH`, Neo4j endpoint and
credentials, Ollama endpoint/model, TLS behavior, embedding defaults, LLM
seed/temperature, community settings, and query limits. However, several
scripts bind `DB_PATH` while importing configuration rather than resolving a
single runtime configuration object.

Default infrastructure is Neo4j 5.26 plus Ollama via `docker-compose.yml`.
The default Neo4j password is `password`, and TLS verification for crawling is
off by default.

## Read-only database artifact baseline

| Artifact | Status |
| --- | --- |
| `crawler/data/db/municipalities.sqlite` | 901,120 bytes; readable; 2,056 municipalities; integrity check returned `ok`. |
| `crawler/data/crawl.sqlite` | 4,096 bytes; valid empty SQLite file; not the configured default path; ignored as a local legacy/accidental artifact. |
| `demo/demo.sqlite` | 20,713,472 bytes; readable; 360 documents, 4,143 segments, 3,021 chunks, and 8 triplets. |
| `crawler/data/db/crawl.sqlite` | 28,691,202,048 bytes; SQLite header is valid and the file is readable with immutable read-only access. |

The large crawl database has Unix mode `-rw-r--r--` and SQLite header fields
consistent with a 4 KiB-page SQLite database. Opening it with ordinary
`mode=ro` fails with `unable to open database file (14)`. Opening it with
`mode=ro&immutable=1` succeeds. This demonstrates that the path exists and
that SQLite can read its schema without writes or locks; it does **not** prove
corruption. The ordinary-open failure is therefore an **unverified
hypothesis** involving the environment's locking/journal behavior or an
operating-system access-control interaction. The database also has macOS
extended attributes (`com.apple.macl` and `com.apple.provenance`).

Immutable read-only inspection found the following current crawl-stage data:

| Table / metric | Value |
| --- | ---: |
| `documents_raw` | 252,624 |
| `segments` | 7,126,987 |
| `document_links` | 11,528,418 |
| `graph_triplets` | 0 |
| `document_summaries` | 0 |
| `seed_jobs` done / failed / claimed / pending | 1,014 / 46 / 7 / 989 |
| `chunks`, `entity_canonical`, `community_reports` | Not present |

The database is therefore a substantial Stage-0 crawl corpus, not a completed
GraphRAG index. It reports SQLite journal mode `delete` when opened immutable.

## Confirmed defects

| ID | Finding | Classification | Evidence / impact |
| --- | --- | --- | --- |
| D1 | `run_worker.py` imports `DEFAULT_CRAWL_DB_PATH` from `core.seed_jobs.py`, where it is not defined. | Confirmed defect | The worker entry point cannot complete its imports once its earlier runtime imports are available. |
| D2 | Worker job claiming uses CLI `--db`, while `Engine -> Storage()` uses import-bound `config.DB_PATH`. | Confirmed defect | A supplied worker database can diverge from the database receiving crawl data. |
| D3 | Chunk-mode extraction stores `chunks.rowid` in `graph_triplets.segment_rowid`; the exporter resolves it against `segments.rowid`. | Confirmed defect | Provenance can be lost or incorrectly mapped, especially where row IDs collide. |
| D4 | Local retrieval traverses `(Chunk)-[:MENTIONS]->(Entity)`, but the exporter only creates `(Segment)-[:MENTIONS]->(Entity)`. | Confirmed defect | The advertised graph-neighborhood augmentation cannot be populated by the current export path. |
| D5 | Direct script execution invokes Neo4j export with `main(limit=500)`. | Confirmed defect | A documented full export processes at most 500 triplets. |
| D6 | `prune_db.py` reports `document_summaries.summary`, while the actual schema uses `summary_text`. | Confirmed defect | The report fails when document summaries exist. |
| D7 | HPC chunk/embed script resolves entities before graph extraction. | Confirmed defect | A fresh pipeline resolves no extracted entities; stage order contradicts the intended flow. |
| D8 | `documents_raw.url_canonical` has no uniqueness constraint although storage promises canonical URL upserts. | Architectural weakness | Concurrent writes can create duplicates because lookup-and-insert is not protected by a unique key. |
| D9 | Embedding generation forces model input to 256 tokens while chunking allows 600 tokens. | Architectural weakness | Retrieval vectors silently omit content beyond the model limit. |
| D10 | Evaluation writes custom LLM-as-a-judge values while RAGAS and Claimify dependencies remain unavailable/commented. | Documentation mismatch | Documentation and Slurm labels overstate implemented evaluation methods. |
| D11 | `NEO4J_SKIP=1` is documented but not consumed by the implementation. | Documentation mismatch | Documented SQLite-only operation is unsupported. |

## Security assessment

| Risk | Classification | Evidence | Required remediation direction |
| --- | --- | --- | --- |
| LLM-generated Cypher is executed directly. | Security risk | `text2cypher()` sends raw model output to `session.run(cypher)`. | Dedicated read-only role, allowlist/parser, timeout, result cap, and safe rejection. |
| Default Neo4j credentials are public defaults. | Security risk | `neo4j/password` is in runtime configuration and Compose. | Require secrets outside defaults for non-local use; document least-privilege roles. |
| Crawl TLS verification defaults to disabled. | Security risk | `KLIMA_TLS_VERIFY=0` by default. | Make risk explicit; use a controlled exception strategy and audit insecure hosts. |
| Broad exception swallowing hides partial failure. | Architectural weakness | Crawl, extraction, export, retrieval, and evaluation contain catch-all handlers. | Persist failure state and error classification; avoid marking incomplete work as done. |
| LLM prompts contain crawled content without an explicit prompt-injection boundary. | Security risk | Raw text is passed to extraction, summary, and answer prompts. | Treat corpus text as untrusted; constrain tool calls and enforce output schemas. |

## Unverified hypotheses

| Hypothesis | Why it remains unverified | Safe next validation |
| --- | --- | --- |
| Ordinary opening of the 27-GB crawl database is blocked by SQLite locking, sandboxing, or macOS access control. | Immutable read-only opens, ordinary read-only does not; no lock-level diagnostic was available. | Inspect on a supported host with `sqlite3` diagnostics while preserving read-only access. |
| Existing raw payload files are incomplete or absent for the large corpus. | Current `Storage` expects `crawler/data/raw`, but this audit did not derive paths from every database record. | Read sample `raw_path` values immutable and compare them to filesystem entries. |
| The root `lib/` directory is an orphaned PyVis output. | Its contents match generated PyVis assets; generated `demo/graph.html` references a relative `lib/` path that may not resolve from `demo/`. | Regenerate visualization in an isolated temp directory and inspect output layout. |
| The large database will pass a full SQLite integrity scan. | Header and immutable schema/data reads work; a complete integrity result was not captured as an explicit `ok` value. | Run a dedicated immutable `PRAGMA integrity_check` on a host with sufficient I/O budget. |

## Missing tests

The repository has 55 test functions focused on canonicalization, chunking,
storage helpers, entity resolution, graph-export helpers, graph schema, and
boilerplate detection. Missing coverage includes:

- worker module import and worker/engine database identity;
- HTML/PDF parser behavior, robots handling, retries, sitemap ingestion, and
  crawl trap detection;
- schema migrations and concurrency behavior for canonical URL upserts;
- segment and chunk provenance from extraction through Neo4j and citations;
- actual Neo4j export, GDS community detection, and vector-index operations;
- Text2Cypher validation, authorization, timeouts, and mutation rejection;
- LLM provider error handling, run metadata, and partial-work recovery;
- end-to-end demo, Slurm scripts, and reproducible evaluation fixtures.

## Prioritized remediation roadmap

### P0 — execution blockers and security-critical defects

1. Fix the worker import and unify database resolution across CLI, workers,
   `Engine`, and `Storage`; add an import and same-database integration test.
2. Disable unsafe Text2Cypher execution until a read-only principal, strict
   query validator, timeout, and result limit are implemented.
3. Add explicit pipeline preflight gates so missing tables, unavailable Neo4j/
   Ollama, and incomplete predecessor stages fail before processing.

### P1 — correctness and provenance defects

1. Replace overloaded `segment_rowid` with explicit source type and source ID;
   preserve document/chunk/segment context through SQLite, Neo4j, retrieval,
   and citations.
2. Export the graph relationship shape that retrieval actually uses, or change
   retrieval to the verified graph model; prove both paths with integration
   tests.
3. Remove the 500-triplet export cap, fix the summary-report column, correct
   Slurm ordering, and enforce canonical URL uniqueness.

### P2 — architecture and maintainability improvements

1. Introduce versioned migrations, a resolved runtime configuration object,
   provider interfaces, and structured run/failure metadata.
2. Consolidate duplicate SQLite and LLM client logic; replace broad exception
   swallowing with typed errors and persisted statuses.
3. Align chunking with embedding model context limits or introduce a documented
   long-context embedding strategy.

### P3 — evaluation, performance, and research enhancements

1. Establish fixed, curated evaluation fixtures and measured baselines before
   setting quality targets.
2. Add retrieval, graph, citation, faithfulness, latency, and cost telemetry.
3. Evaluate hybrid lexical/vector/graph retrieval, reranking, confidence-aware
   extraction, and citation-first answer generation as experiments with clear
   failure criteria.

### P4 — presentation and repository polish

1. Correct README, pipeline diagrams, and HPC documentation to label features
   as implemented, experimental, planned, deprecated, or unsupported.
2. Add a reproducible supported-Python setup, locked development dependencies,
   troubleshooting guidance, artifact manifest policy, changelog, and roadmap.
3. Regenerate or remove orphaned local visualization artifacts; keep generated
   outputs isolated and ignored.

## Baseline conclusion

The repository contains a substantial crawl implementation and corpus plus a
useful demo, but the full GraphRAG system is not yet an end-to-end verified
runtime. The first implementation work should address the P0 execution and
security blockers, then establish provenance correctness before optimizing
retrieval quality or adding research features.
