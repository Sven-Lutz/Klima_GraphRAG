"""Offline representation regression tests; no scientific quality/gold judgments."""

import hashlib
from pathlib import Path

import pytest

from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html


def parse(html):
    body = html.encode("utf-8")
    return parse_html(
        FetchResult("https://fixture.invalid/", 200, "text/html", body, {}),
        "https://fixture.invalid/",
    )


def texts(result):
    return "\n".join(s.text for s in result.segments)


def test_direct_content_retains_subject_operator_and_date_once():
    result = parse(
        "<main><h2>Anlage A</h2><div><div><strong>Betreiber:</strong> "
        "Kommunale Betriebsgesellschaft Beispiel GmbH.<br>"
        "Inbetriebnahme November 2005</div></div></main>"
    )
    body = texts(result)
    assert body.count("Kommunale Betriebsgesellschaft Beispiel GmbH.") == 1
    assert body.count("Inbetriebnahme November 2005") == 1
    segment = next(s for s in result.segments if "Kommunale Betriebsgesellschaft" in s.text)
    assert "Betreiber:" in segment.text
    assert segment.heading_context == "Anlage A"


@pytest.mark.parametrize(
    "tag,text",
    [
        ("p", "Start: 2028."),
        ("li", "Wärmeerzeugung: 42 MWh"),
        ("div", "Betreiber: AB GmbH"),
        ("p", "11/2005"),
        ("p", "42 MWh"),
    ],
)
def test_short_factual_fields_keep_heading(tag, text):
    result = parse(f"<h2>Anlage B</h2><{tag}>{text}</{tag}>")
    found = [s for s in result.segments if text in s.text]
    assert len(found) == 1
    assert found[0].heading_context == "Anlage B"


def test_local_data_label_stays_with_list_without_leaking_to_next_section():
    result = parse(
        "<h2>Anlage A</h2><div><strong>Daten 2020</strong><br><ul>"
        "<li>Wärmeerzeugung: 42 MWh</li><li>Betreiber: AB GmbH</li>"
        "</ul></div><h2>Anlage B</h2><p>Start: 2028.</p>"
    )
    for term in ["Wärmeerzeugung: 42 MWh", "Betreiber: AB GmbH"]:
        segment = next(s for s in result.segments if term in s.text)
        assert segment.heading_context == "Anlage A > Daten 2020"
    assert texts(result).count("Daten 2020") == 1
    assert result.segments[-1].heading_context == "Anlage B"


def test_nested_blocks_and_table_contents_are_not_duplicated():
    result = parse(
        "<h2>Bilanz</h2><div>Direkter Einleitungstext zum Bericht über die Anlage."
        "<div><p>Dieser Absatz enthält die einmalige Sachangabe zum Betrieb.</p></div>"
        "<ul><li>Äußerer Listenpunkt mit ausführlichem Einleitungstext."
        "<p>Innerer Absatz mit einmaligem Bezug zur Betriebsaufnahme.</p>"
        "<ul><li>Start: 2028.</li></ul></li></ul>"
        "<table><tr><td><p>Tabellenwert mit eigener einmaliger Sachinformation.</p>"
        "</td><td>42 MWh</td></tr></table></div>"
    )
    body = texts(result)
    for term in [
        "Direkter Einleitungstext",
        "Dieser Absatz",
        "Äußerer Listenpunkt",
        "Innerer Absatz",
        "Start: 2028.",
        "Tabellenwert",
    ]:
        assert body.count(term) == 1
    tables = [s for s in result.segments if s.segment_type == "table"]
    assert len(tables) == 1
    assert tables[0].heading_context == "Bilanz"
    assert [s.order_index for s in result.segments] == list(range(len(result.segments)))


def test_controls_and_navigation_are_not_rescued_by_digits_or_length():
    result = parse(
        "<head><title>Kein Segmenttitel</title></head><body>"
        "<nav><p>Stand: 2025</p></nav>"
        '<div role="navigation"><div>Diese lange Navigationszeile bleibt draußen.</div></div>'
        '<button>Seite 2025</button><div role="button">Start: 2028.</div>'
        "<select><option>42 MWh</option></select>"
        '<p><a href="/year">2025</a></p><p>Weiter</p>'
        '<div><a href="/a">Start</a> <a href="/b">Kontakt</a></div>'
        "<h2>Anlage C</h2><p>Leistung: 42 MW</p></body>"
    )
    assert [s.text for s in result.segments] == ["Anlage C", "Leistung: 42 MW"]
    assert ("https://fixture.invalid/year", "2025") in result.out_links


