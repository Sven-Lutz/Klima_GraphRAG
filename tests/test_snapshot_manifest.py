"""WAL-aware live validation and manifested snapshot identity tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from crawler.core import snapshots
from crawler.core.database import create_sqlite_database
from crawler.core.preflight import validate_sqlite_database
from crawler.core.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_V2_SCHEMA_VERSION,
    PROVENANCE_V3_SCHEMA_VERSION,
    ensure_provenance_v3,
)
from crawler.core.snapshots import (
    SnapshotManifestError,
    export_database_snapshot,
    manifest_id_for,
    validate_snapshot_manifest,
)


def _live_wal_database(path: Path):
    connection = create_sqlite_database(path, purpose="fixture", wal=True)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE base (id INTEGER PRIMARY KEY)")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("CREATE TABLE committed_in_wal (value TEXT)")
    connection.execute("INSERT INTO committed_in_wal VALUES ('visible')")
    assert Path(f"{path}-wal").exists()
    return connection


def test_live_preflight_reads_committed_wal_state(tmp_path):
    db_path = tmp_path / "live.sqlite"
    writer = _live_wal_database(db_path)
    try:
        assert (
            validate_sqlite_database(db_path, required_columns={"committed_in_wal": ("value",)})
            == db_path.resolve()
        )
    finally:
        writer.close()


def test_snapshot_export_captures_wal_and_validates_manifest(tmp_path):
    source_path = tmp_path / "live.sqlite"
    writer = _live_wal_database(source_path)
    try:
        binding = export_database_snapshot(
            source_path,
            tmp_path / "snapshot.sqlite",
            created_at="2026-08-13T12:00:00+00:00",
        )
    finally:
        writer.close()

    validated = validate_snapshot_manifest(binding.manifest_path)
    assert validated.database.path == (tmp_path / "snapshot.sqlite").resolve()
    assert validated.database.snapshot_id == binding.database.snapshot_id
    assert validated.manifest["pipeline_stage_completeness"] == {
        "status": "not_recorded",
        "source_table": None,
        "records": [],
    }
    with sqlite3.connect(validated.database.path) as connection:
        assert connection.execute("SELECT value FROM committed_in_wal").fetchone() == ("visible",)


def test_manifest_identity_is_deterministic_for_canonical_content(tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
        connection.execute("INSERT INTO data VALUES ('one')")
    binding = export_database_snapshot(
        source,
        tmp_path / "snapshot.sqlite",
        created_at="2026-08-13T12:00:00+00:00",
    )
    manifest = json.loads(binding.manifest_path.read_text(encoding="utf-8"))

    assert manifest_id_for(manifest) == manifest["manifest_id"]
    assert manifest_id_for(dict(reversed(list(manifest.items())))) == manifest["manifest_id"]


def test_manifest_records_only_explicit_migrations_and_stage_completions(tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        ensure_provenance_v3(connection)
        connection.execute(
            "CREATE TABLE pipeline_stage_completions "
            "(stage TEXT PRIMARY KEY, status TEXT, completed_at TEXT)"
        )
        connection.execute(
            "INSERT INTO pipeline_stage_completions VALUES "
            "('chunking', 'succeeded', '2026-08-13T11:00:00Z')"
        )
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")

    assert binding.manifest["sqlite"]["provenance_schema_version"] == 3
    migrations = binding.manifest["sqlite"]["provenance_migrations"]
    assert [(row["version"], row["name"]) for row in migrations] == [
        (PROVENANCE_SCHEMA_VERSION, "explicit_graph_provenance"),
        (PROVENANCE_V2_SCHEMA_VERSION, "extraction_runs_and_evidence"),
        (PROVENANCE_V3_SCHEMA_VERSION, "provenance_constraints_and_immutability"),
    ]
    assert binding.manifest["pipeline_stage_completeness"] == {
        "status": "recorded",
        "source_table": "pipeline_stage_completions",
        "records": [
            {
                "stage": "chunking",
                "status": "succeeded",
                "completed_at": "2026-08-13T11:00:00Z",
            }
        ],
    }


def test_changed_database_produces_changed_snapshot_identity(tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
        connection.execute("INSERT INTO data VALUES ('one')")
    first = export_database_snapshot(
        source, tmp_path / "first.sqlite", created_at="2026-08-13T12:00:00+00:00"
    )
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO data VALUES ('two')")
    second = export_database_snapshot(
        source, tmp_path / "second.sqlite", created_at="2026-08-13T12:00:00+00:00"
    )

    assert first.database.database_id != second.database.database_id
    assert first.database.snapshot_id != second.database.snapshot_id


@pytest.mark.parametrize("content", ["not-json", "[]", '{"format": "wrong"}'])
def test_malformed_manifest_is_rejected(tmp_path, content):
    manifest = tmp_path / "bad.manifest.json"
    manifest.write_text(content, encoding="utf-8")
    with pytest.raises(SnapshotManifestError):
        validate_snapshot_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("created_at", "not-a-time", "ISO 8601"),
        ("created_at", "2026-08-13T12:00:00", "UTC offset"),
        ("snapshot.sha256", "z" * 64, "lowercase hexadecimal"),
    ],
)
def test_malformed_manifest_field_is_rejected(tmp_path, field, value, message):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    manifest = dict(binding.manifest)
    if field == "snapshot.sha256":
        manifest["snapshot"] = {**manifest["snapshot"], "sha256": value}
    else:
        manifest[field] = value
    manifest["manifest_id"] = manifest_id_for(manifest)
    binding.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SnapshotManifestError, match=message):
        validate_snapshot_manifest(binding.manifest_path)


def test_snapshot_schema_version_mismatch_is_rejected(tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    actual = int(binding.manifest["sqlite"]["schema_version"])

    with pytest.raises(SnapshotManifestError, match="does not match required"):
        validate_snapshot_manifest(binding.manifest_path, expected_schema_version=actual + 1)


def test_manifested_schema_metadata_tampering_is_rejected(tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    manifest = json.loads(binding.manifest_path.read_text(encoding="utf-8"))
    manifest["sqlite"]["schema_version"] += 1
    manifest["manifest_id"] = manifest_id_for(manifest)
    binding.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SnapshotManifestError, match="schema_version"):
        validate_snapshot_manifest(binding.manifest_path)


def test_snapshot_change_during_validation_is_rejected(monkeypatch, tmp_path):
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    original_sha256 = snapshots._sha256_file

    def hash_then_change(path):
        digest = original_sha256(path)
        with path.open("r+b") as snapshot_file:
            snapshot_file.seek(68)
            original = snapshot_file.read(1)
            snapshot_file.seek(68)
            snapshot_file.write(bytes([original[0] ^ 1]))
        return digest

    monkeypatch.setattr(snapshots, "_sha256_file", hash_then_change)
    with pytest.raises(SnapshotManifestError, match="changed while"):
        validate_snapshot_manifest(binding.manifest_path)


def test_snapshot_symlink_cannot_escape_manifest_directory(tmp_path):
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    source = tmp_path / "source.sqlite"
    with create_sqlite_database(source, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = export_database_snapshot(source, manifest_dir / "snapshot.sqlite")
    external_snapshot = tmp_path / "external.sqlite"
    binding.database.path.replace(external_snapshot)
    binding.database.path.symlink_to(external_snapshot)

    with pytest.raises(SnapshotManifestError, match="beside its manifest"):
        validate_snapshot_manifest(binding.manifest_path)
