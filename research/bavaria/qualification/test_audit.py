"""Critical source-authority, denominator and dependency controls without network."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from .audit import Components, coverage_layer, email_corroboration, review_originals, url_family


class QualificationTests(unittest.TestCase):
    def test_contact_match_does_not_promote_authority_or_match_host_prefix(self):
        record = {
            "homepage_candidate": "https://www.town.de/",
            "official_contacts": [{"email": "office@town.de"}],
        }
        matched = email_corroboration(record)
        self.assertEqual(
            matched["email_host_corroboration"], "OFFICIAL_CONTACT_HOST_OR_SUBDOMAIN_MATCH"
        )
        self.assertFalse(matched["website_authority_verified"])
        record["homepage_candidate"] = "https://town.de.evil.org/"
        self.assertEqual(
            email_corroboration(record)["email_host_corroboration"], "NO_MATCH_NOT_DISPROOF"
        )
        record["homepage_candidate"] = "https://web.de/"
        record["official_contacts"] = [{"email": "town@web.de"}]
        self.assertFalse(email_corroboration(record)["website_authority_verified"])
        self.assertEqual(email_corroboration(record)["matching_email_domains"], "")

    def test_source_review_denominator_does_not_count_unsampled_municipalities(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
        CREATE TABLE municipalities(ags TEXT,record TEXT,status TEXT,checked_at TEXT);
        CREATE TABLE associations(ags TEXT,sha256 TEXT,url TEXT);
        CREATE TABLE units(sha256 TEXT);
        """
        )
        for i, state in enumerate(["RELEVANT_CANDIDATES_FOUND", "RESOURCE_DEFERRED"]):
            record = {
                "official_name": f"Town {i}",
                "region": "Region",
                "population": 1000,
                "population_date": "2024-12-31",
                "homepage_candidate": None,
                "official_contacts": [],
            }
            conn.execute(
                "INSERT INTO municipalities VALUES (?,?,?,NULL)",
                (str(i), json.dumps(record), state),
            )
        with tempfile.TemporaryDirectory() as directory:
            rows, report = coverage_layer(
                conn,
                Path(directory),
                [{"ags": "0", "relevance": "SUBSTANTIVE", "claim_scope": "EXPLICIT_LOCAL"}],
            )
        self.assertEqual(report["municipalities"], 2)
        self.assertEqual(report["sampled_municipalities"], 1)
        self.assertEqual(rows[1]["sampled_originals"], 0)
        self.assertEqual(rows[1]["crawl_status_unmodified"], "RESOURCE_DEFERRED")
        self.assertIsNone(report["qualified_population_policy_coverage"])
        self.assertFalse(any(r["website_authority_verified"] for r in rows))

    def test_transitive_source_and_institution_dependencies_stay_together(self):
        groups = Components(["a", "b", "c", "d"])
        groups.join(["a", "b"])
        groups.join(["b", "c"])
        self.assertEqual(groups.find("a"), groups.find("c"))
        self.assertNotEqual(groups.find("a"), groups.find("d"))

    def test_url_families_keep_meaningful_news_query_ids(self):
        a = url_family("https://town.de/news?id=12&utm_source=feed#top")
        b = url_family("https://town.de/news?id=12")
        c = url_family("https://town.de/news?id=13")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertIn("cHash=abc", url_family("https://town.de/news?cHash=abc&id=12"))

    def test_review_rejects_changed_preinspection_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "selection.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "sample freeze changed"):
                review_originals(root)


if __name__ == "__main__":
    unittest.main()
