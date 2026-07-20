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
