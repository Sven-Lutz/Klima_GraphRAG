"""Versioned migration tests for explicit graph provenance."""

from __future__ import annotations

import sqlite3

from crawler.core.provenance import PROVENANCE_SCHEMA_VERSION, ensure_provenance_schema


def _legacy_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE graph_triplets (
            id INTEGER PRIMARY KEY,
            municipality_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            segment_rowid INTEGER NOT NULL,
            segment_hash TEXT NOT NULL,
            model_name TEXT NOT NULL,
            graph_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX uq_graph_triplets_seg ON graph_triplets(segment_rowid);
        INSERT INTO graph_triplets VALUES (1, 'm1', 'd1', 42, 'hash', 'legacy', '{}');
        """
    )
    return conn


def test_migration_is_idempotent_and_preserves_legacy_rows():
    conn = _legacy_database()

    ensure_provenance_schema(conn)
    ensure_provenance_schema(conn)

    row = conn.execute(
        "SELECT segment_rowid, source_type, source_id, provenance_status FROM graph_triplets"
    ).fetchone()
    assert row == (42, None, None, "legacy_unclassified")
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_SCHEMA_VERSION,)
        ).fetchone()[0]
        == "explicit_graph_provenance"
    )
    assert conn.execute("SELECT 1 FROM graph_extraction_runs").fetchall() == []


def test_migration_allows_distinct_segment_and_chunk_source_ids():
    conn = _legacy_database()
    ensure_provenance_schema(conn)

    conn.execute(
        "UPDATE graph_triplets SET source_type='segment', source_id='seg-1', "
        "provenance_status='verified'"
    )
    conn.execute(
        """
        INSERT INTO graph_triplets(
            municipality_id, document_id, segment_rowid, segment_hash, model_name, graph_json,
            source_type, source_id, provenance_status
        ) VALUES ('m1', 'd1', 99, 'hash2', 'model', '{}', 'chunk', 'chunk-1', 'verified')
        """
    )

    assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 2


def test_repeated_initialization_removes_legacy_index():
    conn = _legacy_database()
    ensure_provenance_schema(conn)
    conn.execute("CREATE UNIQUE INDEX uq_graph_triplets_seg ON graph_triplets(segment_rowid)")

    ensure_provenance_schema(conn)

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(graph_triplets)")}
    assert "uq_graph_triplets_seg" not in indexes
    assert "uq_graph_triplets_source" in indexes