def test_same_literal_at_distinct_source_positions_is_not_globally_deduplicated():
    result = parse("<h2>A</h2><p>Leistung: 42 MW</p><h2>B</h2><p>Leistung: 42 MW</p>")
    values = [s for s in result.segments if s.text == "Leistung: 42 MW"]
    assert [s.heading_context for s in values] == ["A", "B"]


def test_archived_d053_retains_four_markers_and_their_local_context():
    root = Path(__file__).resolve().parents[1]
    digest = "e4d4bc3858a691a4b0756ac1853cf5d1656fdae6bab34c868c164b2382beadbd"
    raw = root / "research/experiments/e2_v11_acquisition_20261024/export/raw" / digest
    if not raw.is_file():
        pytest.skip("Optional archived D053 body is not distributed with unit fixtures")
    body = raw.read_bytes()
    assert hashlib.sha256(body).hexdigest() == digest
    url = "https://www.wildpoldsried.de/erneuerbare-energien.html"
    result = parse_html(FetchResult(url, 200, "text/html", body, {}), url)
    for marker in [
        "Inbetriebnahme November 2005",
        "Dorfentwicklungs-GmbH",
        "Daten 2020",
        "3.572.280",
    ]:
        assert marker in texts(result)
    opening = next(s for s in result.segments if "Inbetriebnahme November 2005" in s.text)
    assert "DORFHEIZUNG" in opening.text
    operator = next(s for s in result.segments if "Dorfentwicklungs-GmbH" in s.text)
    assert "Betreiber:" in operator.text
    value = next(s for s in result.segments if "3.572.280" in s.text)
    assert "Wärmeerzeugung:" in value.text and "kWh" in value.text
    assert "Daten 2020" in value.heading_context


def test_paragraph_data_label_survives_formatting_whitespace_before_list():
    result = parse(
        "<h2>Anlage A</h2><div><p>Daten 2020</p>\n  <ul>\n"
        "<li>Wärmeerzeugung: 42 MWh</li></ul></div>"
    )
    value = next(s for s in result.segments if "42 MWh" in s.text)
    assert value.heading_context == "Anlage A > Daten 2020"


def test_recovered_fields_preserve_raw_source_and_heading_through_chunking(tmp_path):
    from crawler.core.engine import _segment_features
    from crawler.core.storage import Storage
    from crawler.scripts.chunk_documents import process_segments, setup_db

    url = "https://fixture.invalid/source"
    body = (
        "<h2>Anlage A</h2><div><strong>Daten 2020</strong><br>"
        "<ul><li>Wärmeerzeugung: 42 MWh</li></ul></div>"
    ).encode("utf-8")
    fetched = FetchResult(url, 200, "text/html", body, {})
    result = parse_html(fetched, url)
    with Storage(tmp_path / "fixture.sqlite", raw_dir=tmp_path / "raw", create=True) as store:
        doc = store.store_raw("SYNTHETIC", url, fetched)
        store.store_segments_scored(doc, result.segments, _segment_features)
        setup_db(store.conn)
        process_segments(store.conn, retain_all=True)
        rows = store.conn.execute(
            "SELECT c.text, c.heading_context, s.heading_context, d.url_canonical, d.raw_hash, "
            "c.municipality_id, c.document_id, s.document_id FROM chunks c "
            "JOIN segments s ON c.segment_id=s.segment_id "
            "JOIN documents_raw d ON c.document_id=d.document_id WHERE c.document_id=?",
            (doc,),
        ).fetchall()
        row = next(r for r in rows if "Wärmeerzeugung: 42 MWh" in r[0])
        assert row[1] == row[2] == "Anlage A > Daten 2020"
        assert row[3] == url and row[4] == hashlib.sha256(body).hexdigest()
        assert row[5] == "SYNTHETIC" and row[6] == row[7] == doc
