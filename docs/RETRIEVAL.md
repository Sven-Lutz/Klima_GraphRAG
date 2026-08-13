# Canonical retrieval and deterministic citations

Status: **implemented** for manifested SQLite snapshots, canonical chunk
embedding artifacts, and bounded one-hop reads from the approved Neo4j
projection. Answer-context construction and deterministic grounding validation
are implemented. LLM answer quality, semantic faithfulness, global/community
retrieval, Text2Cypher, and an interactive UI are **unsupported**.

## Architecture and authority

`crawler.core.retrieval.CanonicalRetriever` is the only production retrieval
abstraction. It returns typed `RetrievalResult` and `RetrievedEvidence` values;
it never returns anonymous Neo4j records or text without source identity.

Each evidence result contains:

- manifested `database_snapshot_id`;
- stable evidence, source, segment, optional chunk, and document IDs;
- every associated municipality and the canonical source URL;
- heading context when present;
- extraction-run, graph-artifact, and assertion IDs where graph extraction
  applies;
- method-specific trace, score, fusion contribution, and final rank.

SQLite remains authoritative. Neo4j supplies graph navigation identities only.
Every graph hit is resolved to a `verified` SQLite `evidence_snapshots` /
`graph_triplets` record before it may leave the retriever. SQLite rowids and
Neo4j internal IDs are not application identities.

## Snapshot binding

Every request requires a validated version-2 snapshot manifest. Validation
rechecks manifest identity, SQLite checksum, database identity, and schema
metadata. The query CLI also requires `--db`; it must be the exact file named
by `--manifest`.

Live SQLite state is intentionally rejected by this interface. A changed live
database must be exported again and receives a different snapshot identity.
There is no fallback database and a missing path is never created.

## Embedding lineage

`generate_embeddings` now writes versioned `embedding_artifacts` and
`chunk_embeddings` tables. A complete artifact binds:

- provider, model, explicit model revision, and vector dimension;
- preprocessing/chunking identity;
- source database identity;
- deterministic corpus fingerprint over ordered stable chunk IDs and content
  hashes;
- per-vector chunk ID, source-content hash, dimension, and timestamp;
- planned/embedded counts and lifecycle status.

Retrieval selects exactly one `completed` artifact matching the query embedder,
manifest source, and current snapshot corpus. It validates exact coverage,
including the ordered chunk-ID membership rather than counts alone, per-vector
content hashes, dimensions, finite vectors, and query-vector shape. Artifact
creation uses an atomic single-writer claim; a concurrent or interrupted
`running` owner fails closed instead of allowing a second writer to corrupt its
lifecycle state.
Partial, mixed, stale, ambiguous, or legacy `chunks.embedding` data fails
closed. Historical blobs are not rewritten or represented as verified.

The default revision value `unversioned` is explicit but weaker than a pinned
model commit. Reproducible scientific runs should pass a concrete
`--model-revision` and ensure that the local model loader resolves that same
revision. Automatic model-download provenance is not yet implemented.

## Retrieval modes

### Semantic (`implemented`)

The query is embedded with the artifact-compatible model. Cosine similarity is
computed over the complete canonical chunk artifact. Results are bounded by
`top_k` (1–100) and sorted by descending score, then stable chunk ID. Missing
or partial corpus coverage is an error, not an empty successful result.

### Graph (`implemented`)

Graph retrieval requires an explicit `projection_id`. The retriever first
checks the projection's snapshot and, when supplied, exact extraction-run set.
It then executes one fixed, parameterized, application-owned Cypher query:

- query terms are values, never executable Cypher;
- entity matching expands to supported assertions at exactly one hop;
- the selected projection must include the extraction run that produced the
  deterministically identified graph artifact;
- every assertion must have a `SUPPORTED_BY` evidence edge;
- candidates are capped at 500 and final results at `top_k`;
- the driver transmits a per-query server transaction timeout and uses read
  access;
- returned application IDs and immutable evidence-municipality associations
  are verified against authoritative SQLite.

Graph connectivity does not establish authority and unsupported graph facts are
never returned. Evidence is ranked by descending count of its matched supported
assertions and then stable evidence ID. The CLI requires an explicitly
confirmed read-only Neo4j principal through
`KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED=1`.

