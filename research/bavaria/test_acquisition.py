"""Source integrity and scope gates, without network or historical mutations."""

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .acquisition import connect, crawl_one, initialize, robots, store_document
from .events import extract_events
from .robots import Policy


class AcquisitionTests(unittest.TestCase):
    def test_storage_stop_before_first_request_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.jsonl"
            registry.write_text(
                json.dumps({"ags": "09161000", "homepage_candidate": "https://example.org/"})
            )
            db = root / "acquisition.sqlite"
            initialize(db, registry)
            with connect(db) as conn:
                row = conn.execute("SELECT * FROM municipalities").fetchone()
            with patch(
                "research.bavaria.acquisition.resource_gate",
                side_effect=RuntimeError("STORAGE_RESERVE_STOP"),
            ):
                self.assertEqual(crawl_one(db, root, row, 4)[1], "RESOURCE_DEFERRED")
            with connect(db) as conn:
                self.assertEqual(
                    conn.execute("SELECT error FROM requests").fetchone()[0], "STORAGE_RESERVE_STOP"
                )

    def test_missing_explicit_database_never_creates_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite"
            with self.assertRaises(FileNotFoundError):
                connect(path)
            self.assertFalse(path.exists())

    def test_candidate_denominator_counts_only_real_task_groups(self):
        from .analysis import benchmark_candidates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.jsonl"
            registry.write_text(json.dumps({"ags": "09161000"}))
            db = root / "acquisition.sqlite"
            initialize(db, registry)
            body = (
                "<h1>Klimaschutz</h1><p>" + "Allgemeine Informationen zur Energie. " * 8 + "</p>"
            ).encode()
            with patch("research.bavaria.acquisition.resource_gate"):
                store_document(
                    db,
                    root,
                    "09161000",
                    "https://example.org/",
                    "text/html",
                    body,
                    "CANDIDATE_HOST_ONLY",
                )
            result = benchmark_candidates(db, root / "candidates")
            self.assertEqual(result["tasks"], 0)
            self.assertEqual(result["municipalities"], 0)

    def test_events_do_not_promote_plans_or_cooccurrence(self):
        self.assertFalse(
            extract_events(
                "Stadt Beispiel ist dies ein wichtiges Thema. "
                "Seit 1992 fördert die Stadt Maßnahmen."
            )
        )
        self.assertFalse(extract_events("Die Stadt Beispiel. Ein Solarpark wird diskutiert."))
        self.assertFalse(extract_events("Die Stadt Beispiel plant keinen neuen Solarpark."))
        text = "Die Stadt Beispiel plant einen neuen Solarpark im Gewerbegebiet."
        event = extract_events(text)[0]
        self.assertEqual(event["predicate"], "PLANS")
        self.assertEqual(text[event["start_char"] : event["end_char"]], event["quote"])
        self.assertIsNone(event["municipality_ags"])
        self.assertFalse(event["implementation_independently_verified"])

    def test_longest_rule_and_merged_agents(self):
        policy = Policy(
            "User-agent: *\nDisallow: /\nAllow: /public\n"
            "Disallow: /public/private\nAllow: /same\nDisallow: /same\n"
        )
        self.assertTrue(policy.can_fetch("Research", "https://example.org/public/file"))
        self.assertFalse(policy.can_fetch("Research", "https://example.org/public/private/a"))
        self.assertTrue(policy.can_fetch("Research", "https://example.org/same"))
        policy = Policy("User-agent: Klima\nDisallow: /a\nUser-agent: Klima\nDisallow: /b\n")
        self.assertFalse(policy.can_fetch("KlimaResearch", "https://example.org/b"))

    def test_robots_fail_closed(self):
        for code in (401, 403, 500, 503):
            with patch(
                "research.bavaria.acquisition.fetch",
                return_value=(code, "https://example.org/robots.txt", {}, b""),
            ):
                with self.assertRaisesRegex(ValueError, "ROBOTS_UNAVAILABLE"):
                    robots(None, "https://example.org/")
        with patch("research.bavaria.acquisition.fetch", return_value=(404, "", {}, b"")):
            policy, _ = robots(None, "https://example.org/")
            self.assertTrue(policy.can_fetch("KlimaResearch", "https://example.org/a"))

    def test_dedup_and_many_municipalities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.jsonl"
            registry.write_text("\n".join(json.dumps({"ags": x}) for x in ["09161000", "09162000"]))
            db = root / "acquisition.sqlite"
            initialize(db, registry)
            body = b"<html><h1>Klimaschutz</h1><p>Solarenergie wird geplant.</p></html>"
            with patch("research.bavaria.acquisition.resource_gate"):
                for ags in ["09161000", "09162000"]:
                    digest = store_document(
                        db,
                        root,
                        ags,
                        "https://example.org/",
                        "text/html",
                        body,
                        "CANDIDATE_HOST_ONLY",
                    )
            self.assertEqual(digest, hashlib.sha256(body).hexdigest())
            with connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM associations").fetchone()[0], 2)
                self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
                raw = conn.execute("SELECT raw_path FROM documents").fetchone()[0]
                self.assertEqual(gzip.decompress(Path(raw).read_bytes()), body)
            from .analysis import build

            Path(raw).write_bytes(gzip.compress(b"tampered original"))
            with self.assertRaisesRegex(ValueError, "Original integrity failure"):
                build(db, root / "must-not-materialize.sqlite")
            with self.assertRaises(ValueError):
                initialize(db, registry)
