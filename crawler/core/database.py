"""Runtime database identity and purpose-specific SQLite connections.

Operational database paths are resolved exactly once per entry point.  An
explicit argument wins over ``KLIMA_DB_PATH``, which wins over the repository
default.  Connection factories never create an operational database unless the
caller explicitly selects a creation purpose.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from crawler.core.config import DEFAULT_DB_PATH, resolve_db_path

DatabasePathSource = Literal["explicit", "environment", "default"]
DatabaseCreationPurpose = Literal[
    "fixture",
    "snapshot",
    "initialization",
    "projection_ledger",
]
_SNAPSHOT_MANIFEST_FORMAT = "graphrag-al-sqlite-snapshot"


class DatabaseConfigurationError(RuntimeError):
    """Raised when an operational database binding is invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeDatabase:
    """Immutable identity for one database selected by one invocation."""

    path: Path
    source: DatabasePathSource
    database_id: str
    snapshot_id: str | None = None
    live_state_id: str | None = None

    @property
    def cache_namespace(self) -> str:
        """Immutable cache namespace for this resolved runtime binding."""
        return self.snapshot_id or self.live_state_id or self.database_id


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Minimal immutable runtime contract shared by pipeline entry points."""

    database: RuntimeDatabase


def database_id_for_path(path: str | Path) -> str:
    """Return a deterministic local identity for a resolved database path."""
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256(os.fsencode(resolved)).hexdigest()
    return f"sqlite-path-sha256:{digest}"


def resolve_runtime_database(value: str | Path | None = None) -> RuntimeDatabase:
    """Resolve one runtime database without opening or creating it."""
    if value is not None:
        source: DatabasePathSource = "explicit"
    elif "KLIMA_DB_PATH" in os.environ:
        source = "environment"
    else:
        source = "default"
    path = resolve_db_path(value)
    return RuntimeDatabase(path=path, source=source, database_id=database_id_for_path(path))


def resolve_runtime_configuration(value: str | Path | None = None) -> RuntimeConfiguration:
    return RuntimeConfiguration(database=resolve_runtime_database(value))


def bind_live_database_state(database: RuntimeDatabase) -> RuntimeDatabase:
    """Freeze the current live state identity for one operational invocation."""
    if database.snapshot_id is not None or database.live_state_id is not None:
        return database
    return replace(database, live_state_id=live_database_state_id(database.path))


def require_existing_database(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise DatabaseConfigurationError(
            f"Selected operational database does not exist or is not a file: {resolved}"
        )
    return resolved


def _resolved_database_path(database: str | Path | RuntimeDatabase) -> Path:
    if isinstance(database, RuntimeDatabase):
        return database.path.expanduser().resolve()
    return Path(database).expanduser().resolve()


def _manifest_claims_snapshot(manifest_path: Path, database_path: Path) -> bool:
    """Return whether a sidecar manifest identifies ``database_path`` as a snapshot."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return manifest_path == database_path.with_suffix(database_path.suffix + ".manifest.json")
    if not isinstance(manifest, dict):
        return False
    snapshot = manifest.get("snapshot")
    return bool(
        manifest.get("format") == _SNAPSHOT_MANIFEST_FORMAT
        and isinstance(snapshot, dict)
        and snapshot.get("file_name") == database_path.name
    )


def _require_not_manifested_snapshot(
    database: str | Path | RuntimeDatabase,
    database_path: Path,
) -> None:
    """Fail closed before any canonical factory opens a manifested snapshot writable."""
    if isinstance(database, RuntimeDatabase) and database.snapshot_id is not None:
        raise DatabaseConfigurationError(
            f"Validated snapshot databases are immutable and cannot be opened writable: "
            f"{database_path}"
        )
    default_manifest = database_path.with_suffix(database_path.suffix + ".manifest.json")
    candidates = {default_manifest}
    if database_path.parent.is_dir():
        candidates.update(database_path.parent.glob("*.manifest.json"))
    if any(
        candidate.is_file() and _manifest_claims_snapshot(candidate, database_path)
        for candidate in candidates
    ):
        raise DatabaseConfigurationError(
            f"Manifested snapshot databases are immutable and cannot be opened writable: "
            f"{database_path}"
        )


def _configure_connection(
    connection: sqlite3.Connection,
    *,
    read_only: bool,
    wal: bool,
) -> sqlite3.Connection:
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA foreign_keys=ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        connection.close()
        raise DatabaseConfigurationError("SQLite foreign-key enforcement could not be enabled")
    if read_only:
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            connection.close()
            raise DatabaseConfigurationError("SQLite query-only enforcement could not be enabled")
    elif wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def connect_existing_read_only(
    path: str | Path | RuntimeDatabase,
    *,
    immutable: bool = False,
    row_factory: type | None = None,
) -> sqlite3.Connection:
    """Open an existing database read-only; never create a missing file.

    ``immutable`` is reserved for a checksum-validated stable snapshot.  Live
    operational validation must leave it false so SQLite reads committed WAL
    state rather than treating the main file as the complete database.
    """
    resolved = require_existing_database(_resolved_database_path(path))
    if isinstance(path, RuntimeDatabase) and path.snapshot_id is not None:
        immutable = True
    parameters = "mode=ro"
    if immutable:
        parameters += "&immutable=1"
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?{parameters}",
        uri=True,
        timeout=60.0,
        isolation_level=None,
    )
    if row_factory is not None:
        connection.row_factory = row_factory
    return _configure_connection(connection, read_only=True, wal=False)


def connect_existing_read_write(
    path: str | Path | RuntimeDatabase,
    *,
    wal: bool = True,
    row_factory: type | None = None,
) -> sqlite3.Connection:
    """Open an existing operational database read-write without fallback creation."""
    resolved = require_existing_database(_resolved_database_path(path))
    _require_not_manifested_snapshot(path, resolved)
    connection = sqlite3.connect(str(resolved), timeout=60.0, isolation_level=None)
    if row_factory is not None:
        connection.row_factory = row_factory
    return _configure_connection(connection, read_only=False, wal=wal)


def create_sqlite_database(
    path: str | Path,
    *,
    purpose: DatabaseCreationPurpose,
    wal: bool = False,
    row_factory: type | None = None,
) -> sqlite3.Connection:
    """Explicitly create a fixture, snapshot, initialization, or projection ledger."""
    if purpose not in {"fixture", "snapshot", "initialization", "projection_ledger"}:
        raise DatabaseConfigurationError(f"Unsupported database creation purpose: {purpose!r}")
    resolved = Path(path).expanduser().resolve()
    _require_not_manifested_snapshot(path, resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=60.0, isolation_level=None)
    if row_factory is not None:
        connection.row_factory = row_factory
    return _configure_connection(connection, read_only=False, wal=wal)


def live_database_state_id(path: str | Path) -> str:
    """Identify one live database state, including WAL sidecar state.

    This is a cache invalidation identity, not a scientific snapshot checksum.
    A manifested SQLite backup supplies the reproducible snapshot identity.
    """
    resolved = require_existing_database(path)
    files = []
    for candidate in (resolved, Path(f"{resolved}-wal"), Path(f"{resolved}-shm")):
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            continue
        files.append(
            {
                "name": candidate.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "inode": stat.st_ino,
            }
        )
    payload = {
        "database_id": database_id_for_path(resolved),
        "files": files,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sqlite-live-sha256:{hashlib.sha256(canonical).hexdigest()}"


def default_database_path() -> Path:
    """Expose the documented default without reading the environment."""
    return DEFAULT_DB_PATH.resolve()
