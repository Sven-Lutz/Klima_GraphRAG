"""Versioned, lossless provenance contract for graph extraction artifacts.

Stable application identifiers are the cross-system contract. SQLite rowids
remain legacy implementation details and are never exported as source identity.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

PROVENANCE_SCHEMA_VERSION = 1
PROVENANCE_V2_SCHEMA_VERSION = 2
PROVENANCE_V2_MIGRATION_NAME = "extraction_runs_and_evidence"
SourceType = Literal["segment", "chunk"]


class ProvenanceError(RuntimeError):
    """Raised when a graph artifact cannot be assigned lossless provenance."""


@dataclass(frozen=True)
class GraphProvenance:
    """Lossless extraction input identity persisted with every graph artifact."""

    source_type: SourceType
    source_id: str
    segment_id: str | None
    chunk_id: str | None
    document_id: str
    municipality_id: str
    source_url: str
    source_text: str
    extraction_model: str
    prompt_version: str
    extraction_run_id: str

    def validate(self) -> None:
        if self.source_type not in {"segment", "chunk"}:
            raise ProvenanceError(f"Unsupported extraction source type: {self.source_type!r}")
        required = {
            "source_id": self.source_id,
            "document_id": self.document_id,
            "municipality_id": self.municipality_id,
            "source_url": self.source_url,
            "source_text": self.source_text,
            "extraction_model": self.extraction_model,
            "prompt_version": self.prompt_version,
            "extraction_run_id": self.extraction_run_id,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ProvenanceError(f"Incomplete graph provenance; missing {', '.join(missing)}")
        if self.source_type == "segment":
            if self.segment_id != self.source_id or self.chunk_id is not None:
                raise ProvenanceError(
                    "Segment provenance must use source_id=segment_id and no chunk_id"
                )
        elif not self.segment_id or self.chunk_id != self.source_id:
            raise ProvenanceError(
                "Chunk provenance must include parent segment_id and source_id=chunk_id"
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, ddl_type: str) -> None:
    if name not in _table_columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl_type}')


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA index_list("{table}")')}


def _has_unique_index(
    conn: sqlite3.Connection, table: str, expected_columns: tuple[str, ...]
) -> bool:
    for index in conn.execute(f'PRAGMA index_list("{table}")'):
        if not index[2]:
            continue
        columns = tuple(str(row[2]) for row in conn.execute(f'PRAGMA index_info("{index[1]}")'))
        if columns == expected_columns:
            return True
    return False


def ensure_provenance_schema(conn: sqlite3.Connection) -> None:
    """Apply the idempotent provenance migration inside the caller's database.

    Existing rows remain untouched except for an explicit ``legacy_unclassified``
    marker. Their overloaded ``segment_rowid`` is never reinterpreted
    automatically. Operators must classify a known historical mode before such
    rows can be exported as citeable evidence.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (PROVENANCE_SCHEMA_VERSION,)
    ).fetchone()
    if _table_exists(conn, "graph_triplets"):
        additions = {
            "source_type": "TEXT",
            "source_id": "TEXT",
            "segment_id": "TEXT",
            "chunk_id": "TEXT",
            "source_url": "TEXT",
            "source_text": "TEXT",
            "extraction_model": "TEXT",
            "prompt_version": "TEXT",
            "extraction_run_id": "TEXT",
            "provenance_status": "TEXT NOT NULL DEFAULT 'legacy_unclassified'",
        }
        for name, ddl_type in additions.items():
            _add_column_if_missing(conn, "graph_triplets", name, ddl_type)
        conn.execute("DROP INDEX IF EXISTS uq_graph_triplets_seg")
        v2_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (PROVENANCE_V2_SCHEMA_VERSION,),
        ).fetchone()
        if v2_applied:
            # v2 scopes a source identity to an extraction run.  Do not recreate
            # the v1 global identity index during a later compatibility call.
            conn.execute("DROP INDEX IF EXISTS uq_graph_triplets_source")
        else:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_triplets_source
                ON graph_triplets(source_type, source_id)
                WHERE source_type IN ('segment', 'chunk') AND source_id IS NOT NULL
                """
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_triplets_provenance "
            "ON graph_triplets(provenance_status, source_type, source_id)"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_extraction_runs (
            run_id TEXT PRIMARY KEY,
            extraction_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('segment', 'chunk')),
            started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
                details_json TEXT
            )
            """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_graph_extraction_runs_status "
        "ON graph_extraction_runs(status, started_at)"
    )
    if not applied:
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (PROVENANCE_SCHEMA_VERSION, "explicit_graph_provenance", _utc_now()),
        )


