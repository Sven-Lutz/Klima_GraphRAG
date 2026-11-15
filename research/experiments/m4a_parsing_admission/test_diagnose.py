"""Mechanical regression tests, not new municipal ground truth."""

import hashlib
import unittest
from copy import deepcopy

from research.experiments.m4a_parsing_admission import diagnose as d


def segment(text, order=0):
    return {
        "text": text,
        "order_index": order,
        "page_ref": None,
        "heading_context": None,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


class DiagnosisTests(unittest.TestCase):
    def test_exact_and_reversible_whitespace(self):
        rows = [segment("Abc\n  50 % bis\t2026.")]
        hit = d.matches(rows, "Abc 50 % bis 2026.")[0]
        self.assertEqual(hit["match"], "WHITESPACE_ONLY")
        self.assertEqual(hit["pieces"][0]["start_char"], 0)
        self.assertEqual(hit["pieces"][0]["end_char"], len(rows[0]["text"]))
        self.assertFalse(d.matches(rows, "Abc 50 % bis 2027."))
        self.assertFalse(d.matches(rows, "abc 50 % bis 2026."))

    def test_cross_segment_mapping_and_filter_gap(self):
        rows = [segment("alpha", 7), segment("beta", 8), segment("gamma", 9)]
        hit = d.matches(rows, "alpha beta")[0]
        self.assertEqual([p["segment_index"] for p in hit["pieces"]], [0, 1])
        self.assertFalse(d.matches(rows, "alpha gamma", {0, 2}))
        self.assertFalse(d.matches(rows, "alpha beta", {0, 2}))
        self.assertTrue(d.matches(rows, "alpha beta", {0, 1}))

    def test_all_ambiguous_locators_preserved(self):
        hits = d.matches([segment("same same")], "same")
        self.assertEqual(len(hits), 2)
        self.assertEqual(d.classify("VALID", [], hits, hits, "P1"), "UNRESOLVED_MAPPING")

    def test_source_and_historical_locator(self):
        e = {
            "source_url": "https://a.invalid",
            "raw_sha256": "x",
            "start_char": 0,
            "end_char": 5,
            "quote": "alpha",
            "page_ref": "1",
        }
        u = {"source_url": e["source_url"], "raw_hashes": ["x"], "text": "alpha", "page_ref": "1"}
        self.assertEqual(d.historical_locator(e, u), "VALID")
        for key, value in [("source_url", "https://b.invalid"), ("raw_hashes", ["y"])]:
            wrong = {**u, key: value}
            self.assertEqual(d.historical_locator(e, wrong), "WRONG_SOURCE")
        self.assertEqual(
            d.historical_locator({**e, "end_char": 6}, u), "INVALID_HISTORICAL_LOCATOR"
        )
        self.assertEqual(
            d.historical_locator({**e, "page_ref": "2"}, u), "INVALID_HISTORICAL_LOCATOR"
        )
        self.assertEqual(d.historical_locator(e, None), "INVALID_HISTORICAL_LOCATOR")

    def test_stage_classifications(self):
        one = [{"pieces": []}]
        for full, seg, kept, condition, expected in [
            ([], [], [], "P0", "PARSER_TEXT_LOSS"),
            (one, [], [], "P1", "SEGMENTATION_LOSS"),
            (one, one, [], "P2", "ADMISSION_LOSS"),
            ([], one, one, "P1", "FOUND"),
            (one, one, one, "P2", "FOUND"),
        ]:
            self.assertEqual(d.classify("VALID", full, seg, kept, condition), expected)
        self.assertEqual(d.classify("WRONG_SOURCE", one, one, one, "P1"), "WRONG_SOURCE")

    def test_production_filter_boundaries_and_no_mutation(self):
        rows = [
            segment("Klimaschutz " + "a" * (79 - len("Klimaschutz "))),
            segment("Klimaschutz " + "a" * (80 - len("Klimaschutz "))),
            segment("x" * 80),
            segment("Klimaschutz Impressum " + "a" * 90),
        ]
        saved = deepcopy(rows)
        kept, decisions, _ = d.admission(rows)
        self.assertEqual(kept, {1})
        self.assertEqual(decisions[0]["reasons"], ["LENGTH_LT_80"])
        self.assertEqual(decisions[2]["reasons"], ["SCORE_LT_10"])
        self.assertIn("NEGATIVE", decisions[3]["reasons"])
        self.assertEqual(rows, saved)
        scorer, _ = d.filter_components()
        self.assertEqual(scorer("Klimaschutz"), (25, 1, 0))

    def test_network_and_subprocess_guard(self):
        for event in ["socket.connect", "socket.getaddrinfo", "socket.bind", "urllib.Request"]:
            with self.assertRaises(RuntimeError):
                d.guard(event, ())
        with self.assertRaises(ValueError):
            d.guard("subprocess.Popen", ("curl",))
        d.guard("subprocess.Popen", ("/usr/local/bin/pdftotext",))

    def test_character_mapping_unicode_and_repeated_spaces(self):
        norm, mapping = d.normalized("ä\t\n50\u00a0%")
        self.assertEqual(norm, "ä 50 %")
        self.assertEqual(mapping[1], (1, 3))
        self.assertEqual(mapping[-1], (6, 7))


if __name__ == "__main__":
    unittest.main()
