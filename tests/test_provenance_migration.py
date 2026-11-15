"""Versioned migration tests for explicit graph provenance."""

from __future__ import annotations

import sqlite3

import pytest

from crawler.core.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_V2_MIGRATION_NAME,
    PROVENANCE_V2_SCHEMA_VERSION,
    PROVENANCE_V3_MIGRATION_NAME,
    PROVENANCE_V3_SCHEMA_VERSION,
    GraphProvenance,
    ProvenanceError,
    configure_provenance_connection,
    ensure_provenance_schema,
    ensure_provenance_v2,
    ensure_provenance_v3,
    evidence_id_for,
    get_or_create_evidence,
    validate_provenance_v2_schema,
    validate_provenance_v3_schema,
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


def test_failed_v1_migration_rolls_back_all_schema_changes():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(
        """
        CREATE TABLE graph_triplets (
            id INTEGER PRIMARY KEY, municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
            segment_rowid INTEGER NOT NULL, segment_hash TEXT NOT NULL,
            model_name TEXT NOT NULL, graph_json TEXT NOT NULL,
            source_type TEXT, source_id TEXT
        );
        CREATE UNIQUE INDEX uq_graph_triplets_seg ON graph_triplets(segment_rowid);
        INSERT INTO graph_triplets VALUES (1, 'm1', 'd1', 1, 'h1', 'model', '{}',
                                           'segment', 'duplicate');
        INSERT INTO graph_triplets VALUES (2, 'm1', 'd1', 2, 'h2', 'model', '{}',
                                           'segment', 'duplicate');
        """
    )
    original_columns = _columns(conn, "graph_triplets")

    with pytest.raises(sqlite3.IntegrityError):
        ensure_provenance_schema(conn)

    assert _columns(conn, "graph_triplets") == original_columns
    assert "uq_graph_triplets_seg" in {
        row[1] for row in conn.execute("PRAGMA index_list(graph_triplets)")
    }
    assert not _table_exists(conn, "schema_migrations")


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


def _v3_database() -> sqlite3.Connection:
    conn = _legacy_database()
    ensure_provenance_v3(conn)
    return conn


def test_provenance_connection_enables_and_verifies_foreign_keys():
    conn = sqlite3.connect(":memory:")
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (0,)

    configure_provenance_connection(conn)

    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_v3_orphan_source_outcome_is_rejected():
    conn = _v3_database()

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            """
            INSERT INTO extraction_source_outcomes(
                extraction_run_id, source_type, source_id, outcome, occurred_at
            ) VALUES ('missing-run', 'segment', 'seg-1', 'failed', 'now')
            """
        )


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        ("provenance_status", "'unsupported'"),
        (
            "provenance_status, source_type, source_id",
            "'verified', 'segment', NULL",
        ),
        (
            "provenance_status, source_type, source_id, segment_id, chunk_id",
            "'verified', 'chunk', 'chunk-1', NULL, 'chunk-1'",
        ),
    ],
)
def test_v3_rejects_invalid_status_and_malformed_verified_triplets(columns, values):
    conn = _v3_database()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"""
            INSERT INTO graph_triplets(
                municipality_id, document_id, segment_rowid, segment_hash,
                model_name, graph_json, {columns}
            ) VALUES ('m1', 'd1', 99, 'hash', 'model', '{{}}', {values})
            """
        )


def test_v3_rejects_incorrect_partial_index_predicate():
    conn = _v3_database()
    conn.execute("DROP INDEX uq_graph_triplets_run_source")
    conn.execute(
        """
        CREATE UNIQUE INDEX uq_graph_triplets_run_source
        ON graph_triplets(extraction_run_id, source_type, source_id)
        WHERE source_type='segment'
        """
    )

    with pytest.raises(ProvenanceError, match="index definition"):
        validate_provenance_v3_schema(conn)


def test_v3_rejects_same_name_index_with_wrong_definition():
    conn = _v3_database()
    conn.execute("DROP INDEX uq_graph_triplets_run_source")
    conn.execute(
        "CREATE INDEX uq_graph_triplets_run_source "
        "ON graph_triplets(source_type, source_id, extraction_run_id)"
    )

    with pytest.raises(ProvenanceError, match="index definition"):
        validate_provenance_v3_schema(conn)


