"""Offline synthetic controls; these are not real-source Gold labels."""

import pytest
from bs4 import BeautifulSoup

from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.storage import Storage

BASE = "https://example.test/"


def parse(body):
    fetch = FetchResult(BASE, 200, "text/html", body.encode("utf-8"), {})
    return parse_html(fetch, BASE)


@pytest.mark.parametrize("section", ["nav", "footer", "aside"])
def test_discovery_keeps_nonclimate_links_but_excludes_boilerplate(section):
    result = parse(
        f'<{section}><a href="/sport#details">Sportverein</a>'
        f'<p>Dieses lange Menue darf nicht als Dokumentinhalt erscheinen.</p></{section}>'
        '<p>Der Hauptinhalt bleibt als normaler Absatz weiterhin erhalten.</p>'
    )
    assert result.out_links == [(BASE + "sport", "Sportverein")]
    assert "Sportverein" not in result.text
    assert len(result.segments) == 1
    assert result.segments[0].segment_type == "paragraph"


def test_link_filters_dedup_and_metadata_remain():
    result = parse(
        '<html><head><title>Bericht</title><meta name="author" content="Amt"></head>'
        '<body><nav><a href="/ris">Rat</a></nav><a href="/ris">Duplikat</a>'
        '<a href="#top">Top</a><a href="mailto:a@example.test">Mail</a>'
        '<a href="/bild.png">Bild</a><a href="/a//b#x">Dokument</a>'
        '<noscript><a href="/hidden">Hidden</a></noscript></body></html>'
    )
    assert result.out_links == [(BASE + "ris", "Rat"), (BASE + "a/b", "Dokument")]
    assert result.meta == {"title": "Bericht", "author": "Amt", "content_type": "text/html"}


def test_short_headings_update_hierarchy_without_padding_short_paragraphs():
    result = parse(
        '<h1>Klima</h1><h2>Wärme</h2><h3>Netz</h3><p>Kurz.</p>'
        '<p>Dieser Absatz beschreibt den Ausbau des kommunalen Netzes.</p>'
        '<h2>Strom</h2><li>Dieser Listenpunkt beschreibt die kommunale Stromversorgung.</li>'
    )
    assert [s.text for s in result.segments if s.segment_type == "heading"] == [
        "Klima", "Wärme", "Netz", "Strom"
    ]
    assert "Kurz." not in [s.text for s in result.segments]
    assert result.segments[3].heading_context == "Klima > Wärme > Netz"
    assert result.segments[-1].heading_context == "Klima > Strom"


def test_table_preserves_duplicate_headers_spans_empty_cells_and_units():
    result = parse(
        '<h2>Emissionen</h2><table id="bilanz" onclick="bad()">'
        '<caption>Bilanz in t CO2e</caption><thead>'
        '<tr><th rowspan="2">Jahr</th><th colspan="2">THG</th></tr>'
        '<tr><th id="a" scope="col">Wert</th><th id="b" scope="col">Wert</th></tr>'
        '</thead><tbody><tr><th scope="row">2025</th>'
        '<td headers="a"><b>1.250</b></td><td headers="b"></td></tr>'
        '<tr><th scope="row">2026</th><td>1.100</td><td>900</td></tr></tbody></table>'
    )
    segment = next(s for s in result.segments if s.segment_type == "table")
    assert segment.heading_context == "Emissionen"
    assert segment.text.startswith("HTML table 1\n")
    table = BeautifulSoup(segment.text, "html.parser").table
    assert "onclick" not in table.attrs
    assert table.caption.text == "Bilanz in t CO2e"
    rows = table.find_all("tr")
    assert rows[0].th["rowspan"] == "2"
    assert rows[0].find_all("th")[1]["colspan"] == "2"
    assert [c.text for c in rows[1].find_all("th")] == ["Wert", "Wert"]
    assert [c.get_text(strip=True) for c in rows[2].find_all(["th", "td"])] == [
        "2025", "1.250", ""
    ]
    assert rows[2].td["headers"] == ["a"]
    assert rows[2].th["scope"] == "row"
    assert "2026" in rows[3].text


def test_headerless_tables_do_not_invent_headers_or_duplicate_cell_paragraphs():
    result = parse(
        '<table><tr><td><p>Eine lange Aussage innerhalb einer Tabellenzelle.</p></td>'
        '<td>17</td></tr></table><table><tr><td>18</td></tr></table>'
    )
    assert [s.segment_type for s in result.segments] == ["table", "table"]
    assert "<th" not in result.segments[0].text
    assert result.segments[1].text.startswith("HTML table 2\n")


def test_table_evidence_survives_storage(tmp_path):
    result = parse('<table><tr><th>THG (t CO2e)</th></tr><tr><td>1.250</td></tr></table>')
    with Storage(tmp_path / "fixture.sqlite", raw_dir=tmp_path / "raw", create=True) as store:
        doc = store.store_raw("fixture", BASE, FetchResult(BASE, 200, "text/html", b"fixture", {}))
        store.store_segments_scored(doc, result.segments, lambda text: (0, 0, 0))
        row = store.conn.execute(
            "SELECT segment_type, text FROM segments WHERE document_id=?", (doc,)
        ).fetchone()
        assert row[0] == "table"
        assert row[1] == result.segments[0].text
        assert "THG (t CO2e)" in row[1] and "1.250" in row[1]
