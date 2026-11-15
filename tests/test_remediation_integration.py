"""Cross-contract tests for provenance v3 and runtime snapshot identity."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from crawler.core.database import (
    DatabaseConfigurationError,
    bind_live_database_state,
    connect_existing_read_only,
    connect_existing_read_write,
    create_sqlite_database,
    require_existing_database,
    resolve_runtime_database,
)
from crawler.core.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_V2_SCHEMA_VERSION,
    PROVENANCE_V3_SCHEMA_VERSION,
    PROVENANCE_V4_SCHEMA_VERSION,
    GraphProvenance,
    ensure_provenance_schema,
    ensure_provenance_v2,
    ensure_provenance_v3,
    ensure_provenance_v4,
    get_or_create_evidence,
    validate_provenance_v3_schema,
)
from crawler.core.snapshots import (
    SnapshotManifestError,
    export_database_snapshot,
    validate_snapshot_manifest,
)


def _create_provenance_database(path: Path, version: int = 3) -> Path:
    migrations = {
        PROVENANCE_SCHEMA_VERSION: ensure_provenance_schema,
        PROVENANCE_V2_SCHEMA_VERSION: ensure_provenance_v2,
        PROVENANCE_V3_SCHEMA_VERSION: ensure_provenance_v3,
        PROVENANCE_V4_SCHEMA_VERSION: ensure_provenance_v4,
    }
    with create_sqlite_database(path, purpose="fixture") as connection:
        migrations[version](connection)
    return path


def _insert_evidence(path: Path) -> str:
    provenance = GraphProvenance(
        source_type="segment",
        source_id="segment-1",
        segment_id="segment-1",
        chunk_id=None,
        document_id="document-1",
        municipality_id="municipality-1",
        source_url="https://example.invalid/document-1",
        source_text="Immutable fixture evidence.",
        extraction_model="fixture-model",
        prompt_version="fixture-prompt-v1",
        extraction_run_id="fixture-run",
    )
    with connect_existing_read_write(path) as connection:
        return get_or_create_evidence(connection, provenance)


def test_runtime_write_connection_enforces_provenance_foreign_keys(tmp_path):
    database = _create_provenance_database(tmp_path / "runtime.sqlite")

    with connect_existing_read_write(database) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO extraction_source_outcomes("
                "extraction_run_id, source_type, source_id, outcome, occurred_at"
                ") VALUES ('missing-run', 'segment', 'segment-1', 'failed', 'now')"
            )


@pytest.mark.parametrize(
    ("version", "expected_names"),
    [
        (PROVENANCE_SCHEMA_VERSION, ["explicit_graph_provenance"]),
        (
            PROVENANCE_V2_SCHEMA_VERSION,
            ["explicit_graph_provenance", "extraction_runs_and_evidence"],
        ),
        (
            PROVENANCE_V3_SCHEMA_VERSION,
            [
                "explicit_graph_provenance",
                "extraction_runs_and_evidence",
                "provenance_constraints_and_immutability",
            ],
        ),
        (
            PROVENANCE_V4_SCHEMA_VERSION,
            [
                "explicit_graph_provenance",
                "extraction_runs_and_evidence",
                "provenance_constraints_and_immutability",
                "canonical_extraction_lifecycle",
            ],
        ),
    ],
)
def test_snapshot_manifest_preserves_validated_provenance_migrations(
    tmp_path, version, expected_names
):
    source = _create_provenance_database(tmp_path / "source.sqlite", version)

    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    validated = validate_snapshot_manifest(binding.manifest_path)

    assert validated.manifest["sqlite"]["provenance_schema_version"] == version
    assert [
        row["name"] for row in validated.manifest["sqlite"]["provenance_migrations"]
    ] == expected_names


def test_provenance_v3_snapshot_validates_read_only_without_migration(tmp_path):
    source = _create_provenance_database(tmp_path / "source.sqlite")
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")

    validated = validate_snapshot_manifest(binding.manifest_path)
    with connect_existing_read_only(validated.database) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        validate_provenance_v3_schema(connection)


@pytest.mark.parametrize(
    "corruption",
    [
        "DROP TRIGGER evidence_snapshots_immutable_update",
        "DROP TABLE schema_migrations",
    ],
)
def test_invalid_provenance_is_rejected_before_snapshot_is_created(tmp_path, corruption):
    source = _create_provenance_database(tmp_path / "source.sqlite")
    with connect_existing_read_write(source) as connection:
        connection.execute(corruption)
    snapshot = tmp_path / "snapshot.sqlite"

    with pytest.raises(SnapshotManifestError, match="provenance schema is invalid"):
        export_database_snapshot(source, snapshot)

    assert not snapshot.exists()
    assert not snapshot.with_suffix(".sqlite.manifest.json").exists()


def test_manifested_snapshot_is_read_only_and_restored_evidence_stays_immutable(tmp_path):
    source = _create_provenance_database(tmp_path / "source.sqlite")
    evidence_id = _insert_evidence(source)
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")

    with connect_existing_read_only(binding.database) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
            connection.execute(
                "UPDATE evidence_snapshots SET evidence_text='changed' WHERE evidence_id=?",
                (evidence_id,),
            )
    for database in (binding.database, binding.database.path):
        with pytest.raises(DatabaseConfigurationError, match="immutable"):
            connect_existing_read_write(database)
    with pytest.raises(DatabaseConfigurationError, match="immutable"):
        create_sqlite_database(binding.database.path, purpose="fixture")

    restored = tmp_path / "restored.sqlite"
    shutil.copyfile(binding.database.path, restored)
    with connect_existing_read_write(restored) as connection:
        validate_provenance_v3_schema(connection)
        with pytest.raises(sqlite3.IntegrityError, match="evidence snapshots are immutable"):
            connection.execute(
                "UPDATE evidence_snapshots SET evidence_text='changed' WHERE evidence_id=?",
                (evidence_id,),
            )


def test_valid_snapshots_have_distinct_runtime_and_cache_identities(tmp_path):
    source = _create_provenance_database(tmp_path / "source.sqlite")
    with connect_existing_read_write(source) as connection:
        connection.execute("CREATE TABLE fixture_data (value TEXT)")
        connection.execute("INSERT INTO fixture_data VALUES ('one')")
    first = export_database_snapshot(
        source, tmp_path / "first.sqlite", created_at="2026-08-13T12:00:00+00:00"
    )
    with connect_existing_read_write(source) as connection:
        connection.execute("INSERT INTO fixture_data VALUES ('two')")
    second = export_database_snapshot(
        source, tmp_path / "second.sqlite", created_at="2026-08-13T12:00:00+00:00"
    )

    assert first.database.database_id != second.database.database_id
    assert first.database.snapshot_id != second.database.snapshot_id
    assert first.database.cache_namespace != second.database.cache_namespace


def test_missing_database_remains_absent_through_operational_factories(tmp_path):
    missing = tmp_path / "missing.sqlite"
    runtime = resolve_runtime_database(missing)

    assert runtime.path == missing.resolve()
    assert not missing.exists()
    for operation in (
        lambda: require_existing_database(missing),
        lambda: connect_existing_read_only(runtime),
        lambda: connect_existing_read_write(runtime),
        lambda: bind_live_database_state(runtime),
    ):
        with pytest.raises(DatabaseConfigurationError, match="does not exist"):
            operation()
        assert not missing.exists()
