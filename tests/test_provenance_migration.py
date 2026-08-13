"""Versioned migration tests for explicit graph provenance."""

from __future__ import annotations

import sqlite3

import pytest

from crawler.core.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_V2_MIGRATION_NAME,
    PROVENANCE_V2_SCHEMA_VERSION,
    GraphProvenance,
    ProvenanceError,
    ensure_provenance_schema,
    ensure_provenance_v2,
    evidence_id_for,
    get_or_create_evidence,
    validate_provenance_v2_schema,
)


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


def test_v2_migration_is_idempotent_and_has_a_distinct_marker():
    conn = _legacy_database()

    ensure_provenance_v2(conn)
    ensure_provenance_v2(conn)

    assert conn.execute(
        "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_V2_SCHEMA_VERSION,)
    ).fetchone() == (PROVENANCE_V2_MIGRATION_NAME,)
    assert _columns(conn, "graph_extraction_runs") >= {
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
    assert _columns(conn, "graph_triplets") >= {"evidence_id"}
    assert conn.execute("SELECT 1 FROM extraction_source_outcomes").fetchall() == []
    assert conn.execute("SELECT 1 FROM evidence_snapshots").fetchall() == []

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(graph_triplets)")}
    assert "uq_graph_triplets_source" not in indexes
    assert "uq_graph_triplets_run_source" in indexes


def test_v2_fresh_database_migration_creates_only_v2_foundation_tables():
    conn = sqlite3.connect(":memory:")

    ensure_provenance_v2(conn)

    assert conn.execute(
        "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_V2_SCHEMA_VERSION,)
    ).fetchone() == (PROVENANCE_V2_MIGRATION_NAME,)
    assert _table_exists(conn, "graph_extraction_runs")
    assert _table_exists(conn, "extraction_source_outcomes")
    assert _table_exists(conn, "evidence_snapshots")
    assert not _table_exists(conn, "graph_triplets")


def test_v1_to_v2_migration_preserves_legacy_rows_and_source_text():
    conn = _legacy_database()
    ensure_provenance_schema(conn)

    ensure_provenance_v2(conn)

    assert conn.execute(
        "SELECT segment_rowid, source_type, source_id, source_text, provenance_status "
        "FROM graph_triplets"
    ).fetchone() == (42, None, None, None, "legacy_unclassified")


def test_v2_scopes_graph_source_uniqueness_to_extraction_run():
    conn = _legacy_database()
    ensure_provenance_v2(conn)

    def insert(run_id: str) -> None:
        conn.execute(
            """
            INSERT INTO graph_triplets(
                municipality_id, document_id, segment_rowid, segment_hash, model_name, graph_json,
                extraction_run_id, source_type, source_id
            ) VALUES ('m1', 'd1', 42, 'hash', 'model', '{}', ?, 'segment', 'seg-1')
            """,
            (run_id,),
        )

    insert("run-1")
    insert("run-2")
    with pytest.raises(sqlite3.IntegrityError):
        insert("run-2")


def test_evidence_snapshot_id_is_deterministic_and_content_addressed():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    provenance = GraphProvenance(
        source_type="segment",
        source_id="seg-1",
        segment_id="seg-1",
        chunk_id=None,
        document_id="doc-1",
        municipality_id="muni-1",
        source_url="https://example.test/source",
        source_text="Evidence text.",
        extraction_model="model-1",
        prompt_version="v1",
        extraction_run_id="run-1",
    )

    first = get_or_create_evidence(conn, provenance)
    second = get_or_create_evidence(conn, provenance)

    assert first == second
    assert first == evidence_id_for(
        "segment",
        "seg-1",
        "540b3827c843f512543cc60c3dd613c113c40a43754f9e1f95f479b397bf5864",
    )
    assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 1
    assert conn.execute(
        "SELECT document_id, evidence_text FROM evidence_snapshots WHERE evidence_id=?", (first,)
    ).fetchone() == ("doc-1", "Evidence text.")


def test_changed_evidence_text_creates_a_new_exact_snapshot():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    original = _provenance("Evidence text.")
    changed = _provenance("Evidence text, revised.")

    original_id = get_or_create_evidence(conn, original)
    changed_id = get_or_create_evidence(conn, changed)

    assert original_id != changed_id
    assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 2
    assert {row[0] for row in conn.execute("SELECT evidence_text FROM evidence_snapshots")} == {
        "Evidence text.",
        "Evidence text, revised.",
    }


def test_v2_postcondition_validator_detects_schema_drift():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    conn.execute("DROP INDEX uq_graph_triplets_run_source")

    with pytest.raises(ProvenanceError, match="uq_graph_triplets_run_source"):
        validate_provenance_v2_schema(conn)

    conn.execute(
        "CREATE UNIQUE INDEX uq_graph_triplets_run_source "
        "ON graph_triplets(extraction_run_id, source_type, source_id) "
        "WHERE extraction_run_id IS NOT NULL "
        "AND source_type IN ('segment', 'chunk') AND source_id IS NOT NULL"
    )
    conn.execute("DROP TABLE evidence_snapshots")
    conn.execute("CREATE TABLE evidence_snapshots (evidence_id TEXT PRIMARY KEY)")

    with pytest.raises(ProvenanceError, match="evidence_snapshots.content_hash"):
        validate_provenance_v2_schema(conn)


def test_v2_marker_with_unrepairable_drift_fails_without_replacing_marker():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    conn.execute("DROP TABLE evidence_snapshots")
    conn.execute("CREATE TABLE evidence_snapshots (evidence_id TEXT PRIMARY KEY)")

    with pytest.raises(ProvenanceError, match="evidence_snapshots.content_hash"):
        ensure_provenance_v2(conn)

    assert conn.execute(
        "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_V2_SCHEMA_VERSION,)
    ).fetchone() == (PROVENANCE_V2_MIGRATION_NAME,)


def test_failed_v2_migration_does_not_record_a_completion_marker():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE evidence_snapshots (evidence_id TEXT PRIMARY KEY)")

    with pytest.raises(ProvenanceError, match="evidence_snapshots.content_hash"):
        ensure_provenance_v2(conn)

    assert not _table_exists(conn, "schema_migrations")


def _provenance(source_text: str) -> GraphProvenance:
    return GraphProvenance(
        source_type="segment",
        source_id="seg-1",
        segment_id="seg-1",
        chunk_id=None,
        document_id="doc-1",
        municipality_id="muni-1",
        source_url="https://example.test/source",
        source_text=source_text,
        extraction_model="model-1",
        prompt_version="v1",
        extraction_run_id="run-1",
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )
