"""Audit safety/invariance and frozen-accounting checks; no new quality gold."""

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from research.experiments.m4a2_segmentation_admission.prepare_controls import node_path
from research.experiments.m4a3_evidence_audit import inventory as a
from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def read(name):
    return json.loads((BASE / name).read_bytes())


class AuditTests(unittest.TestCase):
    def test_origin_observer_preserves_output_and_original_preclean_paths(self):
        body = (
            b"<html><body><div>before</div><nav>ignored</nav><div><h2>Energy</h2><p>A fact"
            b"ual energy statement that exceeds the minimum length.</p></div></body></html>"
        )
        f = FetchResult("https://fixture.invalid", 200, "text/html", body, {})
        original = parse_html(f, f.url_final)
        observed, origins = a.observed_html_parser()
        result = observed(f, f.url_final)
        self.assertEqual(original, result)
        raw = BeautifulSoup(body, "lxml")
        paths = {node_path(n) for n in raw.find_all(True)}
        self.assertEqual(len(origins), len(result.segments))
        self.assertTrue(all(o["dom_path"] in paths for o in origins))
        self.assertEqual(origins[-1]["dom_path"], "html[0]/body[0]/div[1]/p[0]")

    def test_sample_is_order_independent_and_no_larger_than_frozen_bound(self):
        rows = read("freeze01/inventory.json")["added"]
        self.assertEqual(
            a.select_sample(rows)["selected_ids"],
            a.select_sample(list(reversed(rows)))["selected_ids"],
        )
        self.assertEqual(
            a.select_sample(rows)["selected_ids"],
            read("freeze01/sample_freeze.json")["selected_ids"],
        )
        self.assertEqual(len(a.select_sample(rows)["selected_ids"]), 48)

    def test_exhaustive_added_removed_and_character_accounting(self):
        x = read("freeze01/inventory.json")
        self.assertEqual(len(x["added"]), 338)
        self.assertEqual(len(x["removed"]), 2)
        self.assertEqual(
            sum(r["characters"] for r in x["added"]) - sum(r["characters"] for r in x["removed"]),
            55750 - 33579,
        )
        self.assertEqual(len({r["id"] for r in x["added"] + x["removed"]}), 340)

    def test_all_duplicate_excesses_partition_and_originals_present(self):
        x = read("freeze01/duplicates.json")
        origins = read("freeze01/duplicate_origins.json")
        self.assertEqual(sum(g["excess"] for g in x["groups"]), 75)
        self.assertEqual(x["within_source_excess"] + x["cross_source_excess"], 75)
        self.assertEqual(len(origins), 107)
        self.assertTrue(all(i in origins for g in x["groups"] for i in g["occurrence_ids"]))

    def test_reviews_match_frozen_sample_and_stay_provisional(self):
        x = read("sample_reviews.json")
        sample = read("freeze01/sample_freeze.json")
        self.assertEqual({r["inventory_id"] for r in x["reviews"]}, set(sample["selected_ids"]))
        self.assertEqual(len(x["reviews"]), 48)
        self.assertTrue(
            all(
                not r["independent_human_review"] and not r["historical_labels_changed"]
                for r in x["reviews"]
            )
        )
        self.assertTrue(
            all(
                r["claim_ags_adjudication"] == "NOT_INFERRED_FROM_ACQUISITION_AGS"
                for r in x["reviews"]
            )
        )

    def test_c22_target_is_not_assigned_to_substring_hit(self):
        x = read("detailed_findings.json")["C22"]
        self.assertEqual(x["status"], "UNRESOLVED_TARGET_NOT_EMITTED")
        self.assertTrue(
            all(
                r["origin"]["dom_path"] != x["original_target_dom_path"]
                for r in x["competing_output_origins"]
            )
        )
        self.assertEqual(len(x["competing_output_origins"]), 2)
        self.assertFalse(x["historical_annotation_changed"])

    def test_input_integrity(self):
        x = read("audit_protocol.json")
        self.assertTrue(all(d.sha(d.ROOT / p) == h for p, h in x["protected_sha256"].items()))


if __name__ == "__main__":
    unittest.main()
