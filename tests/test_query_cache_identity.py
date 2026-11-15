"""Canonical corpus identity is content-bound and has no process-global cache."""

from __future__ import annotations

from crawler.core.database import connect_existing_read_only, create_sqlite_database
from crawler.core.retrieval import chunk_corpus_rows, corpus_fingerprint
from crawler.scripts import query_agent


def _database(path, text):
    with create_sqlite_database(path, purpose="fixture") as connection:
        connection.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, text TEXT)")
        connection.execute("INSERT INTO chunks VALUES ('c1', ?)", (text,))


def test_corpus_identity_isolated_across_same_shape_databases(tmp_path):
    first_path = tmp_path / "first.sqlite"
    second_path = tmp_path / "second.sqlite"
    _database(first_path, "first database")
    _database(second_path, "second database")

    with connect_existing_read_only(first_path) as connection:
        first = chunk_corpus_rows(connection)
    with connect_existing_read_only(second_path) as connection:
        second = chunk_corpus_rows(connection)

    assert first[0][2] == "first database"
    assert second[0][2] == "second database"
    assert corpus_fingerprint(first) != corpus_fingerprint(second)
    assert not hasattr(query_agent, "_CHUNK_CORPUS")
