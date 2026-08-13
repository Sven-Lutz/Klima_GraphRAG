"""Versioned, lossless provenance contract for graph extraction artifacts.

Stable application identifiers are the cross-system contract. SQLite rowids
remain legacy implementation details and are never exported as source identity.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

PROVENANCE_SCHEMA_VERSION = 1
PROVENANCE_V2_SCHEMA_VERSION = 2
PROVENANCE_V2_MIGRATION_NAME = "extraction_runs_and_evidence"
PROVENANCE_V3_SCHEMA_VERSION = 3
PROVENANCE_V3_MIGRATION_NAME = "provenance_constraints_and_immutability"
SourceType = Literal["segment", "chunk"]

_V1_MIGRATION_NAME = "explicit_graph_provenance"
_GRAPH_SOURCE_INDEX = "uq_graph_triplets_run_source"
_GRAPH_SOURCE_PREDICATE = (
    "extraction_run_id IS NOT NULL AND source_type IN ('segment', 'chunk') "
    "AND source_id IS NOT NULL"
)


class ProvenanceError(RuntimeError):
    """Raised when a graph artifact cannot be assigned lossless provenance."""


def configure_provenance_connection(conn: sqlite3.Connection) -> None:
    """Enable and verify SQLite enforcement required by provenance writes."""
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        if conn.in_transaction:
            raise ProvenanceError(
                "SQLite foreign keys cannot be enabled inside an active transaction; "
                "configure the provenance connection before beginning work"
            )
        conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ProvenanceError("SQLite foreign-key enforcement is required for provenance writes")


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
    conn: sqlite3.Connection,
    table: str,
    expected_columns: tuple[str, ...],
    *,
    expected_predicate: str | None = None,
) -> bool:
    for index in conn.execute(f'PRAGMA index_list("{table}")'):
        if not index[2]:
            continue
        columns = tuple(str(row[2]) for row in conn.execute(f'PRAGMA index_info("{index[1]}")'))
        if columns != expected_columns:
            continue
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index[1],)
        ).fetchone()
        sql = str(sql_row[0]) if sql_row and sql_row[0] else ""
        actual_predicate = _index_predicate(sql)
        if _normalize_sql(actual_predicate) == _normalize_sql(expected_predicate or ""):
            return True
    return False


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).upper()


def _index_predicate(sql: str) -> str:
    match = re.search(r"\bWHERE\b(.+)$", sql, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _index_matches(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
    predicate: str = "",
) -> bool:
    row = next(
        (entry for entry in conn.execute(f'PRAGMA index_list("{table}")') if entry[1] == name),
        None,
    )
    if row is None or bool(row[2]) is not unique:
        return False
    actual_columns = tuple(str(entry[2]) for entry in conn.execute(f'PRAGMA index_info("{name}")'))
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    sql = str(sql_row[0]) if sql_row and sql_row[0] else ""
    return actual_columns == columns and _normalize_sql(_index_predicate(sql)) == _normalize_sql(
        predicate
    )


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def _trigger_sql(conn: sqlite3.Connection, trigger: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = sorted(
        (row for row in conn.execute(f'PRAGMA table_info("{table}")') if row[5]),
        key=lambda row: row[5],
    )
    return tuple(str(row[1]) for row in rows)


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
    }


def _foreign_key_groups(
    conn: sqlite3.Connection, table: str
) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    groups: dict[int, list[sqlite3.Row | tuple]] = {}
    for row in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
        groups.setdefault(int(row[0]), []).append(row)
    return {
        (
            tuple(str(row[3]) for row in sorted(rows, key=lambda item: item[1])),
            str(rows[0][2]),
            tuple(str(row[4]) for row in sorted(rows, key=lambda item: item[1])),
        )
        for rows in groups.values()
    }


def _ensure_provenance_schema_v1(conn: sqlite3.Connection) -> None:
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
            (PROVENANCE_SCHEMA_VERSION, _V1_MIGRATION_NAME, _utc_now()),
        )


def ensure_provenance_schema(conn: sqlite3.Connection) -> None:
    """Apply provenance v1 atomically for legacy-compatible entry points."""
    configure_provenance_connection(conn)
    conn.execute("SAVEPOINT provenance_schema_v1")
    try:
        _ensure_provenance_schema_v1(conn)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT provenance_schema_v1")
        conn.execute("RELEASE SAVEPOINT provenance_schema_v1")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT provenance_schema_v1")


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
        elif not _index_matches(
            conn,
            "graph_triplets",
            _GRAPH_SOURCE_INDEX,
            ("extraction_run_id", "source_type", "source_id"),
            unique=True,
            predicate=_GRAPH_SOURCE_PREDICATE,
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
    configure_provenance_connection(conn)
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


_V1_RUN_COLUMNS = {
    "run_id",
    "extraction_model",
    "prompt_version",
    "source_type",
    "started_at",
    "completed_at",
    "status",
    "details_json",
}
_GRAPH_PROVENANCE_COLUMNS = {
    "source_type",
    "source_id",
    "segment_id",
    "chunk_id",
    "source_url",
    "source_text",
    "extraction_model",
    "prompt_version",
    "extraction_run_id",
    "provenance_status",
    "evidence_id",
}
_GRAPH_REQUIRED_COLUMNS = _GRAPH_PROVENANCE_COLUMNS | {"document_id", "municipality_id"}


def _require_schema_fragments(
    conn: sqlite3.Connection, table: str, fragments: tuple[str, ...], errors: list[str]
) -> None:
    sql = _normalize_sql(_table_sql(conn, table))
    for fragment in fragments:
        if _normalize_sql(fragment) not in sql:
            errors.append(f"invalid {table} constraint: {fragment}")


def _validate_migration_chain(conn: sqlite3.Connection, *, through_v3: bool = False) -> None:
    """Validate marker identity and the complete predecessor schema contract."""
    errors: list[str] = []
    if not _table_exists(conn, "schema_migrations"):
        raise ProvenanceError("Provenance migration chain is missing schema_migrations")
    if _table_columns(conn, "schema_migrations") != {"version", "name", "applied_at"}:
        errors.append("invalid schema_migrations column set")
    if _primary_key_columns(conn, "schema_migrations") != ("version",):
        errors.append("invalid schema_migrations primary key")
    try:
        markers = {
            int(row[0]): str(row[1])
            for row in conn.execute("SELECT version, name FROM schema_migrations")
        }
    except sqlite3.DatabaseError as exc:
        raise ProvenanceError("Invalid schema_migrations table") from exc
    expected = {
        PROVENANCE_SCHEMA_VERSION: _V1_MIGRATION_NAME,
        PROVENANCE_V2_SCHEMA_VERSION: PROVENANCE_V2_MIGRATION_NAME,
    }
    if through_v3:
        expected[PROVENANCE_V3_SCHEMA_VERSION] = PROVENANCE_V3_MIGRATION_NAME
    for version, name in expected.items():
        if markers.get(version) != name:
            errors.append(f"invalid schema migration {version} marker")

    if not _table_exists(conn, "graph_extraction_runs"):
        errors.append("missing table graph_extraction_runs")
    else:
        run_columns = _table_columns(conn, "graph_extraction_runs")
        expected_run_columns = _V1_RUN_COLUMNS | _V2_RUN_COLUMNS
        for column in sorted(expected_run_columns - run_columns):
            errors.append(f"missing graph_extraction_runs.{column}")
        if run_columns - expected_run_columns:
            errors.append("unexpected graph_extraction_runs columns")
        if _primary_key_columns(conn, "graph_extraction_runs") != ("run_id",):
            errors.append("invalid graph_extraction_runs primary key")
        _require_schema_fragments(
            conn,
            "graph_extraction_runs",
            (
                "CHECK(source_type IN ('segment', 'chunk'))",
                "CHECK(status IN ('running', 'completed', 'failed'))",
            ),
            errors,
        )

    if not _table_exists(conn, "extraction_source_outcomes"):
        errors.append("missing table extraction_source_outcomes")
    else:
        outcome_columns = _table_columns(conn, "extraction_source_outcomes")
        for column in sorted(_V2_OUTCOME_COLUMNS - outcome_columns):
            errors.append(f"missing extraction_source_outcomes.{column}")
        if outcome_columns - _V2_OUTCOME_COLUMNS:
            errors.append("unexpected extraction_source_outcomes columns")
        if _primary_key_columns(conn, "extraction_source_outcomes") != (
            "extraction_run_id",
            "source_type",
            "source_id",
        ):
            errors.append("invalid extraction_source_outcomes primary key")
        if (
            "extraction_run_id",
            "graph_extraction_runs",
            "run_id",
        ) not in _foreign_keys(conn, "extraction_source_outcomes"):
            errors.append("invalid extraction_source_outcomes run foreign key")
        _require_schema_fragments(
            conn,
            "extraction_source_outcomes",
            (
                "CHECK(source_type IN ('segment', 'chunk'))",
                "CHECK(outcome IN ('inserted', 'skipped', 'rejected', 'failed'))",
                "CHECK(retryable IN (0, 1))",
            ),
            errors,
        )

    if not _table_exists(conn, "evidence_snapshots"):
        errors.append("missing table evidence_snapshots")
    else:
        evidence_columns = _table_columns(conn, "evidence_snapshots")
        for column in sorted(_V2_EVIDENCE_COLUMNS - evidence_columns):
            errors.append(f"missing evidence_snapshots.{column}")
        if evidence_columns - _V2_EVIDENCE_COLUMNS:
            errors.append("unexpected evidence_snapshots columns")
        if _primary_key_columns(conn, "evidence_snapshots") != ("evidence_id",):
            errors.append("invalid evidence_snapshots primary key")
        if not _has_unique_index(
            conn, "evidence_snapshots", ("source_type", "source_id", "content_hash")
        ):
            errors.append("invalid evidence_snapshots uniqueness")
        _require_schema_fragments(
            conn,
            "evidence_snapshots",
            ("UNIQUE(source_type, source_id, content_hash)",),
            errors,
        )

    if _table_exists(conn, "graph_triplets"):
        for column in sorted(_GRAPH_REQUIRED_COLUMNS - _table_columns(conn, "graph_triplets")):
            errors.append(f"missing graph_triplets.{column}")
        indexes = _index_names(conn, "graph_triplets")
        if "uq_graph_triplets_source" in indexes or "uq_graph_triplets_seg" in indexes:
            errors.append("obsolete graph_triplets provenance index")
        if not _index_matches(
            conn,
            "graph_triplets",
            _GRAPH_SOURCE_INDEX,
            ("extraction_run_id", "source_type", "source_id"),
            unique=True,
            predicate=_GRAPH_SOURCE_PREDICATE,
        ):
            errors.append("invalid uq_graph_triplets_run_source index definition")

    if errors:
        raise ProvenanceError("Provenance migration chain drift detected: " + "; ".join(errors))


def _validate_provenance_v1_schema(conn: sqlite3.Connection) -> None:
    errors: list[str] = []
    if not _table_exists(conn, "graph_extraction_runs"):
        errors.append("missing table graph_extraction_runs")
    else:
        if _table_columns(conn, "graph_extraction_runs") != _V1_RUN_COLUMNS:
            errors.append("invalid graph_extraction_runs column set")
        if _primary_key_columns(conn, "graph_extraction_runs") != ("run_id",):
            errors.append("invalid graph_extraction_runs primary key")
        _require_schema_fragments(
            conn,
            "graph_extraction_runs",
            (
                "CHECK(source_type IN ('segment', 'chunk'))",
                "CHECK(status IN ('running', 'completed', 'failed'))",
            ),
            errors,
        )
    if _table_exists(conn, "graph_triplets"):
        v1_graph_columns = _GRAPH_REQUIRED_COLUMNS - {"evidence_id"}
        for column in sorted(v1_graph_columns - _table_columns(conn, "graph_triplets")):
            errors.append(f"missing graph_triplets.{column}")
        if not _index_matches(
            conn,
            "graph_triplets",
            "uq_graph_triplets_source",
            ("source_type", "source_id"),
            unique=True,
            predicate="source_type IN ('segment', 'chunk') AND source_id IS NOT NULL",
        ):
            errors.append("invalid uq_graph_triplets_source index definition")
        if "uq_graph_triplets_seg" in _index_names(conn, "graph_triplets"):
            errors.append("obsolete graph_triplets segment index")
    if errors:
        raise ProvenanceError("Provenance schema v1 drift detected: " + "; ".join(errors))


def validated_provenance_schema_version(conn: sqlite3.Connection) -> int | None:
    """Discover and validate the exact provenance schema version without migration."""
    configure_provenance_connection(conn)
    if not _table_exists(conn, "schema_migrations"):
        provenance_tables = {
            "graph_extraction_runs",
            "evidence_snapshots",
            "evidence_municipalities",
            "extraction_source_outcomes",
        }
        present_tables = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if provenance_tables & present_tables:
            raise ProvenanceError(
                "Provenance tables exist without the required schema_migrations registry"
            )
        return None
    if _table_columns(conn, "schema_migrations") != {"version", "name", "applied_at"}:
        raise ProvenanceError("Invalid provenance schema_migrations column set")
    if _primary_key_columns(conn, "schema_migrations") != ("version",):
        raise ProvenanceError("Invalid provenance schema_migrations primary key")
    markers = [
        (int(row[0]), str(row[1]))
        for row in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")
    ]
    if not markers:
        raise ProvenanceError("Provenance schema_migrations contains no migration markers")
    expected_names = {
        PROVENANCE_SCHEMA_VERSION: _V1_MIGRATION_NAME,
        PROVENANCE_V2_SCHEMA_VERSION: PROVENANCE_V2_MIGRATION_NAME,
        PROVENANCE_V3_SCHEMA_VERSION: PROVENANCE_V3_MIGRATION_NAME,
    }
    current = markers[-1][0]
    expected_markers = [
        (version, expected_names[version])
        for version in range(PROVENANCE_SCHEMA_VERSION, current + 1)
        if version in expected_names
    ]
    if current not in expected_names or markers != expected_markers:
        raise ProvenanceError("Invalid or unsupported provenance migration marker chain")
    if current == PROVENANCE_SCHEMA_VERSION:
        _validate_provenance_v1_schema(conn)
    elif current == PROVENANCE_V2_SCHEMA_VERSION:
        _validate_migration_chain(conn)
    else:
        validate_provenance_v3_schema(conn)
    return current


def _municipality_ids_for_document(
    conn: sqlite3.Connection, document_id: str, fallback: str
) -> tuple[str, ...]:
    fallback_id = fallback.strip()
    municipality_ids: set[str] = set()
    if _table_exists(conn, "municipality_documents") and {
        "municipality_id",
        "document_id",
    } <= _table_columns(conn, "municipality_documents"):
        municipality_ids.update(
            str(row[0]).strip()
            for row in conn.execute(
                "SELECT municipality_id FROM municipality_documents WHERE document_id=?",
                (document_id,),
            )
            if str(row[0]).strip()
        )
        if municipality_ids and fallback_id not in municipality_ids:
            raise ProvenanceError(
                f"Evidence municipality {fallback_id!r} is not associated with document "
                f"{document_id!r}"
            )
    if not municipality_ids and fallback_id:
        municipality_ids.add(fallback_id)
    if not municipality_ids:
        raise ProvenanceError(f"Evidence document {document_id!r} has no municipality association")
    return tuple(sorted(municipality_ids))


def _evidence_values(provenance: GraphProvenance) -> tuple[str, tuple[object, ...]]:
    provenance.validate()
    content_hash = hashlib.sha256(provenance.source_text.encode("utf-8")).hexdigest()
    identifier = evidence_id_for(provenance.source_type, provenance.source_id, content_hash)
    return identifier, (
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
    )


def _insert_v3_evidence(
    conn: sqlite3.Connection,
    table: str,
    municipality_table: str,
    provenance: GraphProvenance,
    *,
    created_at: str | None = None,
) -> str:
    identifier, immutable_values = _evidence_values(provenance)
    existing = conn.execute(
        f"""
        SELECT evidence_id, source_type, source_id, document_id, municipality_id,
               segment_id, chunk_id, source_url, evidence_text, content_hash
        FROM "{table}" WHERE evidence_id=?
        """,
        (identifier,),
    ).fetchone()
    municipality_ids = _municipality_ids_for_document(
        conn, provenance.document_id, provenance.municipality_id
    )
    if existing is not None:
        stored_municipalities = tuple(
            sorted(
                str(row[0])
                for row in conn.execute(
                    f'SELECT municipality_id FROM "{municipality_table}" WHERE evidence_id=?',
                    (identifier,),
                )
            )
        )
        if tuple(existing) != immutable_values or stored_municipalities != municipality_ids:
            raise ProvenanceError(
                f"Conflicting immutable metadata for evidence identity {identifier}"
            )
        return identifier
    if existing is None:
        try:
            conn.execute(
                f"""
                INSERT INTO "{table}"(
                    evidence_id, source_type, source_id, document_id, municipality_id,
                    segment_id, chunk_id, source_url, evidence_text, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*immutable_values, created_at or _utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            collision = conn.execute(
                f"""
                SELECT evidence_id FROM "{table}"
                WHERE source_type=? AND source_id=? AND content_hash=?
                """,
                (provenance.source_type, provenance.source_id, immutable_values[-1]),
            ).fetchone()
            if collision is None or collision[0] != identifier:
                raise ProvenanceError(
                    f"Conflicting immutable metadata for evidence identity {identifier}"
                ) from exc
            return _insert_v3_evidence(
                conn,
                table,
                municipality_table,
                provenance,
                created_at=created_at,
            )
    for municipality_id in municipality_ids:
        conn.execute(
            f'INSERT OR IGNORE INTO "{municipality_table}"(evidence_id, municipality_id) '
            "VALUES (?, ?)",
            (identifier, municipality_id),
        )
    return identifier


def _create_v3_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE graph_extraction_runs_v3 (
          run_id TEXT PRIMARY KEY,
          extraction_model TEXT NOT NULL CHECK(length(trim(extraction_model)) > 0),
          prompt_version TEXT NOT NULL CHECK(length(trim(prompt_version)) > 0),
          source_type TEXT NOT NULL CHECK(source_type IN ('segment', 'chunk')),
          started_at TEXT NOT NULL CHECK(length(trim(started_at)) > 0),
          completed_at TEXT,
          status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
          details_json TEXT, provider TEXT, parameters_json TEXT, seed INTEGER,
          database_snapshot_id TEXT,
          planned_count INTEGER NOT NULL DEFAULT 0 CHECK(planned_count >= 0),
          succeeded_count INTEGER NOT NULL DEFAULT 0 CHECK(succeeded_count >= 0),
          failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
          skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
          status_details TEXT,
          UNIQUE(run_id, source_type),
          UNIQUE(run_id, source_type, extraction_model, prompt_version),
          CHECK(succeeded_count + failed_count + skipped_count <= planned_count),
          CHECK(
            (status = 'running' AND completed_at IS NULL) OR
            (status IN ('completed', 'failed') AND completed_at IS NOT NULL)
          )
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE evidence_snapshots_v3 (
          evidence_id TEXT PRIMARY KEY
            CHECK(length(evidence_id) = 64 AND evidence_id NOT GLOB '*[^0-9a-f]*'),
          source_type TEXT NOT NULL CHECK(source_type IN ('segment', 'chunk')),
          source_id TEXT NOT NULL CHECK(length(trim(source_id)) > 0),
          document_id TEXT NOT NULL CHECK(length(trim(document_id)) > 0),
          municipality_id TEXT NOT NULL CHECK(length(trim(municipality_id)) > 0),
          segment_id TEXT, chunk_id TEXT,
          source_url TEXT NOT NULL CHECK(length(trim(source_url)) > 0),
          evidence_text TEXT NOT NULL CHECK(length(evidence_text) > 0),
          content_hash TEXT NOT NULL
            CHECK(length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
          created_at TEXT NOT NULL CHECK(length(trim(created_at)) > 0),
          UNIQUE(source_type, source_id, content_hash),
          UNIQUE(evidence_id, source_type, source_id),
          CHECK(
            (source_type = 'segment' AND segment_id = source_id AND chunk_id IS NULL) OR
            (source_type = 'chunk' AND chunk_id = source_id AND segment_id IS NOT NULL
              AND length(trim(segment_id)) > 0)
          )
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE evidence_municipalities_v3 (
          evidence_id TEXT NOT NULL,
          municipality_id TEXT NOT NULL CHECK(length(trim(municipality_id)) > 0),
          PRIMARY KEY(evidence_id, municipality_id),
          FOREIGN KEY(evidence_id) REFERENCES evidence_snapshots_v3(evidence_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE extraction_source_outcomes_v3 (
          extraction_run_id TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
          outcome TEXT NOT NULL CHECK(outcome IN ('inserted', 'skipped', 'rejected', 'failed')),
          error_category TEXT, occurred_at TEXT NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
          error_summary TEXT, PRIMARY KEY(extraction_run_id, source_type, source_id),
          CHECK(source_type IN ('segment', 'chunk')), CHECK(retryable IN (0, 1)),
          FOREIGN KEY(extraction_run_id, source_type)
            REFERENCES graph_extraction_runs_v3(run_id, source_type)
        )
        """
    )


def _graph_column_definition(row: sqlite3.Row | tuple, old_sql: str) -> str:
    name, data_type, not_null, default, primary_key = (
        str(row[1]),
        str(row[2] or ""),
        bool(row[3]),
        row[4],
        bool(row[5]),
    )
    definition = f'"{name}" {data_type}'.rstrip()
    if primary_key:
        definition += " PRIMARY KEY"
        if name == "id" and "AUTOINCREMENT" in _normalize_sql(old_sql):
            definition += " AUTOINCREMENT"
    if not_null:
        definition += " NOT NULL"
    if default is not None:
        definition += f" DEFAULT {default}"
    return definition


def _create_v3_graph_table(conn: sqlite3.Connection) -> list[str]:
    old_sql = _table_sql(conn, "graph_triplets")
    table_info = list(conn.execute('PRAGMA table_info("graph_triplets")'))
    primary_key_columns = [row for row in table_info if row[5]]
    if len(primary_key_columns) > 1:
        raise ProvenanceError("Cannot safely rebuild graph_triplets with a composite primary key")
    if list(conn.execute('PRAGMA foreign_key_list("graph_triplets")')):
        raise ProvenanceError("Cannot safely replace unowned graph_triplets foreign keys")
    existing_triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='graph_triplets' LIMIT 1"
    ).fetchone()
    if existing_triggers:
        raise ProvenanceError("Cannot safely replace unowned graph_triplets triggers")
    unexpected_automatic_unique = next(
        (
            row
            for row in conn.execute('PRAGMA index_list("graph_triplets")')
            if row[3] == "u" and row[1] not in {"uq_graph_triplets_seg", "uq_graph_triplets_source"}
        ),
        None,
    )
    if unexpected_automatic_unique:
        raise ProvenanceError("Cannot safely replace an unowned graph_triplets UNIQUE constraint")
    base_rows = [row for row in table_info if str(row[1]) not in _GRAPH_PROVENANCE_COLUMNS]
    definitions = [_graph_column_definition(row, old_sql) for row in base_rows]
    definitions.extend(
        [
            "source_type TEXT",
            "source_id TEXT",
            "segment_id TEXT",
            "chunk_id TEXT",
            "source_url TEXT",
            "source_text TEXT",
            "extraction_model TEXT",
            "prompt_version TEXT",
            "extraction_run_id TEXT",
            "provenance_status TEXT NOT NULL DEFAULT 'legacy_unclassified' "
            "CHECK(provenance_status IN ('legacy_unclassified', 'verified'))",
            "evidence_id TEXT",
            "CHECK((provenance_status = 'legacy_unclassified') OR ("
            "source_type IN ('segment', 'chunk') AND source_id IS NOT NULL "
            "AND length(trim(source_id)) > 0 AND extraction_run_id IS NOT NULL "
            "AND evidence_id IS NOT NULL AND source_url IS NOT NULL "
            "AND length(trim(source_url)) > 0 AND source_text IS NOT NULL "
            "AND length(source_text) > 0 AND extraction_model IS NOT NULL "
            "AND length(trim(extraction_model)) > 0 AND prompt_version IS NOT NULL "
            "AND length(trim(prompt_version)) > 0 AND document_id IS NOT NULL "
            "AND length(trim(document_id)) > 0 AND municipality_id IS NOT NULL "
            "AND length(trim(municipality_id)) > 0 AND ((source_type = 'segment' "
            "AND segment_id = source_id AND chunk_id IS NULL) OR (source_type = 'chunk' "
            "AND chunk_id = source_id AND segment_id IS NOT NULL "
            "AND length(trim(segment_id)) > 0))))",
            "FOREIGN KEY(extraction_run_id, source_type, extraction_model, prompt_version) "
            "REFERENCES graph_extraction_runs_v3("
            "run_id, source_type, extraction_model, prompt_version)",
            "FOREIGN KEY(evidence_id, source_type, source_id) "
            "REFERENCES evidence_snapshots_v3(evidence_id, source_type, source_id)",
        ]
    )
    conn.execute(f"CREATE TABLE graph_triplets_v3 ({', '.join(definitions)})")
    return [str(row[1]) for row in table_info]


def _copy_and_backfill_v3_data(conn: sqlite3.Connection, graph_columns: list[str]) -> None:
    run_columns = sorted(_V1_RUN_COLUMNS | _V2_RUN_COLUMNS)
    quoted_runs = ", ".join(f'"{column}"' for column in run_columns)
    conn.execute(
        f"INSERT INTO graph_extraction_runs_v3({quoted_runs}) "
        f"SELECT {quoted_runs} FROM graph_extraction_runs"
    )
    conn.execute(
        """
        INSERT INTO extraction_source_outcomes_v3(
          extraction_run_id, source_type, source_id, outcome, error_category,
          occurred_at, retryable, error_summary
        ) SELECT extraction_run_id, source_type, source_id, outcome, error_category,
                 occurred_at, retryable, error_summary FROM extraction_source_outcomes
        """
    )

    for row in conn.execute(
        """
        SELECT evidence_id, source_type, source_id, document_id, municipality_id,
               segment_id, chunk_id, source_url, evidence_text, content_hash, created_at
        FROM evidence_snapshots
        """
    ):
        provenance = GraphProvenance(
            source_type=row[1],
            source_id=row[2],
            document_id=row[3],
            municipality_id=row[4],
            segment_id=row[5],
            chunk_id=row[6],
            source_url=row[7],
            source_text=row[8],
            extraction_model="migration-validation",
            prompt_version="migration-validation",
            extraction_run_id="migration-validation",
        )
        identifier, values = _evidence_values(provenance)
        if row[0] != identifier or row[9] != values[-1]:
            raise ProvenanceError(f"Evidence {row[0]!r} has invalid content identity")
        _insert_v3_evidence(
            conn,
            "evidence_snapshots_v3",
            "evidence_municipalities_v3",
            provenance,
            created_at=row[10],
        )

    if not graph_columns:
        return
    original_row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        verified_rows = list(
            conn.execute(
                "SELECT rowid AS _migration_rowid, * FROM graph_triplets "
                "WHERE provenance_status='verified'"
            )
        )
    finally:
        conn.row_factory = original_row_factory
    for row in verified_rows:
        provenance = GraphProvenance(
            source_type=row["source_type"],
            source_id=row["source_id"],
            segment_id=row["segment_id"],
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            municipality_id=row["municipality_id"],
            source_url=row["source_url"],
            source_text=row["source_text"],
            extraction_model=row["extraction_model"],
            prompt_version=row["prompt_version"],
            extraction_run_id=row["extraction_run_id"],
        )
        identifier = _insert_v3_evidence(
            conn,
            "evidence_snapshots_v3",
            "evidence_municipalities_v3",
            provenance,
        )
        if row["evidence_id"] not in {None, identifier}:
            raise ProvenanceError(
                f"Graph source {provenance.source_id!r} references conflicting evidence"
            )
        conn.execute(
            "UPDATE graph_triplets SET evidence_id=? WHERE rowid=?",
            (identifier, row["_migration_rowid"]),
        )

    invalid_status = conn.execute(
        "SELECT provenance_status FROM graph_triplets "
        "WHERE provenance_status NOT IN ('legacy_unclassified', 'verified') LIMIT 1"
    ).fetchone()
    if invalid_status:
        raise ProvenanceError(f"Invalid graph provenance status: {invalid_status[0]!r}")
    quoted_graph = ", ".join(f'"{column}"' for column in graph_columns)
    conn.execute(
        f"INSERT INTO graph_triplets_v3({quoted_graph}) SELECT {quoted_graph} FROM graph_triplets"
    )


def _replace_v3_tables(
    conn: sqlite3.Connection, graph_columns: list[str], retained_graph_indexes: list[str]
) -> None:
    if graph_columns:
        conn.execute("DROP TABLE graph_triplets")
    conn.execute("DROP TABLE extraction_source_outcomes")
    conn.execute("DROP TABLE evidence_snapshots")
    conn.execute("DROP TABLE graph_extraction_runs")
    conn.execute("ALTER TABLE graph_extraction_runs_v3 RENAME TO graph_extraction_runs")
    conn.execute("ALTER TABLE evidence_snapshots_v3 RENAME TO evidence_snapshots")
    conn.execute("ALTER TABLE evidence_municipalities_v3 RENAME TO evidence_municipalities")
    conn.execute("ALTER TABLE extraction_source_outcomes_v3 RENAME TO extraction_source_outcomes")
    if graph_columns:
        conn.execute("ALTER TABLE graph_triplets_v3 RENAME TO graph_triplets")
        for index_sql in retained_graph_indexes:
            conn.execute(index_sql)
        conn.execute(
            "CREATE UNIQUE INDEX uq_graph_triplets_run_source "
            "ON graph_triplets(extraction_run_id, source_type, source_id) "
            f"WHERE {_GRAPH_SOURCE_PREDICATE}"
        )
        conn.execute(
            "CREATE INDEX idx_graph_triplets_provenance "
            "ON graph_triplets(provenance_status, source_type, source_id)"
        )
    conn.execute(
        "CREATE INDEX idx_graph_extraction_runs_status ON graph_extraction_runs(status, started_at)"
    )
    conn.execute("CREATE INDEX idx_evidence_source ON evidence_snapshots(source_type, source_id)")
    conn.execute(
        """
        CREATE TRIGGER evidence_snapshots_immutable_update
        BEFORE UPDATE ON evidence_snapshots
        BEGIN SELECT RAISE(ABORT, 'evidence snapshots are immutable'); END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER evidence_snapshots_immutable_delete
        BEFORE DELETE ON evidence_snapshots
        BEGIN SELECT RAISE(ABORT, 'evidence snapshots are immutable'); END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER evidence_municipalities_immutable_update
        BEFORE UPDATE ON evidence_municipalities
        BEGIN SELECT RAISE(ABORT, 'evidence municipality associations are immutable'); END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER evidence_municipalities_immutable_delete
        BEFORE DELETE ON evidence_municipalities
        BEGIN SELECT RAISE(ABORT, 'evidence municipality associations are immutable'); END
        """
    )
    if graph_columns:
        conn.execute(
            """
            CREATE TRIGGER graph_triplets_verified_no_downgrade
            BEFORE UPDATE ON graph_triplets
            WHEN OLD.provenance_status = 'verified' AND NEW.provenance_status != 'verified'
            BEGIN SELECT RAISE(ABORT, 'verified graph provenance cannot be downgraded'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER graph_triplets_verified_evidence_insert
            BEFORE INSERT ON graph_triplets WHEN NEW.provenance_status = 'verified'
            BEGIN
              SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM evidence_snapshots e
                WHERE e.evidence_id = NEW.evidence_id
                  AND e.source_type = NEW.source_type AND e.source_id = NEW.source_id
                  AND e.document_id = NEW.document_id
                  AND e.municipality_id = NEW.municipality_id
                  AND e.segment_id IS NEW.segment_id AND e.chunk_id IS NEW.chunk_id
                  AND e.source_url = NEW.source_url AND e.evidence_text = NEW.source_text
              ) THEN RAISE(ABORT, 'verified graph provenance does not match evidence') END;
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER graph_triplets_verified_evidence_update
            BEFORE UPDATE ON graph_triplets WHEN NEW.provenance_status = 'verified'
            BEGIN
              SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM evidence_snapshots e
                WHERE e.evidence_id = NEW.evidence_id
                  AND e.source_type = NEW.source_type AND e.source_id = NEW.source_id
                  AND e.document_id = NEW.document_id
                  AND e.municipality_id = NEW.municipality_id
                  AND e.segment_id IS NEW.segment_id AND e.chunk_id IS NEW.chunk_id
                  AND e.source_url = NEW.source_url AND e.evidence_text = NEW.source_text
              ) THEN RAISE(ABORT, 'verified graph provenance does not match evidence') END;
            END
            """
        )


def validate_provenance_v3_schema(conn: sqlite3.Connection) -> None:
    """Validate corrective constraints, indexes, triggers, markers, and FKs."""
    configure_provenance_connection(conn)
    _validate_migration_chain(conn, through_v3=True)
    errors: list[str] = []
    run_columns = _table_columns(conn, "graph_extraction_runs")
    if run_columns != _V1_RUN_COLUMNS | _V2_RUN_COLUMNS:
        errors.append("invalid graph_extraction_runs column set")
    _require_schema_fragments(
        conn,
        "graph_extraction_runs",
        (
            "CHECK(planned_count >= 0)",
            "CHECK(succeeded_count >= 0)",
            "CHECK(failed_count >= 0)",
            "CHECK(skipped_count >= 0)",
            "status = 'running' AND completed_at IS NULL",
            "status IN ('completed', 'failed') AND completed_at IS NOT NULL",
        ),
        errors,
    )
    for run_key in (
        ("run_id", "source_type"),
        ("run_id", "source_type", "extraction_model", "prompt_version"),
    ):
        if not _has_unique_index(conn, "graph_extraction_runs", run_key):
            errors.append(f"invalid graph_extraction_runs uniqueness {run_key}")
    required_tables = {"evidence_municipalities"}
    for table in required_tables:
        if not _table_exists(conn, table):
            errors.append(f"missing table {table}")
    if not _has_unique_index(
        conn, "evidence_snapshots", ("source_type", "source_id", "content_hash")
    ):
        errors.append("invalid evidence_snapshots content uniqueness")
    if not _has_unique_index(
        conn, "evidence_snapshots", ("evidence_id", "source_type", "source_id")
    ):
        errors.append("invalid evidence_snapshots reference uniqueness")
    _require_schema_fragments(
        conn,
        "evidence_snapshots",
        (
            "CHECK(source_type IN ('segment', 'chunk'))",
            "source_type = 'segment' AND segment_id = source_id AND chunk_id IS NULL",
            "source_type = 'chunk' AND chunk_id = source_id AND segment_id IS NOT NULL",
        ),
        errors,
    )
    for table, name, columns in (
        (
            "graph_extraction_runs",
            "idx_graph_extraction_runs_status",
            ("status", "started_at"),
        ),
        ("evidence_snapshots", "idx_evidence_source", ("source_type", "source_id")),
    ):
        if not _index_matches(conn, table, name, columns, unique=False):
            errors.append(f"invalid {name} index definition")
    trigger_names = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    immutable_trigger_contracts = {
        "evidence_snapshots_immutable_update": (
            "BEFORE UPDATE ON evidence_snapshots",
            "RAISE(ABORT, 'evidence snapshots are immutable')",
        ),
        "evidence_snapshots_immutable_delete": (
            "BEFORE DELETE ON evidence_snapshots",
            "RAISE(ABORT, 'evidence snapshots are immutable')",
        ),
        "evidence_municipalities_immutable_update": (
            "BEFORE UPDATE ON evidence_municipalities",
            "RAISE(ABORT, 'evidence municipality associations are immutable')",
        ),
        "evidence_municipalities_immutable_delete": (
            "BEFORE DELETE ON evidence_municipalities",
            "RAISE(ABORT, 'evidence municipality associations are immutable')",
        ),
    }
    for trigger, fragments in immutable_trigger_contracts.items():
        if trigger not in trigger_names:
            errors.append(f"missing trigger {trigger}")
            continue
        normalized_sql = _normalize_sql(_trigger_sql(conn, trigger))
        if any(_normalize_sql(fragment) not in normalized_sql for fragment in fragments):
            errors.append(f"invalid trigger {trigger}")
    if _table_exists(conn, "graph_triplets"):
        for trigger in {
            "graph_triplets_verified_no_downgrade",
            "graph_triplets_verified_evidence_insert",
            "graph_triplets_verified_evidence_update",
        } - trigger_names:
            errors.append(f"missing trigger {trigger}")
        if not _index_matches(
            conn,
            "graph_triplets",
            _GRAPH_SOURCE_INDEX,
            ("extraction_run_id", "source_type", "source_id"),
            unique=True,
            predicate=_GRAPH_SOURCE_PREDICATE,
        ):
            errors.append("invalid uq_graph_triplets_run_source index definition")
        if not _index_matches(
            conn,
            "graph_triplets",
            "idx_graph_triplets_provenance",
            ("provenance_status", "source_type", "source_id"),
            unique=False,
        ):
            errors.append("invalid idx_graph_triplets_provenance index definition")
        indexes = _index_names(conn, "graph_triplets")
        if "uq_graph_triplets_source" in indexes or "uq_graph_triplets_seg" in indexes:
            errors.append("obsolete graph_triplets provenance index")
        graph_fks = _foreign_key_groups(conn, "graph_triplets")
        required_graph_fks = {
            (
                ("extraction_run_id", "source_type", "extraction_model", "prompt_version"),
                "graph_extraction_runs",
                ("run_id", "source_type", "extraction_model", "prompt_version"),
            ),
            (
                ("evidence_id", "source_type", "source_id"),
                "evidence_snapshots",
                ("evidence_id", "source_type", "source_id"),
            ),
        }
        if not required_graph_fks <= graph_fks:
            errors.append("invalid graph_triplets provenance foreign keys")
        _require_schema_fragments(
            conn,
            "graph_triplets",
            (
                "CHECK(provenance_status IN ('legacy_unclassified', 'verified'))",
                "source_type = 'segment' AND segment_id = source_id AND chunk_id IS NULL",
                "source_type = 'chunk' AND chunk_id = source_id AND segment_id IS NOT NULL",
            ),
            errors,
        )
        mismatch = conn.execute(
            """
            SELECT g.rowid FROM graph_triplets g
            LEFT JOIN evidence_snapshots e
              ON e.evidence_id=g.evidence_id AND e.source_type=g.source_type
             AND e.source_id=g.source_id AND e.document_id=g.document_id
             AND e.municipality_id=g.municipality_id AND e.segment_id IS g.segment_id
             AND e.chunk_id IS g.chunk_id AND e.source_url=g.source_url
             AND e.evidence_text=g.source_text
            WHERE g.provenance_status='verified' AND e.evidence_id IS NULL LIMIT 1
            """
        ).fetchone()
        if mismatch:
            errors.append("verified graph provenance does not match immutable evidence")
    outcome_fks = _foreign_key_groups(conn, "extraction_source_outcomes")
    if (
        ("extraction_run_id", "source_type"),
        "graph_extraction_runs",
        ("run_id", "source_type"),
    ) not in outcome_fks:
        errors.append("invalid extraction_source_outcomes provenance foreign key")
    if _table_exists(conn, "evidence_municipalities"):
        if (
            ("evidence_id",),
            "evidence_snapshots",
            ("evidence_id",),
        ) not in _foreign_key_groups(conn, "evidence_municipalities"):
            errors.append("invalid evidence_municipalities evidence foreign key")
        missing_association = conn.execute(
            """
            SELECT e.evidence_id FROM evidence_snapshots e
            LEFT JOIN evidence_municipalities m
              ON m.evidence_id=e.evidence_id AND m.municipality_id=e.municipality_id
            WHERE m.evidence_id IS NULL LIMIT 1
            """
        ).fetchone()
        if missing_association:
            errors.append("evidence snapshot is missing its municipality association")
    for row in conn.execute(
        "SELECT evidence_id, source_type, source_id, evidence_text, content_hash "
        "FROM evidence_snapshots"
    ):
        content_hash = hashlib.sha256(str(row[3]).encode("utf-8")).hexdigest()
        if row[4] != content_hash or row[0] != evidence_id_for(row[1], row[2], content_hash):
            errors.append(f"invalid evidence content identity {row[0]}")
            break
    foreign_key_errors = list(conn.execute("PRAGMA foreign_key_check"))
    if foreign_key_errors:
        errors.append("foreign-key integrity check failed")
    if errors:
        raise ProvenanceError("Provenance schema v3 drift detected: " + "; ".join(errors))


def ensure_provenance_v3(conn: sqlite3.Connection) -> None:
    """Apply the atomic corrective provenance migration on a validated database."""
    configure_provenance_connection(conn)
    conn.execute("SAVEPOINT provenance_schema_v3")
    try:
        ensure_provenance_v2(conn)
        marker = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (PROVENANCE_V3_SCHEMA_VERSION,),
        ).fetchone()
        if marker is not None:
            if marker[0] != PROVENANCE_V3_MIGRATION_NAME:
                raise ProvenanceError("Invalid provenance schema v3 migration marker")
            validate_provenance_v3_schema(conn)
        else:
            _validate_migration_chain(conn)
            retained_graph_indexes: list[str] = []
            graph_columns: list[str] = []
            if _table_exists(conn, "graph_triplets"):
                for row in conn.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='graph_triplets' AND sql IS NOT NULL"
                ):
                    if row[0] not in {
                        "uq_graph_triplets_seg",
                        "uq_graph_triplets_source",
                        "uq_graph_triplets_run_source",
                        "idx_graph_triplets_provenance",
                    }:
                        retained_graph_indexes.append(str(row[1]))
            _create_v3_tables(conn)
            if _table_exists(conn, "graph_triplets"):
                graph_columns = _create_v3_graph_table(conn)
            _copy_and_backfill_v3_data(conn, graph_columns)
            _replace_v3_tables(conn, graph_columns, retained_graph_indexes)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (PROVENANCE_V3_SCHEMA_VERSION, PROVENANCE_V3_MIGRATION_NAME, _utc_now()),
            )
            validate_provenance_v3_schema(conn)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT provenance_schema_v3")
        conn.execute("RELEASE SAVEPOINT provenance_schema_v3")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT provenance_schema_v3")


def _get_or_create_evidence(conn: sqlite3.Connection, provenance: GraphProvenance) -> str:
    if _table_exists(conn, "evidence_municipalities"):
        return _insert_v3_evidence(
            conn, "evidence_snapshots", "evidence_municipalities", provenance
        )

    identifier, immutable_values = _evidence_values(provenance)
    existing = conn.execute(
        """
        SELECT evidence_id, source_type, source_id, document_id, municipality_id,
               segment_id, chunk_id, source_url, evidence_text, content_hash
        FROM evidence_snapshots WHERE evidence_id=?
        """,
        (identifier,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != immutable_values:
            raise ProvenanceError(
                f"Conflicting immutable metadata for evidence identity {identifier}"
            )
        return identifier
    try:
        conn.execute(
            """
            INSERT INTO evidence_snapshots(
                evidence_id, source_type, source_id, document_id, municipality_id,
                segment_id, chunk_id, source_url, evidence_text, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*immutable_values, _utc_now()),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            """
            SELECT evidence_id, source_type, source_id, document_id, municipality_id,
                   segment_id, chunk_id, source_url, evidence_text, content_hash
            FROM evidence_snapshots WHERE evidence_id=?
            """,
            (identifier,),
        ).fetchone()
        if existing is None or tuple(existing) != immutable_values:
            raise ProvenanceError(
                f"Conflicting immutable metadata for evidence identity {identifier}"
            ) from exc
    return identifier


def get_or_create_evidence(conn: sqlite3.Connection, provenance: GraphProvenance) -> str:
    """Atomically persist one immutable evidence snapshot and return its identity."""
    configure_provenance_connection(conn)
    conn.execute("SAVEPOINT immutable_evidence")
    try:
        identifier = _get_or_create_evidence(conn, provenance)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT immutable_evidence")
        conn.execute("RELEASE SAVEPOINT immutable_evidence")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT immutable_evidence")
        return identifier


def start_extraction_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    extraction_model: str,
    prompt_version: str,
    source_type: SourceType,
) -> None:
    configure_provenance_connection(conn)
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
    configure_provenance_connection(conn)
    if status not in {"completed", "failed"}:
        raise ProvenanceError(f"Invalid extraction run status: {status}")
    conn.execute(
        "UPDATE graph_extraction_runs SET completed_at=?, status=? WHERE run_id=?",
        (_utc_now(), status, run_id),
    )
