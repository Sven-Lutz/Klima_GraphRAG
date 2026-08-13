"""Focused tests for explicit, non-mutating pipeline preflight checks."""

from __future__ import annotations

import sqlite3

import pytest

from crawler.core.preflight import (
    PreflightError,
    validate_query_preflight,
    validate_sqlite_database,
    validate_worker_preflight,
)


def _create_worker_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE seed_jobs (municipality_id TEXT PRIMARY KEY, seed_url TEXT, "
            "status TEXT, claimed_by TEXT, claimed_at TEXT, heartbeat_at TEXT, "
            "done_at TEXT, last_error TEXT, attempt_count INTEGER)"
        )


def _create_query_database(path, *, with_embedding=True):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE chunks (chunk_id TEXT, text TEXT, embedding BLOB)")
        if with_embedding:
            connection.execute(
                "INSERT INTO chunks (chunk_id, text, embedding) VALUES ('c1', 'text', ?)",
                (b"embedding",),
            )


def test_worker_preflight_validates_required_schema(tmp_path):
    db_path = tmp_path / "worker.sqlite"
    _create_worker_database(db_path)

    assert validate_worker_preflight(db_path) == db_path


def test_worker_preflight_does_not_change_database_contents(tmp_path):
    db_path = tmp_path / "worker.sqlite"
    _create_worker_database(db_path)
    before = db_path.read_bytes()

    assert validate_worker_preflight(db_path) == db_path
    assert db_path.read_bytes() == before


def test_worker_preflight_rejects_incomplete_schema(tmp_path):
    db_path = tmp_path / "worker.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE seed_jobs (municipality_id TEXT)")

    with pytest.raises(PreflightError, match="incompatible schema"):
        validate_worker_preflight(db_path)


def test_preflight_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing.sqlite"

    with pytest.raises(PreflightError, match="does not exist"):
        validate_sqlite_database(missing, required_columns={})

    assert not missing.exists()


def test_query_preflight_requires_embedded_predecessor_output(tmp_path):
    db_path = tmp_path / "query.sqlite"
    _create_query_database(db_path, with_embedding=False)

    with pytest.raises(PreflightError, match="chunks.embedding"):
        validate_query_preflight(db_path)


def test_query_preflight_rejects_missing_table(tmp_path):
    db_path = tmp_path / "stage_zero.sqlite"
    _create_worker_database(db_path)

    with pytest.raises(PreflightError, match="missing required table 'chunks'"):
        validate_query_preflight(db_path)
