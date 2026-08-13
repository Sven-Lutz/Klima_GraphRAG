"""Versioned, content-addressed SQLite snapshot manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from crawler.core.database import (
    RuntimeDatabase,
    connect_existing_read_only,
    create_sqlite_database,
    database_id_for_path,
    live_database_state_id,
    require_existing_database,
)
from crawler.core.provenance import ProvenanceError, validated_provenance_schema_version

MANIFEST_FORMAT = "graphrag-al-sqlite-snapshot"
MANIFEST_FORMAT_VERSION = 2
SNAPSHOT_ASSUMPTION = "sqlite-backup-single-file"


class SnapshotManifestError(RuntimeError):
    """Raised when a snapshot or its manifest cannot be trusted."""


@dataclass(frozen=True, slots=True)
class SnapshotBinding:
    """Validated binding suitable for runs, caches, exports, and evaluation."""

    database: RuntimeDatabase
    manifest_path: Path
    manifest: Mapping[str, Any]


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_id_for(manifest: Mapping[str, Any]) -> str:
    """Derive the manifest identity from all canonical content except the ID itself."""
    content = dict(manifest)
    content.pop("manifest_id", None)
    return f"snapshot-manifest-sha256:{hashlib.sha256(_canonical_json(content)).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_state(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _table_exists(connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _sqlite_metadata(connection) -> dict[str, Any]:
    try:
        provenance_schema_version = validated_provenance_schema_version(connection)
    except ProvenanceError as exc:
        raise SnapshotManifestError(f"Snapshot provenance schema is invalid: {exc}") from exc
    migrations: list[dict[str, Any]] = []
    if _table_exists(connection, "schema_migrations"):
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
        }
        if {"version", "name", "applied_at"}.issubset(columns):
            migrations = [
                {"version": int(version), "name": str(name), "applied_at": str(applied_at)}
                for version, name, applied_at in connection.execute(
                    "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                )
            ]

    return {
        "sqlite_version": str(connection.execute("SELECT sqlite_version()").fetchone()[0]),
        "schema_version": int(connection.execute("PRAGMA schema_version").fetchone()[0]),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
        "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
        "provenance_schema_version": provenance_schema_version,
        "provenance_migrations": migrations,
    }


def _stage_completeness_metadata(connection) -> dict[str, Any]:
    """Report only explicit completion records; never infer stage completion from data."""
    table = "pipeline_stage_completions"
    if not _table_exists(connection, table):
        return {"status": "not_recorded", "source_table": None, "records": []}
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    required = {"stage", "status", "completed_at"}
    if not required.issubset(columns):
        return {"status": "incompatible", "source_table": table, "records": []}
    records = [
        {"stage": str(stage), "status": str(status), "completed_at": str(completed_at)}
        for stage, status, completed_at in connection.execute(
            f"SELECT stage, status, completed_at FROM {table} ORDER BY stage"
        )
    ]
    return {"status": "recorded", "source_table": table, "records": records}


def export_database_snapshot(
    source_database: str | Path,
    snapshot_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    created_at: str | None = None,
) -> SnapshotBinding:
    """Export a live, WAL-aware SQLite backup and write its content manifest.

    SQLite's backup API captures a transactionally consistent view including
    committed WAL content.  It does not checkpoint or otherwise mutate the live
    source database.
    """
    source_path = require_existing_database(source_database)
    target_path = Path(snapshot_path).expanduser().resolve()
    if target_path.exists():
        raise SnapshotManifestError(f"Snapshot target already exists: {target_path}")
    target_manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path is not None
        else target_path.with_suffix(target_path.suffix + ".manifest.json")
    )
    if target_manifest.exists():
        raise SnapshotManifestError(f"Snapshot manifest already exists: {target_manifest}")
    if target_manifest.parent != target_path.parent or not target_manifest.name.endswith(
        ".manifest.json"
    ):
        raise SnapshotManifestError(
            "Snapshot manifest must use a .manifest.json filename beside its snapshot"
        )

    with connect_existing_read_only(source_path) as source:
        source.execute("BEGIN")
        try:
            source_metadata = _sqlite_metadata(source)
            source_stages = _stage_completeness_metadata(source)
            with create_sqlite_database(target_path, purpose="snapshot", wal=False) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                target.execute("PRAGMA synchronous=FULL")
        finally:
            source.execute("ROLLBACK")

    checksum = _sha256_file(target_path)
    size = target_path.stat().st_size
    with connect_existing_read_only(target_path, immutable=True) as snapshot:
        snapshot_metadata = _sqlite_metadata(snapshot)
        snapshot_stages = _stage_completeness_metadata(snapshot)

    if snapshot_stages != source_stages:
        raise SnapshotManifestError("SQLite backup stage metadata does not match its source view")

    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    database_identity = f"sqlite-sha256:{checksum}"
    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "format_version": MANIFEST_FORMAT_VERSION,
        "created_at": timestamp,
        "database_identity": database_identity,
        "source": {
            "database_id": database_id_for_path(source_path),
            "live_state_id": live_database_state_id(source_path),
            "origin_path": os.fspath(source_path),
        },
        "snapshot": {
            "file_name": target_path.name,
            "size_bytes": size,
            "sha256": checksum,
        },
        "sqlite": {
            **snapshot_metadata,
            "snapshot_assumption": SNAPSHOT_ASSUMPTION,
            "source_journal_mode": source_metadata["journal_mode"],
            "source_schema_version": source_metadata["schema_version"],
        },
        "pipeline_stage_completeness": snapshot_stages,
    }
    manifest["manifest_id"] = manifest_id_for(manifest)
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    target_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return validate_snapshot_manifest(target_manifest)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotManifestError(f"Snapshot manifest field {field!r} must be an object")
    return value


def _require_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotManifestError(f"Snapshot manifest field {field!r} must be non-negative")
    return value


def validate_snapshot_manifest(
    manifest_path: str | Path,
    *,
    expected_schema_version: int | None = None,
) -> SnapshotBinding:
    """Validate manifest structure, content identity, checksum, and SQLite metadata."""
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    try:
        raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotManifestError(
            f"Snapshot manifest cannot be read as JSON: {resolved_manifest}"
        ) from exc
    manifest = _require_mapping(raw, "root")
    if manifest.get("format") != MANIFEST_FORMAT:
        raise SnapshotManifestError("Unsupported snapshot manifest format")
    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        raise SnapshotManifestError("Unsupported snapshot manifest format version")
    for field in ("created_at", "database_identity", "manifest_id"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise SnapshotManifestError(f"Snapshot manifest field {field!r} is required")
    try:
        created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotManifestError("Snapshot created_at must be an ISO 8601 timestamp") from exc
    if created_at.tzinfo is None:
        raise SnapshotManifestError("Snapshot created_at must include a UTC offset")
    expected_manifest_id = manifest_id_for(manifest)
    if manifest["manifest_id"] != expected_manifest_id:
        raise SnapshotManifestError("Snapshot manifest identity does not match canonical content")

    snapshot = _require_mapping(manifest.get("snapshot"), "snapshot")
    file_name = snapshot.get("file_name")
    if (
        not isinstance(file_name, str)
        or not file_name
        or Path(file_name).name != file_name
        or Path(file_name).is_absolute()
    ):
        raise SnapshotManifestError("Snapshot file_name must be one relative file name")
    expected_size = _require_integer(snapshot.get("size_bytes"), "snapshot.size_bytes")
    expected_checksum = snapshot.get("sha256")
    if not isinstance(expected_checksum, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_checksum
    ):
        raise SnapshotManifestError("Snapshot sha256 must be a lowercase hexadecimal digest")

    requested_snapshot_path = resolved_manifest.parent / file_name
    snapshot_path = require_existing_database(requested_snapshot_path)
    if snapshot_path.parent != resolved_manifest.parent:
        raise SnapshotManifestError("Snapshot file must resolve beside its manifest")
    if snapshot_path.stat().st_size != expected_size:
        raise SnapshotManifestError("Snapshot size does not match its manifest")
    initial_state = _file_state(snapshot_path)
    actual_checksum = _sha256_file(snapshot_path)
    if _file_state(snapshot_path) != initial_state:
        raise SnapshotManifestError("Snapshot changed while its checksum was being validated")
    if actual_checksum != expected_checksum:
        raise SnapshotManifestError("Snapshot checksum does not match its manifest")
    expected_database_identity = f"sqlite-sha256:{actual_checksum}"
    if manifest["database_identity"] != expected_database_identity:
        raise SnapshotManifestError("Snapshot database identity does not match its checksum")

    sqlite_manifest = _require_mapping(manifest.get("sqlite"), "sqlite")
    if sqlite_manifest.get("snapshot_assumption") != SNAPSHOT_ASSUMPTION:
        raise SnapshotManifestError("Unsupported SQLite snapshot assumption")
    for suffix in ("-wal", "-shm"):
        if Path(f"{snapshot_path}{suffix}").exists():
            raise SnapshotManifestError("Stable snapshot must not have WAL or SHM sidecars")

    with connect_existing_read_only(snapshot_path, immutable=True) as connection:
        actual_metadata = _sqlite_metadata(connection)
        actual_stages = _stage_completeness_metadata(connection)
    if _file_state(snapshot_path) != initial_state:
        raise SnapshotManifestError("Snapshot changed while its SQLite metadata was inspected")
    for field in (
        "schema_version",
        "user_version",
        "page_size",
        "page_count",
        "journal_mode",
        "provenance_schema_version",
        "provenance_migrations",
    ):
        if sqlite_manifest.get(field) != actual_metadata[field]:
            raise SnapshotManifestError(f"Snapshot SQLite {field} does not match its manifest")
    manifest_schema_version = _require_integer(
        sqlite_manifest.get("schema_version"), "sqlite.schema_version"
    )
    if expected_schema_version is not None and manifest_schema_version != expected_schema_version:
        raise SnapshotManifestError(
            f"Snapshot schema version {manifest_schema_version} does not match required "
            f"version {expected_schema_version}"
        )
    if manifest.get("pipeline_stage_completeness") != actual_stages:
        raise SnapshotManifestError("Snapshot stage completeness metadata does not match")

    database = RuntimeDatabase(
        path=snapshot_path,
        source="explicit",
        database_id=str(manifest["database_identity"]),
        snapshot_id=str(manifest["manifest_id"]),
    )
    return SnapshotBinding(database=database, manifest_path=resolved_manifest, manifest=manifest)