def test_v3_rejects_invalid_run_table_despite_valid_predecessor_markers():
    conn = sqlite3.connect(":memory:")
    ensure_provenance_v2(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE extraction_source_outcomes")
    conn.execute("DROP TABLE graph_extraction_runs")
    conn.executescript(
        """
        CREATE TABLE graph_extraction_runs (
            status TEXT, started_at TEXT, provider TEXT, parameters_json TEXT, seed INTEGER,
            database_snapshot_id TEXT, planned_count INTEGER, succeeded_count INTEGER,
            failed_count INTEGER, skipped_count INTEGER, status_details TEXT
        );
        CREATE TABLE extraction_source_outcomes (
            extraction_run_id TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
            outcome TEXT NOT NULL, error_category TEXT, occurred_at TEXT NOT NULL,
            retryable INTEGER NOT NULL DEFAULT 0, error_summary TEXT,
            PRIMARY KEY(extraction_run_id, source_type, source_id),
            FOREIGN KEY(extraction_run_id) REFERENCES graph_extraction_runs(run_id)
        );
        """
    )

    with pytest.raises(ProvenanceError, match="graph_extraction_runs"):
        ensure_provenance_v3(conn)
    assert conn.execute(
        "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_V2_SCHEMA_VERSION,)
    ).fetchone() == (PROVENANCE_V2_MIGRATION_NAME,)
    assert (
        conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (PROVENANCE_V3_SCHEMA_VERSION,)
        ).fetchone()
        is None
    )


def test_v3_conflicting_evidence_metadata_is_rejected():
    conn = _v3_database()
    original = _provenance("Evidence text.")
    conflicting = GraphProvenance(
        source_type=original.source_type,
        source_id=original.source_id,
        segment_id=original.segment_id,
        chunk_id=original.chunk_id,
        document_id="doc-conflict",
        municipality_id=original.municipality_id,
        source_url=original.source_url,
        source_text=original.source_text,
        extraction_model=original.extraction_model,
        prompt_version=original.prompt_version,
        extraction_run_id=original.extraction_run_id,
    )
    get_or_create_evidence(conn, original)

    with pytest.raises(ProvenanceError, match="Conflicting immutable metadata"):
        get_or_create_evidence(conn, conflicting)


