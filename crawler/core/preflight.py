"""Explicit, read-only preflight checks for pipeline entry points.

Preflight checks deliberately never create a database, repair schema, or mark
work as complete. They make missing predecessor outputs and schema drift fail
before a worker or query invocation can act on incomplete state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path


class PreflightError(RuntimeError):
    """Raised when an entry point's explicit prerequisites are not satisfied."""


def _quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier used by the fixed preflight schema contract."""
    if not identifier or not identifier.replace("_", "").isalnum():
        raise PreflightError(f"Invalid schema identifier in preflight contract: {identifier!r}")
    return f'"{identifier}"'


def validate_sqlite_database(
    db_path: Path,
    *,
    required_columns: Mapping[str, Sequence[str]],
    required_nonempty_tables: Sequence[str] = (),
) -> Path:
    """Validate a selected SQLite database using a read-only connection.

    The selected path is returned unchanged. This lets callers pass exactly
    the path that was preflighted through the rest of an invocation.
    """
    if not db_path.is_file():
        raise PreflightError(f"Selected database does not exist or is not a file: {db_path}")

    read_only_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(read_only_uri, uri=True) as connection:
            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {row[0] for row in table_rows}

            for table, columns in required_columns.items():
                if table not in tables:
                    raise PreflightError(
                        f"Selected database is missing required table '{table}': {db_path}. "
                        "Run the required predecessor stage first."
                    )
                present_columns = {
                    row[1]
                    for row in connection.execute(
                        f"PRAGMA table_info({_quote_identifier(table)})"
                    ).fetchall()
                }
                missing_columns = set(columns) - present_columns
                if missing_columns:
                    raise PreflightError(
                        f"Required table '{table}' has incompatible schema; missing "
                        f"columns {sorted(missing_columns)} in {db_path}."
                    )

            for table in required_nonempty_tables:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
                if count == 0:
                    raise PreflightError(
                        f"Required predecessor output '{table}' is empty in {db_path}. "
                        "Run its producing pipeline stage before continuing."
                    )
    except PreflightError:
        raise
    except sqlite3.Error as exc:
        raise PreflightError(
            f"Selected database cannot be read safely: {db_path} ({type(exc).__name__})."
        ) from exc

    return db_path


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
    selected_db_path = validate_sqlite_database(
        db_path,
        required_columns={"chunks": ("chunk_id", "text", "embedding")},
    )
    read_only_uri = f"{selected_db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(read_only_uri, uri=True) as connection:
            embedded_chunk = connection.execute(
                "SELECT 1 FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise PreflightError(
            f"Selected database cannot validate embedded chunks: {selected_db_path} "
            f"({type(exc).__name__})."
        ) from exc
    if embedded_chunk is None:
        raise PreflightError(
            f"Required predecessor output 'chunks.embedding' is empty in {selected_db_path}. "
            "Run generate_embeddings.py before querying."
        )
    return selected_db_path
