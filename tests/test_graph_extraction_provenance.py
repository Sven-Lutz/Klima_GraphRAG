"""Extraction persistence tests for the explicit provenance contract."""

from __future__ import annotations

import sqlite3

import pytest

from crawler.core.provenance import ProvenanceError
from crawler.scripts import extract_graph_ollama


def _database(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents_raw (
            document_id TEXT PRIMARY KEY, municipality_id TEXT, url_canonical TEXT
        );
        CREATE TABLE segments (
            segment_id TEXT PRIMARY KEY, document_id TEXT, order_index INTEGER,
            segment_type TEXT, text TEXT, segment_hash TEXT, page_ref TEXT,
            impact_score INTEGER, is_negative INTEGER, heading_context TEXT
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY, document_id TEXT, municipality_id TEXT,
            segment_id TEXT, chunk_index INTEGER, text TEXT, heading_context TEXT,
            impact_score INTEGER
        );
        INSERT INTO documents_raw VALUES ('doc-1', 'muni-1', 'https://example.test/source');
        """
    )
    return conn


def _graph() -> extract_graph_ollama.KnowledgeGraph:
    return extract_graph_ollama.KnowledgeGraph(
        entities=[
            extract_graph_ollama.Entity(
                name="Stadtwerke", type="Akteur", category="Strom", status="Existierend"
            )
        ]
    )


@pytest.mark.parametrize(
    ("chunk_mode", "source_id", "segment_id", "chunk_id"),
    [
        (False, "seg-1", "seg-1", None),
        (True, "chunk-1", "seg-1", "chunk-1"),
    ],
)
def test_extraction_persists_lossless_source_provenance(
    monkeypatch, tmp_path, chunk_mode, source_id, segment_id, chunk_id
):
    db_path = tmp_path / "graph.sqlite"
    conn = _database(str(db_path))
    text = "Klimaschutz " * 30
    conn.execute(
        "INSERT INTO segments VALUES "
        "('seg-1', 'doc-1', 0, 'body', ?, 'hash', NULL, 20, 0, 'Heading')",
        (text,),
    )
    conn.execute(
        "INSERT INTO chunks VALUES ('chunk-1', 'doc-1', 'muni-1', 'seg-1', 0, ?, 'Heading', 20)",
        (text,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **kwargs: _graph())

    extract_graph_ollama.main(
        limit=1, min_score=1, use_chunks_table=chunk_mode, db_path=str(db_path)
    )

    with sqlite3.connect(db_path) as verify:
        row = verify.execute(
            """
            SELECT source_type, source_id, segment_id, chunk_id, document_id, municipality_id,
                   source_url, source_text, extraction_model, prompt_version,
                   extraction_run_id, provenance_status
            FROM graph_triplets
            """
        ).fetchone()
        assert row[:4] == (("chunk" if chunk_mode else "segment"), source_id, segment_id, chunk_id)
        assert row[4:8] == ("doc-1", "muni-1", "https://example.test/source", text)
        assert row[8] == extract_graph_ollama.MODEL_NAME
        assert row[9]
        assert row[10]
        assert row[11] == "verified"
        run_status = verify.execute("SELECT status FROM graph_extraction_runs").fetchone()[0]
        assert run_status == "completed"


def test_chunk_extraction_rejects_missing_parent_segment(monkeypatch, tmp_path):
    db_path = tmp_path / "graph.sqlite"
    conn = _database(str(db_path))
    conn.execute(
        "INSERT INTO chunks VALUES ('chunk-orphan', 'doc-1', 'muni-1', NULL, 0, ?, 'Heading', 20)",
        ("Klimaschutz " * 30,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **kwargs: _graph())

    with pytest.raises(ProvenanceError, match="no resolvable parent segment"):
        extract_graph_ollama.main(limit=1, min_score=1, use_chunks_table=True, db_path=str(db_path))
