"""Explicit, SQLite-aware preflight checks for live operational databases.

Live validation opens the selected database in SQLite read-only mode.  This is
WAL-aware and never falls back to creating a missing file.  Immutable snapshot
validation is provided separately by :mod:`crawler.core.snapshots` and is only
used after manifest checksum verification.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from crawler.core.database import DatabaseConfigurationError, connect_existing_read_only


class PreflightError(RuntimeError):
    """Raised when an entry point's explicit prerequisites are not satisfied."""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    quoted_table = table.replace('"', '""')
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{quoted_table}")')}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def validate_sqlite_database(
    db_path: Path,
    *,
    required_columns: Mapping[str, Sequence[str]],
    required_nonempty_tables: Sequence[str] = (),
) -> Path:
    """Validate the committed live SQLite state without writing application data."""
    selected = Path(db_path).expanduser().resolve()
    try:
        with connect_existing_read_only(selected) as connection:
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise PreflightError("Live database preflight is not query-only")
            for table, columns in required_columns.items():
                if not _table_exists(connection, table):
                    raise PreflightError(
                        f"Selected database is missing required table '{table}': {selected}. "
                        "Run the required predecessor stage first."
                    )
                missing = set(columns) - _table_columns(connection, table)
                if missing:
                    raise PreflightError(
                        f"Required table '{table}' has incompatible schema; missing "
                        f"columns {sorted(missing)} in {selected}."
                    )
            for table in required_nonempty_tables:
                if not _table_exists(connection, table):
                    raise PreflightError(
                        f"Selected database is missing required table '{table}': {selected}."
                    )
                quoted_table = table.replace('"', '""')
                if connection.execute(f'SELECT 1 FROM "{quoted_table}" LIMIT 1').fetchone() is None:
                    raise PreflightError(
                        f"Required predecessor output '{table}' is empty in {selected}."
                    )
    except PreflightError:
        raise
    except DatabaseConfigurationError as exc:
        raise PreflightError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise PreflightError(
            f"Selected database cannot be validated safely: {selected} ({type(exc).__name__})."
        ) from exc
    return selected


def validate_worker_preflight(db_path: Path) -> Path:
    """Validate the seed-job state required before a crawl worker can run."""
    return validate_sqlite_database(
        db_path,
        required_columns={
            "seed_jobs": (
                "municipality_id",
                "seed_url",
                "status",
                "claimed_by",
                "claimed_at",
                "heartbeat_at",
                "done_at",
                "last_error",
                "attempt_count",
            )
        },
    )


def validate_query_preflight(db_path: Path) -> Path:
    """Validate embedded chunks, the required predecessor output for retrieval."""
    selected = validate_sqlite_database(
        db_path,
        required_columns={"chunks": ("chunk_id", "text", "embedding")},
    )
    try:
        with connect_existing_read_only(selected) as connection:
            embedded_chunk = connection.execute(
                "SELECT 1 FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
    except (DatabaseConfigurationError, sqlite3.Error) as exc:
        raise PreflightError(
            f"Selected database cannot validate embedded chunks: {selected} ({type(exc).__name__})."
        ) from exc
    if embedded_chunk is None:
        raise PreflightError(
            f"Required predecessor output 'chunks.embedding' is empty in {selected}. "
            "Run generate_embeddings.py before querying."
        )
    return selected
