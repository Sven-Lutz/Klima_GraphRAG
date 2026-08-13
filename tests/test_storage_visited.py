"""Tests für Storage.get_visited_status — der kombinierte Visited-Check
ersetzt bis zu 4 Einzel-Queries pro bereits besuchter URL im Crawl-Loop."""

import pytest

from crawler.core.storage import Storage


@pytest.fixture()
def storage(tmp_path):
    s = Storage(db_path=tmp_path / "t.sqlite", raw_dir=tmp_path / "raw", create=True)
    yield s
    s.conn.close()


URL = "https://stadt.de/klima"


def test_unvisited_url(storage):
    assert storage.get_visited_status(URL) == (False, False, None)


def test_visited_ok_without_document(storage):
    storage.mark_visited(URL, 200)
    visited, errored, doc_id = storage.get_visited_status(URL)
    assert visited is True
    assert errored is False
    assert doc_id is None


def test_visited_with_error(storage):
    storage.mark_visited(URL, 404, "not found")
    visited, errored, doc_id = storage.get_visited_status(URL)
    assert visited is True
    assert errored is True


def test_visited_with_document(storage):
    storage.conn.execute(
        "INSERT INTO documents_raw (document_id, municipality_id, url_canonical) "
        "VALUES ('doc-1', '09161000', ?)",
        (URL,),
    )
    storage.mark_visited(URL, 200)
    visited, errored, doc_id = storage.get_visited_status(URL)
    assert visited is True
    assert errored is False
    assert doc_id == "doc-1"


def test_matches_legacy_methods(storage):
    """Der kombinierte Check muss dieselben Antworten geben wie die Einzel-Methoden."""
    storage.mark_visited("https://stadt.de/ok", 200)
    storage.mark_visited("https://stadt.de/err", 500, "server error")

    for url in ("https://stadt.de/ok", "https://stadt.de/err", "https://stadt.de/neu"):
        visited, errored, doc_id = storage.get_visited_status(url)
        assert visited == storage.is_visited(url)
        if visited:
            assert errored == storage.is_visited_with_error(url)
        assert doc_id == storage.get_document_id_by_canonical_url(url)
