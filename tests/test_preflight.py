"""Focused tests for explicit, non-mutating pipeline preflight checks."""

from __future__ import annotations

import sqlite3
import sys

import pytest

from crawler.core.preflight import (
    PreflightError,
    validate_query_preflight,
    validate_sqlite_database,
    validate_worker_preflight,
)
from crawler.scripts import query_agent


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


def test_answer_preflights_and_propagates_selected_database(monkeypatch, tmp_path):
    db_path = tmp_path / "query.sqlite"
    _create_query_database(db_path)
    captured = {}

    monkeypatch.setattr(query_agent, "route", lambda *args: "local_search")

    def local_search(*args, **kwargs):
        captured["db_path"] = kwargs["db_path"]
        return "ok", []

    monkeypatch.setattr(query_agent, "local_search", local_search)
    monkeypatch.setattr(
        query_agent,
        "critic_check",
        lambda *args: {"is_complete": True, "confidence": 1.0},
    )

    assert query_agent.answer("Frage", db_path=db_path) == "ok"
    assert captured["db_path"].path == db_path.resolve()


def test_text2cypher_preflight_checks_graph_service_and_export(monkeypatch, tmp_path):
    db_path = tmp_path / "query.sqlite"
    _create_query_database(db_path)
    captured = {}

    class Result:
        def single(self):
            return {"entity_count": 1}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run(self, query, timeout):
            captured.update(query=query, timeout=timeout)
            return Result()

    class Driver:
        def session(self, **kwargs):
            captured["session_kwargs"] = kwargs
            return Session()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(query_agent, "_open_text2cypher_driver", lambda: Driver())
    fake_neo4j = type("Neo4j", (), {"READ_ACCESS": "READ"})
    monkeypatch.setitem(sys.modules, "neo4j", fake_neo4j)

    assert query_agent.validate_text2cypher_preflight(db_path) == db_path
    assert "MATCH (entity:Entity)" in captured["query"]
    assert captured["session_kwargs"] == {"default_access_mode": "READ"}
    assert captured["closed"] is True
