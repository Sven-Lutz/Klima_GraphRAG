"""Retrieval caches must not leak corpora across runtime databases."""

from __future__ import annotations

import struct

from crawler.core.database import connect_existing_read_only, create_sqlite_database
from crawler.scripts import query_agent


def _database(path, text):
    with create_sqlite_database(path, purpose="fixture") as connection:
        connection.execute(
            "CREATE TABLE chunks (chunk_id TEXT, text TEXT, heading_context TEXT, embedding BLOB)"
        )
        connection.execute(
            "INSERT INTO chunks VALUES ('c1', ?, NULL, ?)",
            (text, struct.pack("2f", 1.0, 0.0)),
        )


def test_chunk_corpus_cache_is_isolated_across_same_shape_databases(tmp_path):
    first_path = tmp_path / "first.sqlite"
    second_path = tmp_path / "second.sqlite"
    _database(first_path, "first database")
    _database(second_path, "second database")
    query_agent._CHUNK_CORPUS.clear()
    query_agent._CHUNK_CORPUS["sig"] = None

    first_binding = query_agent._query_database(first_path)
    with connect_existing_read_only(first_path) as connection:
        first = query_agent._load_chunk_corpus(connection, first_binding.cache_namespace)
        first_text = first["texts"][0]
        first_signature = first["sig"]

    second_binding = query_agent._query_database(second_path)
    with connect_existing_read_only(second_path) as connection:
        second = query_agent._load_chunk_corpus(connection, second_binding.cache_namespace)

    assert first_text == "first database"
    assert second["texts"] == ["second database"]
    assert second["sig"] != first_signature
