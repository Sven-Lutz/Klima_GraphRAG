"""Versioned, lossless provenance contract for graph extraction artifacts.

Stable application identifiers are the cross-system contract. SQLite rowids
remain legacy implementation details and are never exported as source identity.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

PROVENANCE_SCHEMA_VERSION = 1
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
    if applied:
        return

    graph_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_triplets'"
    ).fetchone()
    if graph_table:
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

    conn.executescript(
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
        );
        CREATE INDEX IF NOT EXISTS idx_graph_extraction_runs_status
            ON graph_extraction_runs(status, started_at);
        """
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (PROVENANCE_SCHEMA_VERSION, "explicit_graph_provenance", _utc_now()),
    )


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