### Hybrid (`implemented`)

Semantic cosine scores and graph ranks are not treated as comparable. Hybrid
retrieval uses reciprocal-rank fusion with constant 60:

`fusion contribution = 1 / (60 + source rank)`

Evidence is deduplicated by stable `evidence_id`. All method traces, graph
assertions, extraction runs, and fusion contributions are retained. Final ties
break by `evidence_id`, so identical query and state produce identical order.
Each input channel must supply contiguous ranks and unique evidence IDs; a
cross-channel identity collision fails rather than merging conflicting source
or snapshot metadata.

### Global/community (`deferred`)

The approved graph architecture has no canonical community-report projection
or provenance lifecycle. Global/community GraphRAG is therefore unsupported;
the retriever does not fabricate this mode.

## Citation identity and rendering

`crawler.core.citations` builds citations from already ranked evidence. The
immutable `citation_id` hashes snapshot, evidence, source, document, extraction
run, and graph-artifact identities. Display labels `C1`, `C2`, and so on
reflect context order only and do not participate in immutable identity.

Each citation resolves to the evidence, URL, document, segment/chunk,
municipalities, snapshot, extraction runs, and graph artifacts. Markdown and
structured JSON renderers format these fields without asking an LLM to emit
URLs or provenance IDs. Markdown renders both the parent segment and chunk for
chunk evidence rather than reducing that hierarchy to a display label.

## Context and grounding validation

`crawler.core.answer_context` separates fixed system instructions, the user
question, metadata, assertions, and JSON-quoted source text. Source text is
wrapped as `<untrusted_evidence>` and is explicitly declared data, not an
instruction. Its allowed citation label is adjacent to the evidence unit.

`crawler.core.grounding` validates generated answer text against that registry
and reports:

- referenced and unknown labels;
- known but disallowed labels;
- retrieved evidence unused;
- sentences requiring citations that contain none;
- citation-to-evidence resolution success.

Unknown, unsupported, and required-but-missing citations raise by default. The
validator never silently rewrites an invented label. Citation presence is only
a structural grounding signal and is not presented as semantic faithfulness.

No model-calling answer generator is enabled by this revision; the context and
validation contracts are the minimal adapter boundary for a later generator.

## CLI

Create embeddings before exporting the snapshot so their lineage tables are
included:

```bash
python -m crawler.scripts.generate_embeddings \
  --db work.sqlite \
  --model paraphrase-multilingual-MiniLM-L12-v2 \
  --model-revision unversioned
python -m crawler.scripts.create_database_snapshot \
  --db work.sqlite \
  --output snapshots/work.sqlite
```

Semantic retrieval:

```bash
python -m crawler.scripts.query \
  --db snapshots/work.sqlite \
  --manifest snapshots/work.sqlite.manifest.json \
  --mode semantic --top-k 8 --output markdown \
  "Welche kommunalen Solaranlagen werden beschrieben?"
```

Graph and hybrid retrieval additionally require the exact projection ID and a
read-only Neo4j identity. These are environment-specific commands and must use
the IDs emitted by `project_graph_to_neo4j`:

```bash
export KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED=1
export KLIMA_RETRIEVAL_NEO4J_PASSWORD='configured-outside-shell-history'
python -m crawler.scripts.query \
  --db snapshots/work.sqlite \
  --manifest snapshots/work.sqlite.manifest.json \
  --mode hybrid --projection-id "$PROJECTION_ID" \
  --neo4j-user graphrag_retrieval_reader \
  --output json "Welche Akteure bauen Solaranlagen?"
```

Text2Cypher is disabled. `crawler.scripts.query_agent` is a deprecated CLI
alias to this command; its former Python `answer()` interface fails closed.

## Known limitations

- Semantic retrieval is chunk-based; canonical segment-vector artifacts are
  not implemented.
- Graph expansion is one hop and lexical entity matching is intentionally
  simple. Graph-aware entity linking and reranking remain future work.
- Server-side Neo4j transaction timeout configuration and database-enforced
  read-only authorization are operator responsibilities in addition to client
  controls.
- No global/community retrieval, Text2Cypher, answer-quality evaluator,
  interactive UI, or production-readiness claim is included.
