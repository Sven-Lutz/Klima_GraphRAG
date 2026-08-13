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

## Target Neo4j contract

```text
Municipality -> HAS_DOCUMENT -> Document
Document -> HAS_SEGMENT -> Segment
Segment -> HAS_CHUNK -> Chunk
Segment|Chunk -> MENTIONS -> Entity
Entity -> typed relation -> Entity
```

Source nodes retain `source_id`, document metadata, URL and text required to
construct deterministic citations. Entity relations retain the same source
identity and evidence; no relationship is citeable without a reconstructable
source node.
