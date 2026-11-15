"""Synthetic mechanics only; no new municipal gold claims."""

import unittest
from copy import deepcopy

from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from research.experiments.m4a2_segmentation_admission import candidates as c
from research.experiments.m4a2_segmentation_admission import experiment as e
from research.experiments.m4a_parsing_admission import diagnose as d


def segment(text, kind="paragraph", heading=None):
    return {
        "text": text,
        "segment_type": kind,
        "heading_context": heading,
        "page_ref": None,
        "order_index": 0,
        "text_sha256": d.digest(text),
    }


class CandidateTests(unittest.TestCase):
    def test_fragment_preservation_has_structural_witness_and_excludes_menu(self):
        html = b"""<html><body><nav><p>Menu fragment</p><p>Other long unrelated menu entry
        with text</p></nav>
        <h1>Public facilities</h1><div><p>This is a substantive neighbouring statement.</p>
        <p>Short facility</p></div><div><p>Lonely small</p></div></body></html>"""
        f = FetchResult("https://test.invalid", 200, "text/html", html, {})
        baseline = parse_html(f, f.url_final)
        candidate = c.experimental_parser()
        parsed = candidate.parse_html(f, f.url_final)
        self.assertFalse(any(s.text == "Short facility" for s in baseline.segments))
        added = [s for s in parsed.segments if s.text == "Short facility"]
        self.assertEqual(added[0].segment_type, "context_fragment")
        self.assertFalse(any(s.text == "Lonely small" for s in parsed.segments))
        self.assertFalse(any("Menu fragment" in s.text for s in parsed.segments))
        self.assertTrue(candidate.witnesses)
        self.assertEqual(parse_html(f, f.url_final), baseline)

    def test_links_do_not_become_fragments(self):
        f = FetchResult(
            "https://test.invalid",
            200,
            "text/html",
            (
                b"<h1>Energy</h1><p>Long enough factual neighbour"
                b' sentence.</p><p><a href="/">Short menu</a></p>'
            ),
            {},
        )
        p = c.experimental_parser().parse_html(f, f.url_final)
        self.assertFalse(any(s.text == "Short menu" for s in p.segments))

    def test_context_admission_and_unknown_context(self):
        rows = [
            segment("Energie", "heading"),
            segment("Year 2030"),
            segment("Plain statement"),
            segment("Other topic", "heading"),
            segment("Year 2030"),
        ]
        kept, decisions = c.alternative(rows)
        self.assertIn(1, kept)
        self.assertNotIn(4, kept)
        self.assertEqual(decisions[4]["reason"], "EXCLUDE_UNRESOLVED_CONTEXT")

    def test_contact_veto_not_one_email_mention(self):
        self.assertTrue(c.contact_signature("Telefon 123, Fax 456"))
        self.assertFalse(c.contact_signature("Email supports the policy process."))
        rows = [segment("Energie", "heading"), segment("Telefon 123 Fax 456 Energie " + "x" * 90)]
        kept, _ = c.alternative(rows)
        self.assertNotIn(1, kept)

    def test_dedup_is_local_to_context_and_keeps_alias(self):
        rows = [
            segment("Energie facts", "heading"),
            segment("2020 value", heading="A"),
            segment("2020 value", heading="A"),
            segment("2020 value", heading="B"),
        ]
        kept, decisions = c.alternative(rows)
        self.assertIn(1, kept)
        self.assertNotIn(2, kept)
        self.assertIn(3, kept)
        self.assertEqual(decisions[2]["duplicate_of_segment_index"], 1)
        self.assertIn(1, c.alternative([rows[0], rows[1]])[0])

    def test_no_mutation_and_source_identity(self):
        rows = [segment("Energie", "heading"), segment("Year 2030")]
        old = deepcopy(rows)
        c.alternative(rows)
        self.assertEqual(rows, old)
        source = {"body_sha256": "raw", "url": "https://a.invalid", "ags": "09163000"}
        record = {
            "raw_sha256": "raw",
            "source_url": "https://a.invalid",
            "acquisition_ags": "09163000",
        }
        self.assertTrue(e.source_identity(record, source))
        self.assertFalse(e.source_identity({**record, "acquisition_ags": "09263000"}, source))
        self.assertFalse(e.source_identity({**record, "source_url": "https://b.invalid"}, source))

    def test_ambiguity_stays_unresolved(self):
        rows = [segment("known known")]
        self.assertEqual(e.observe({"quote": "known"}, rows, {0})["status"], "UNRESOLVED_MAPPING")

    def test_numeric_fragment_without_topic_is_not_auto_admitted(self):
        self.assertEqual(c.alternative([segment("2020")])[0], set())


if __name__ == "__main__":
    unittest.main()
