"""Tests für Storage: Upsert bei Re-Fetch und echte Transaktions-Atomarität."""

from types import SimpleNamespace

import pytest

from crawler.core.storage import Storage

URL = "https://stadt.de/klima"


@pytest.fixture()
def storage(tmp_path):
    s = Storage(db_path=tmp_path / "t.sqlite", raw_dir=tmp_path / "raw", create=True)
    yield s
    s.conn.close()


def _fetch_result(body: bytes = b"<html>Klimaschutz</html>", status: int = 200):
    return SimpleNamespace(
        body=body,
        headers={"Content-Type": "text/html"},
        url_final=URL,
        status_code=status,
        content_type="text/html",
    )


def test_refetch_reuses_document_row(storage):
    """Gleiche URL zweimal gespeichert → EINE Zeile, gleiche doc_id."""
    doc1 = storage.store_raw("09161000", URL, _fetch_result())
    doc2 = storage.store_raw("09161000", URL, _fetch_result())
    assert doc1 == doc2
    n = storage.conn.execute(
        "SELECT COUNT(*) FROM documents_raw WHERE url_canonical=?", (URL,)
    ).fetchone()[0]
    assert n == 1


def test_refetch_after_error_updates_status(storage):
    """Fehler-Retry: Status wird aktualisiert statt Duplikat angelegt."""
    doc1 = storage.store_raw("09161000", URL, _fetch_result(b"", status=500))
    doc2 = storage.store_raw("09161000", URL, _fetch_result(status=200))
    assert doc1 == doc2
    status = storage.conn.execute(
        "SELECT status_code FROM documents_raw WHERE document_id=?", (doc1,)
    ).fetchone()[0]
    assert status == 200


def test_content_change_clears_stale_segments(storage):
    doc_id = storage.store_raw("09161000", URL, _fetch_result(b"version 1"))
    storage.conn.execute(
        "INSERT INTO segments (segment_id, document_id, text, segment_hash) "
        "VALUES ('s1', ?, 'alter Inhalt', 'h1')",
        (doc_id,),
    )
    # Re-Fetch mit geändertem Inhalt → alte Segmente fliegen raus
    storage.store_raw("09161000", URL, _fetch_result(b"version 2"))
    n = storage.conn.execute(
        "SELECT COUNT(*) FROM segments WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert n == 0


def test_unchanged_content_keeps_segments(storage):
    doc_id = storage.store_raw("09161000", URL, _fetch_result(b"gleich"))
    storage.conn.execute(
        "INSERT INTO segments (segment_id, document_id, text, segment_hash) "
        "VALUES ('s1', ?, 'Inhalt', 'h1')",
        (doc_id,),
    )
    storage.store_raw("09161000", URL, _fetch_result(b"gleich"))
    n = storage.conn.execute(
        "SELECT COUNT(*) FROM segments WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert n == 1


def test_transaction_rolls_back_on_error(storage):
    """Ein Fehler im Block darf keine halben Zustände hinterlassen."""
    with pytest.raises(RuntimeError):
        with storage.transaction():
            storage.store_raw("09161000", URL, _fetch_result())
            raise RuntimeError("Absturz mitten im Block")

    n = storage.conn.execute("SELECT COUNT(*) FROM documents_raw").fetchone()[0]
    assert n == 0, "Rollback muss den store_raw rückgängig machen"


def test_transaction_commits_on_success(storage):
    with storage.transaction():
        storage.store_raw("09161000", URL, _fetch_result())
        storage.mark_visited(URL, 200)
    assert storage.is_visited(URL)
    n = storage.conn.execute("SELECT COUNT(*) FROM documents_raw").fetchone()[0]
    assert n == 1
