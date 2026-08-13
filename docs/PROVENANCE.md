# Provenance Contract

## Current mismatch

Before schema version 1, `graph_triplets.segment_rowid` was overloaded: segment
mode stored a SQLite `segments.rowid`, while chunk mode stored a
`chunks.rowid`. The Neo4j exporter always dereferenced it as a segment. As a
result, chunk extraction could be exported with a wrong source or no source at
all. The exporter also only produced `Segment-[:MENTIONS]->Entity`, although
local retrieval traversed `Chunk-[:MENTIONS]->Entity`. Neither relation edges
nor answer contexts carried a stable citation contract.

## Canonical contract

Every post-migration graph artifact carries these fields:

| Field | Meaning |
|---|---|
| `source_type` | `segment` or `chunk` |
| `source_id` | Stable `segment_id` or `chunk_id`; cross-system key |
| `segment_id` / `chunk_id` | Explicit source hierarchy |
| `document_id`, `municipality_id`, `source_url` | Document origin |
| `source_text` | Extracted text, retained for citation reconstruction |
| `extraction_model`, `prompt_version`, `extraction_run_id` | Reproducible run metadata |

SQLite rowids are retained only as legacy/internal details and are not valid
Neo4j or citation identifiers.

## Migration and recovery

Migration version 1 is idempotent and recorded in `schema_migrations`. It adds
provenance columns and `graph_extraction_runs`, and replaces the old rowid-only
unique index with a partial unique index on `(source_type, source_id)`.

Existing rows are preserved as `legacy_unclassified`. The migration never
guesses whether an old rowid meant a segment or chunk; these rows are excluded
from provenance-safe export and citation until an explicit, audited legacy
classification tool exists. Recovery is therefore straightforward: restore a
SQLite backup and rerun the idempotent migration. No migration is applied to
the 27-GB Stage-0 crawl database during development; it currently has no graph
triplets to migrate.

### Corrective schema version 3

Schema version 3 is the corrective provenance enforcement layer. It validates
the complete v1/v2 marker chain before changing data and then rebuilds the
provenance-owned tables inside one SQLite savepoint where stronger `CHECK`,
foreign-key, and compound-key constraints cannot be added with `ALTER TABLE`.
The migration is restartable and idempotent; its completion marker is written
only after schema, index, trigger, and foreign-key postconditions pass.

The corrective schema enforces:

- complete extraction-run fields, allowed source and status values,
  non-negative counters, and terminal timestamp consistency;
- verified segment/chunk hierarchy and mandatory run/evidence references on
  graph artifacts;
- exact run-scoped graph uniqueness, including the partial-index predicate;
- deterministic evidence identity with explicit rejection of conflicting
  immutable metadata;
- database triggers that reject evidence snapshot updates and deletes; and
- all municipality associations from the canonical
  `municipality_documents` many-to-many table in
  `evidence_municipalities`. The scalar `municipality_id` remains a compatibility
  field and is not interpreted as the complete association set.

Existing `legacy_unclassified` graph rows are copied unchanged and are never
promoted to verified provenance. Existing verified v2 rows are backfilled only
when their complete source hierarchy, run reference, text, URL, model, and
prompt metadata validate; ambiguous or malformed rows abort and roll back the
corrective migration.

Version 3 has been exercised only against temporary and in-memory fixture
databases. Applying it to a production snapshot requires a separate backup,
preflight validation, capacity estimate for table reconstruction, and explicit
operator approval. The migration must not be run directly against
`crawler/data/db/crawl.sqlite` as part of development or test validation.

### Canonical extraction lifecycle (schema version 4)

Schema version 4 is an additive writer-lifecycle migration over the approved
v3 evidence contract. It does not change evidence IDs, source hierarchy,
immutable-evidence triggers, or verified graph provenance. It rebuilds only the
run-owned tables and their graph foreign-key target so the canonical writer can
represent the complete lifecycle:

- `running` while selected work remains;
- `completed` when every selected source is inserted or explicitly skipped and
  none failed;
- `completed_with_errors` when at least one artifact was inserted and at least
  one source failed or was rejected;
- `failed` when no artifact succeeded and a source failed/rejected, or a fatal
  orchestration error stops the run;
- `cancelled` for explicit cooperative cancellation; and
- `interrupted` for `KeyboardInterrupt` or equivalent process interruption.

`crawler.scripts.extract_graph_ollama` is the only canonical production graph
writer. It creates the run and its ordered `extraction_run_sources` ledger
before invoking Ollama. The ledger records one content hash per selected
segment/chunk, making the input set deterministic and detecting changed text
before provider or persistence work. When the canonical
`municipality_documents` table exists, the document must have an explicit row
for its declared municipality; an absent association is rejected before the
provider call. A zero-input experiment is retained as a `completed` run with
`no_inputs_selected` status detail.

Every run persists provider, model, prompt version, canonical JSON parameters,
seed, source type, database path identity, manifested `database_snapshot_id`
when one exists, initial live-state identity for an unmanifested operational
database, timestamps, counts, heartbeat, bounded status detail, and a
deterministic `extraction-config-sha256` fingerprint. A live-state ID is
explicitly not stored as a scientific snapshot ID. Canonical writable runs use
one operational database path; manifested snapshot files remain read-only.

Historical results do not suppress new experiments. The same source may be
processed by multiple runs with the same or different model, prompt,
parameters, seed, or database snapshot. Graph uniqueness is scoped to
`(run_id, source_type, source_id)`, so earlier artifacts are preserved.
Content-identical evidence is reused by its immutable content identity; changed
source text creates a new evidence snapshot. A duplicate write inside one run
raises `DuplicateRunSourceError` rather than being silently ignored.

For a successful source, one writer-owned savepoint covers:

```text
validated source hierarchy
  -> immutable evidence
  -> verified graph artifact
  -> inserted source outcome
  -> counters derived from persisted outcomes
```

Any failure rolls that savepoint back. A failure/rejection outcome is then
recorded in its own savepoint. Shared lifecycle functions never commit a
caller-owned transaction. The CLI owns and always closes its SQLite connection,
including cancellation and interruption paths.

Resume is explicit through `--resume-run RUN_ID`. It reuses the original run,
verifies its configuration and database identity, and processes only selected
sources without an outcome. Only `interrupted` runs or `running` runs whose
heartbeat exceeds `--stale-after` are resumable; a resume never creates a new
experiment silently.

The spaCy script is experimental and isolated. It writes
`demo_graph_triplets`, not canonical `graph_triplets`, evidence, outcomes, or
run records.

Known limitations:

- provider cancellation is checked before and after each source call and as
  parallel results arrive; an in-flight blocking Ollama HTTP request is bounded
  by its request timeout but is not remotely cancelled;
- unmanifested writable databases have a path identity and recorded live-state
  identity, but deliberately have no `database_snapshot_id`; and
- schema v4, like v3, is validated against fixtures only and must not be applied
  directly to the production crawl database without a separately approved
  backup and migration preflight.

## Neo4j projection contract (implemented)

The canonical snapshot/run-scoped projection is documented in
[NEO4J_PROJECTION.md](NEO4J_PROJECTION.md). Explicit `GraphArtifact`,
`EntityMention`, `Assertion` and `Evidence` nodes prevent multiple assertions
from collapsing into weighted entity relationships. SQLite remains the
authoritative immutable evidence store; Neo4j retains stable reconstruction
identifiers and metadata.

Retrieval and deterministic citation rendering over this model remain planned.
