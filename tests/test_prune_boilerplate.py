"""Tests für prune_db.mark_boilerplate — seitenübergreifender Segment-Dedup.

Ein Menü-/Footer-Text, der auf vielen Seiten derselben Kommune identisch
vorkommt, wird als is_negative markiert (Chunking + Extraktion überspringen ihn).
Echte Inhalte und gleiche Texte in VERSCHIEDENEN Kommunen bleiben unberührt.
"""
import sqlite3

import pytest

from crawler.scripts.prune_db import mark_boilerplate


@pytest.fixture()
def db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "t.sqlite"))
    con.executescript(
        """
        CREATE TABLE documents_raw (document_id TEXT PRIMARY KEY, municipality_id TEXT);
        CREATE TABLE segments (
            segment_id TEXT PRIMARY KEY, document_id TEXT,
            text TEXT, segment_hash TEXT, is_negative INTEGER
        );
        """
    )
    yield con
    con.close()


def _doc(con, doc_id, muni):
    con.execute("INSERT INTO documents_raw VALUES (?, ?)", (doc_id, muni))


def _seg(con, seg_id, doc_id, text, hash_):
    con.execute(
        "INSERT INTO segments VALUES (?, ?, ?, ?, 0)", (seg_id, doc_id, text, hash_)
    )


def test_repeated_segment_marked(db):
    # Footer-Text identisch in 5 Dokumenten derselben Kommune
    for i in range(5):
        _doc(db, f"d{i}", "09161000")
        _seg(db, f"s{i}", f"d{i}", "© Stadt Ingolstadt | Impressum", "hash-footer")
    # Echter Inhalt nur in einem Dokument
    _seg(db, "s-real", "d0", "Das Klimaschutzkonzept sieht 50% CO2-Reduktion vor.", "hash-real")

    mark_boilerplate(db, min_docs=5)

    marked = dict(db.execute("SELECT segment_id, is_negative FROM segments").fetchall())
    for i in range(5):
        assert marked[f"s{i}"] == 1, "Footer muss markiert sein"
    assert marked["s-real"] == 0, "Echter Inhalt darf nicht markiert werden"


def test_below_threshold_not_marked(db):
    for i in range(3):
        _doc(db, f"d{i}", "09161000")
        _seg(db, f"s{i}", f"d{i}", "Navigation", "hash-nav")

    mark_boilerplate(db, min_docs=5)

    n = db.execute("SELECT COUNT(*) FROM segments WHERE is_negative=1").fetchone()[0]
    assert n == 0


def test_same_text_in_different_municipalities_not_marked(db):
    """Gleicher Text in verschiedenen Kommunen ist KEIN Boilerplate —
    z.B. zitieren viele Kommunen denselben Fördertext."""
    for i in range(6):
        _doc(db, f"d{i}", f"muni-{i}")  # jede in eigener Kommune
        _seg(db, f"s{i}", f"d{i}", "KfW-Förderung 442 für Solaranlagen", "hash-kfw")

    mark_boilerplate(db, min_docs=5)

    n = db.execute("SELECT COUNT(*) FROM segments WHERE is_negative=1").fetchone()[0]
    assert n == 0


def test_already_negative_untouched(db):
    _doc(db, "d0", "09161000")
    db.execute(
        "INSERT INTO segments VALUES ('s0', 'd0', 'Impressum', 'h', 1)"
    )
    mark_boilerplate(db, min_docs=1)
    assert db.execute("SELECT is_negative FROM segments WHERE segment_id='s0'").fetchone()[0] == 1