def test_v3_evidence_creation_rolls_back_if_municipality_link_fails():
    conn = _v3_database()
    conn.execute(
        """
        CREATE TRIGGER fail_evidence_municipality_insert
        BEFORE INSERT ON evidence_municipalities
        BEGIN SELECT RAISE(ABORT, 'fixture association failure'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="fixture association failure"):
        get_or_create_evidence(conn, _provenance("Evidence text."))

    assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM evidence_municipalities").fetchone() == (0,)


def test_v3_evidence_update_and_delete_are_rejected():
    conn = _v3_database()
    evidence_id = get_or_create_evidence(conn, _provenance("Evidence text."))

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE evidence_snapshots SET evidence_text='changed' WHERE evidence_id=?",
            (evidence_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM evidence_snapshots WHERE evidence_id=?", (evidence_id,))


def test_v3_detects_predecessor_marker_corruption():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    conn.execute(
        "UPDATE schema_migrations SET name='wrong-v1' WHERE version=?",
        (PROVENANCE_SCHEMA_VERSION,),
    )

    with pytest.raises(ProvenanceError, match="migration 1 marker"):
        ensure_provenance_v3(conn)
    assert (
        conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (PROVENANCE_V3_SCHEMA_VERSION,)
        ).fetchone()
        is None
    )


def test_v3_preserves_legacy_rows_without_upgrading_them():
    conn = _legacy_database()

    ensure_provenance_v3(conn)

    assert conn.execute(
        "SELECT segment_rowid, source_type, source_id, evidence_id, provenance_status "
        "FROM graph_triplets"
    ).fetchone() == (42, None, None, None, "legacy_unclassified")


def test_v3_backfills_verified_v2_evidence_and_all_municipalities():
    conn = _legacy_database()
    conn.executescript(
        """
        CREATE TABLE municipality_documents (
            municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
            PRIMARY KEY(municipality_id, document_id)
        );
        INSERT INTO municipality_documents VALUES ('m1', 'd1');
        INSERT INTO municipality_documents VALUES ('m2', 'd1');
        """
    )
    ensure_provenance_v2(conn)
    conn.execute(
        """
        INSERT INTO graph_extraction_runs(
            run_id, extraction_model, prompt_version, source_type, started_at, status
        ) VALUES ('run-1', 'model-1', 'v1', 'segment', 'now', 'running')
        """
    )
    conn.execute(
        """
        UPDATE graph_triplets SET
          source_type='segment', source_id='seg-1', segment_id='seg-1', chunk_id=NULL,
          source_url='https://example.test/source', source_text='Evidence text.',
          extraction_model='model-1', prompt_version='v1', extraction_run_id='run-1',
          provenance_status='verified'
        """
    )

    ensure_provenance_v3(conn)

    evidence_id = conn.execute("SELECT evidence_id FROM graph_triplets").fetchone()[0]
    assert evidence_id
    assert conn.execute(
        "SELECT municipality_id FROM evidence_municipalities WHERE evidence_id=? "
        "ORDER BY municipality_id",
        (evidence_id,),
    ).fetchall() == [("m1",), ("m2",)]
    assert conn.execute(
        "SELECT evidence_text FROM evidence_snapshots WHERE evidence_id=?", (evidence_id,)
    ).fetchone() == ("Evidence text.",)


def test_failed_v3_migration_rolls_back_all_corrective_changes():
    conn = _legacy_database()
    ensure_provenance_v2(conn)
    conn.execute(
        """
        INSERT INTO graph_extraction_runs(
            run_id, extraction_model, prompt_version, source_type, started_at, status
        ) VALUES ('run-bad', 'model', 'v1', 'chunk', 'now', 'running')
        """
    )
    conn.execute(
        """
        INSERT INTO graph_triplets(
            municipality_id, document_id, segment_rowid, segment_hash, model_name, graph_json,
            source_type, source_id, chunk_id, source_url, source_text,
            extraction_model, prompt_version, extraction_run_id, provenance_status
        ) VALUES ('m1', 'd1', 99, 'hash', 'model', '{}', 'chunk', 'chunk-bad',
                  'chunk-bad', 'https://example.test', 'text', 'model', 'v1', 'run-bad',
                  'verified')
        """
    )
    original_sql = _table_sql_for_test(conn, "graph_triplets")

    with pytest.raises(ProvenanceError, match="parent segment_id"):
        ensure_provenance_v3(conn)

    assert _table_sql_for_test(conn, "graph_triplets") == original_sql
    assert not _table_exists(conn, "evidence_municipalities")
    assert not any(
        row[0].endswith("_v3")
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    assert (
        conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (PROVENANCE_V3_SCHEMA_VERSION,)
        ).fetchone()
        is None
    )


def test_v3_migration_is_idempotent_and_records_distinct_marker():
    conn = _legacy_database()

    ensure_provenance_v3(conn)
    first_sql = _table_sql_for_test(conn, "graph_triplets")
    ensure_provenance_v3(conn)

    assert _table_sql_for_test(conn, "graph_triplets") == first_sql
    assert conn.execute(
        "SELECT name FROM schema_migrations WHERE version=?", (PROVENANCE_V3_SCHEMA_VERSION,)
    ).fetchone() == (PROVENANCE_V3_MIGRATION_NAME,)


def test_v3_preserves_all_document_municipality_associations():
    conn = _legacy_database()
    conn.executescript(
        """
        CREATE TABLE municipality_documents (
            municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
            PRIMARY KEY(municipality_id, document_id)
        );
        INSERT INTO municipality_documents VALUES ('muni-1', 'doc-1');
        INSERT INTO municipality_documents VALUES ('muni-2', 'doc-1');
        """
    )
    ensure_provenance_v3(conn)

    evidence_id = get_or_create_evidence(conn, _provenance("Evidence text."))

    assert conn.execute(
        "SELECT municipality_id FROM evidence_municipalities WHERE evidence_id=? "
        "ORDER BY municipality_id",
        (evidence_id,),
    ).fetchall() == [("muni-1",), ("muni-2",)]


def test_v3_rejects_scalar_municipality_outside_canonical_document_associations():
    conn = _legacy_database()
    conn.executescript(
        """
        CREATE TABLE municipality_documents (
            municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
            PRIMARY KEY(municipality_id, document_id)
        );
        INSERT INTO municipality_documents VALUES ('muni-2', 'doc-1');
        """
    )
    ensure_provenance_v3(conn)

    with pytest.raises(ProvenanceError, match="is not associated with document"):
        get_or_create_evidence(conn, _provenance("Evidence text."))


def test_v3_run_constraints_reject_negative_counts_and_invalid_completion_state():
    conn = _v3_database()

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            """
            INSERT INTO graph_extraction_runs(
                run_id, extraction_model, prompt_version, source_type, started_at,
                status, planned_count
            ) VALUES ('negative', 'model', 'v1', 'segment', 'now', 'running', -1)
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            """
            INSERT INTO graph_extraction_runs(
                run_id, extraction_model, prompt_version, source_type, started_at,
                completed_at, status
            ) VALUES ('bad-terminal', 'model', 'v1', 'segment', 'now', NULL, 'completed')
            """
        )


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


def _table_sql_for_test(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else ""
