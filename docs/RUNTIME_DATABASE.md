# Runtime Database and Snapshot Identity

## Runtime database contract

Every operational command resolves one immutable database binding at runtime:

1. explicit `--db`
2. `KLIMA_DB_PATH`
3. the repository default `crawler/data/db/crawl.sqlite`

The selected path is expanded and converted to an absolute path. An explicit
CLI value remains authoritative for the complete invocation; stages do not
consult the environment again after resolution. The binding exposes:

- `path`: the resolved SQLite file
- `database_id`: a deterministic local path identity
- `snapshot_id`: the manifest identity when operating on a validated snapshot
- `live_state_id`: an optional main/WAL/SHM state frozen explicitly for one
  live invocation
- `cache_namespace`: snapshot identity, frozen live-state identity, or the path
  identity before a live state is bound

The API is defined in `crawler/core/database.py`; `bind_live_database_state`
freezes the live cache identity without presenting it as a scientific checksum.

## Connection purposes

Operational connections are intentionally separated:

- `connect_existing_read_only`: requires an existing file, uses SQLite
  `mode=ro`, `query_only`, foreign keys, and a bounded busy timeout.
- `connect_existing_read_write`: requires an existing file and applies the
  shared WAL, foreign-key, synchronous, and timeout settings. It rejects any
  database claimed by a colocated snapshot manifest.
- `create_sqlite_database`: creation is explicit and limited to
  initialization, fixtures, or snapshot output.

Opening a missing operational database is an error. This prevents a typo or
working-directory change from producing an empty fallback database.

## Live validation and stable snapshots

A live WAL database cannot be validated by treating its main file as immutable:
committed schema and data may exist only in the WAL. Live preflight therefore
uses SQLite read-only access and sees the committed database state.

Scientific artifacts should instead use a stable snapshot:

```bash
python -m crawler.scripts.create_database_snapshot \
  --db crawler/data/db/crawl.sqlite \
  --output snapshots/crawl.sqlite
```

The command uses SQLite's online backup API. It includes committed WAL content
without checkpointing or modifying the source database. The output is a
single-file, DELETE-journal snapshot accompanied by
`crawl.sqlite.manifest.json` unless `--manifest` selects another
`.manifest.json` filename beside the snapshot.

## Manifest format version 2

The manifest records:

- manifest format and version
- creation timestamp
- content-derived database and manifest identities
- source path identity and sampled live-state identity
- snapshot filename, byte size, and SHA-256 checksum
- SQLite version, schema/user versions, page metadata, and journal assumptions
- the validated provenance schema version and exact migration rows, when present
- pipeline-stage completion records only when an explicit compatible completion
  table exists

Absence of completion records is represented as `not_recorded`; table contents
are never interpreted as proof that a stage completed.

Validation recomputes the canonical manifest ID, verifies size and checksum,
rejects WAL/SHM sidecars, then opens the checksum-validated snapshot with
SQLite's immutable read-only mode and compares its metadata with the manifest.
If provenance migrations are present, validation verifies the complete v1, v2,
or v3 schema contract, including v3 evidence-immutability triggers, without
attempting a migration. A malformed provenance schema cannot become a valid
snapshot.
Moving a snapshot and its unchanged manifest together does not change their
identity.
