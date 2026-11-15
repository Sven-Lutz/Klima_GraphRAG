"""Read-only source hints are provisional; fixtures are not research data."""

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from crawler.core.retrieval import RetrievalError
from crawler.web.source_structure import SourceStructureInspector, structural_texts


def test_exact_structural_labels_do_not_override_identical_prose():
    raw = (
        b'<h2>A heading</h2><li><a href="/next">Navigation label</a></li>'
        b'<a href="/x">Same label</a><p>Same label</p><li>A substantive list measure</li>'
    )
    assert structural_texts(raw) == {"A heading", "Navigation label"}


def test_inspector_checks_actual_unit_original_and_cache_changes(tmp_path):
    raw = b'<h2>A heading</h2><li><a href="/next">Navigation label</a></li>'
    sha = hashlib.sha256(raw).hexdigest()
    original = tmp_path / "original.html"
    original.write_bytes(raw)
    ledger = tmp_path / "synthetic.sqlite"
    with sqlite3.connect(ledger) as conn:
        conn.executescript(
            "CREATE TABLE metadata(key,value);"
            "INSERT INTO metadata VALUES('schema','bavaria-acquisition-v1');"
            "CREATE TABLE municipalities(ags,record);CREATE TABLE associations(sha256,ags);"
            "CREATE TABLE units(unit_id,sha256,text,kind);"
            "CREATE TABLE documents(sha256,raw_path,raw_bytes);"
        )
        conn.execute("INSERT INTO documents VALUES(?,?,?)", (sha, str(original), len(raw)))
        conn.executemany(
            "INSERT INTO units VALUES(?,?,?,?)",
            [
                ("heading", sha, "A heading", "heading"),
                ("link", sha, "Navigation label", "list_item"),
            ],
        )
    inspector = SourceStructureInspector(
        SimpleNamespace(db=ledger, source_databases=[]), [tmp_path]
    )
    metadata = dict(source_id="link", document_id="source-version:" + sha)
    assert inspector(metadata, "Navigation label")["structural_only"] is True
    assert inspector(metadata, "A partial excerpt")["structural_only"] is False
    with pytest.raises(ValueError, match="another original"):
        inspector({**metadata, "document_id": "source-version:foreign"}, "Navigation label")
    original.write_bytes(raw + b" ")
    with pytest.raises(RetrievalError, match="integrity"):
        inspector(metadata, "Navigation label")