def evidence_id_for(source_type: SourceType, source_id: str, content_hash: str) -> str:
    """Return a deterministic immutable evidence identity."""
    if source_type not in {"segment", "chunk"}:
        raise ProvenanceError(f"Unsupported evidence source type: {source_type!r}")
    if not source_id.strip() or not content_hash.strip():
        raise ProvenanceError("Evidence identity requires a source ID and content hash")
    return hashlib.sha256(f"{source_type}\0{source_id}\0{content_hash}".encode("utf-8")).hexdigest()


_V2_RUN_COLUMNS = {
    "provider",
    "parameters_json",
    "seed",
    "database_snapshot_id",
    "planned_count",
    "succeeded_count",
    "failed_count",
    "skipped_count",
    "status_details",
}
_V2_OUTCOME_COLUMNS = {
    "extraction_run_id",
    "source_type",
    "source_id",
    "outcome",
    "error_category",
    "occurred_at",
    "retryable",
    "error_summary",
}
_V2_EVIDENCE_COLUMNS = {
    "evidence_id",
    "source_type",
    "source_id",
    "document_id",
    "municipality_id",
    "segment_id",
    "chunk_id",
    "source_url",
    "evidence_text",
    "content_hash",
    "created_at",
}


def _validate_existing_v2_table_shapes(conn: sqlite3.Connection) -> None:
    """Reject incompatible existing v2 table names before DDL can mask drift."""
    required_columns = {
        "extraction_source_outcomes": _V2_OUTCOME_COLUMNS,
        "evidence_snapshots": _V2_EVIDENCE_COLUMNS,
    }
    missing = [
        f"missing {table}.{column}"
        for table, columns in required_columns.items()
        if _table_exists(conn, table)
        for column in sorted(columns - _table_columns(conn, table))
    ]
    if missing:
        raise ProvenanceError("Provenance schema v2 drift detected: " + "; ".join(missing))


def validate_provenance_v2_schema(
    conn: sqlite3.Connection, *, require_migration_marker: bool = True
) -> None:
    """Raise an actionable error when a v2-marked database has schema drift."""
    missing: list[str] = []
    required_tables = {
        "schema_migrations",
        "graph_extraction_runs",
        "extraction_source_outcomes",
        "evidence_snapshots",
    }
    for table in sorted(required_tables):
        if not _table_exists(conn, table):
            missing.append(f"missing table {table}")

    if require_migration_marker and _table_exists(conn, "schema_migrations"):
        marker = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (PROVENANCE_V2_SCHEMA_VERSION,),
        ).fetchone()
        if marker is None:
            missing.append(f"missing schema migration {PROVENANCE_V2_SCHEMA_VERSION}")
        elif marker[0] != PROVENANCE_V2_MIGRATION_NAME:
            missing.append(f"unexpected schema migration {PROVENANCE_V2_SCHEMA_VERSION} name")

    required_columns = {
        "graph_extraction_runs": _V2_RUN_COLUMNS,
        "extraction_source_outcomes": _V2_OUTCOME_COLUMNS,
        "evidence_snapshots": _V2_EVIDENCE_COLUMNS,
    }
    for table, columns in required_columns.items():
        if _table_exists(conn, table):
            for column in sorted(columns - _table_columns(conn, table)):
                missing.append(f"missing {table}.{column}")
    if _table_exists(conn, "graph_triplets"):
        columns = _table_columns(conn, "graph_triplets")
        if "evidence_id" not in columns:
            missing.append("missing graph_triplets.evidence_id")
        indexes = _index_names(conn, "graph_triplets")
        if "uq_graph_triplets_run_source" not in indexes:
            missing.append("missing uq_graph_triplets_run_source index")
        elif not _has_unique_index(
            conn, "graph_triplets", ("extraction_run_id", "source_type", "source_id")
        ):
            missing.append("invalid uq_graph_triplets_run_source index")
        if "uq_graph_triplets_source" in indexes:
            missing.append("obsolete uq_graph_triplets_source index")
    if _table_exists(conn, "extraction_source_outcomes") and not _has_unique_index(
        conn, "extraction_source_outcomes", ("extraction_run_id", "source_type", "source_id")
    ):
        missing.append("invalid extraction_source_outcomes uniqueness")
    if _table_exists(conn, "evidence_snapshots") and not _has_unique_index(
        conn, "evidence_snapshots", ("source_type", "source_id", "content_hash")
    ):
        missing.append("invalid evidence_snapshots uniqueness")
    if missing:
        raise ProvenanceError("Provenance schema v2 drift detected: " + "; ".join(missing))


