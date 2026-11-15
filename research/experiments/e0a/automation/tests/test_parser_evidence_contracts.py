"""Executable behavioral contracts for the REAL project's HTML parser.

Run at repository root after integration:
    python3 -m unittest -v research.experiments.e0a.automation.tests.test_parser_evidence_contracts
or:
    python3 research/experiments/e0a/automation/tests/test_parser_evidence_contracts.py

IMPORTANT: Tests are deliberately strict. Some are expected to FAIL on the
current implementation. Failures identify gaps, not a green quality gate.
"""
import unittest
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html

BASE = "https://example.municipality.test/"


def parse(html):
    f = FetchResult(url_final=BASE, status_code=200,
        content_type="text/html", body=html.encode("utf-8"), headers={})
    return parse_html(f,BASE)


class EvidenceContractTests(unittest.TestCase):
    def test_control_main_body_link_is_discovered(self):
        r = parse('<html><body><main><a href="/klimaplan">Klimaplan</a></main></body></html>')
        self.assertIn((BASE+'klimaplan','Klimaplan'), list(r.out_links))

    def test_contract_nav_only_ris_link_must_be_discovered(self):
        r = parse('<html><body><nav><a href="/ris">Ratsinformationssystem</a></nav>'
                  '<main><p>Willkommen bei der Gemeinde und ihren öffentlichen Informationen.</p></main></body></html>')
        self.assertIn((BASE+'ris','Ratsinformationssystem'),list(r.out_links),
                      'Navigation-only links must remain discoverable even if nav is removed from indexed text.')

    def test_contract_short_heading_context_retained(self):
        r = parse('<html><main><h2>Wärme</h2>'
                  '<p>Der Gemeinderat beschließt die kommunale Wärmeplanung im Juni.</p></main></html>')
        paragraph = next(s for s in r.segments if 'Der Gemeinderat' in s.text)
        self.assertIn('Wärme', paragraph.heading_context or '',
                      'A short but meaningful heading must not lose its context function.')

    def test_contract_table_evidence_retained_as_segment(self):
        r = parse('<html><main><h2>Emissionen</h2><table>'
                  '<tr><th>Jahr</th><th>THG</th><th>Einheit</th></tr>'
                  '<tr><td>2025</td><td>1.250</td><td>t CO2e</td></tr>'
                  '</table></main></html>')
        segment_text = ' '.join(s.text for s in r.segments)
        self.assertIn('1.250',segment_text,
                      'Table values must be available in persisted segments, not only transient full_text.')
        self.assertIn('t CO2e',segment_text)

    def test_control_paragraph_is_segmented(self):
        r = parse('<html><main><p>Die Gemeinde veröffentlicht eine aktuelle Wärmeplanung mit Anlagen.</p></main></html>')
        self.assertTrue(any('Wärmeplanung' in s.text for s in r.segments))

    def test_control_inline_anchor_is_not_external_document(self):
        r = parse('<html><body><a href="#top">nach oben</a></body></html>')
        self.assertEqual([],list(r.out_links))

if __name__=='__main__': unittest.main(verbosity=2)