def ensure_provenance_v2(conn: sqlite3.Connection) -> None:
    """Apply v2 atomically, leaving no completion marker after a failed attempt."""
    conn.execute("SAVEPOINT provenance_schema_v2")
    try:
        ensure_provenance_schema(conn)
        for name, ddl in {
            "provider": "TEXT",
            "parameters_json": "TEXT",
            "seed": "INTEGER",
            "database_snapshot_id": "TEXT",
            "planned_count": "INTEGER NOT NULL DEFAULT 0",
            "succeeded_count": "INTEGER NOT NULL DEFAULT 0",
            "failed_count": "INTEGER NOT NULL DEFAULT 0",
            "skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "status_details": "TEXT",
        }.items():
            _add_column_if_missing(conn, "graph_extraction_runs", name, ddl)
        _validate_existing_v2_table_shapes(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_source_outcomes (
              extraction_run_id TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
              outcome TEXT NOT NULL CHECK(outcome IN ('inserted', 'skipped', 'rejected', 'failed')),
              error_category TEXT, occurred_at TEXT NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
              error_summary TEXT, PRIMARY KEY(extraction_run_id, source_type, source_id),
              CHECK(source_type IN ('segment', 'chunk')),
              CHECK(retryable IN (0, 1)),
              FOREIGN KEY(extraction_run_id) REFERENCES graph_extraction_runs(run_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_snapshots (
              evidence_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
              document_id TEXT NOT NULL, municipality_id TEXT NOT NULL, segment_id TEXT,
              chunk_id TEXT, source_url TEXT NOT NULL, evidence_text TEXT NOT NULL,
              content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(source_type, source_id, content_hash)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_source "
            "ON evidence_snapshots(source_type, source_id)"
        )
        if _table_exists(conn, "graph_triplets"):
            _add_column_if_missing(conn, "graph_triplets", "evidence_id", "TEXT")
            conn.execute("DROP INDEX IF EXISTS uq_graph_triplets_source")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_triplets_run_source "
                "ON graph_triplets(extraction_run_id, source_type, source_id) "
                "WHERE extraction_run_id IS NOT NULL "
                "AND source_type IN ('segment', 'chunk') AND source_id IS NOT NULL"
            )
        validate_provenance_v2_schema(conn, require_migration_marker=False)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (PROVENANCE_V2_SCHEMA_VERSION, PROVENANCE_V2_MIGRATION_NAME, _utc_now()),
        )
        validate_provenance_v2_schema(conn)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT provenance_schema_v2")
        conn.execute("RELEASE SAVEPOINT provenance_schema_v2")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT provenance_schema_v2")


def get_or_create_evidence(conn: sqlite3.Connection, provenance: GraphProvenance) -> str:
    """Persist one immutable evidence snapshot and return its deterministic ID."""
    provenance.validate()
    content_hash = hashlib.sha256(provenance.source_text.encode("utf-8")).hexdigest()
    identifier = evidence_id_for(provenance.source_type, provenance.source_id, content_hash)
    conn.execute(
        """
        INSERT OR IGNORE INTO evidence_snapshots(
            evidence_id, source_type, source_id, document_id, municipality_id,
            segment_id, chunk_id, source_url, evidence_text, content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            provenance.source_type,
            provenance.source_id,
            provenance.document_id,
            provenance.municipality_id,
            provenance.segment_id,
            provenance.chunk_id,
            provenance.source_url,
            provenance.source_text,
            content_hash,
            _utc_now(),
        ),
    )
    return identifier


def start_extraction_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    extraction_model: str,
    prompt_version: str,
    source_type: SourceType,
) -> None:
    ensure_provenance_schema(conn)
    conn.execute(
        """
        INSERT INTO graph_extraction_runs(
            run_id, extraction_model, prompt_version, source_type, started_at, status
        ) VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (run_id, extraction_model, prompt_version, source_type, _utc_now()),
    )


def finish_extraction_run(conn: sqlite3.Connection, run_id: str, *, status: str) -> None:
    if status not in {"completed", "failed"}:
        raise ProvenanceError(f"Invalid extraction run status: {status}")
    conn.execute(
        "UPDATE graph_extraction_runs SET completed_at=?, status=? WHERE run_id=?",
        (_utc_now(), status, run_id),
    )
