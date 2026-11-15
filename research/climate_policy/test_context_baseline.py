"""Strong lexical control must preserve source quotations and old method globals."""

import tempfile
import unittest
from pathlib import Path

from research.climate_policy import context_baseline as c
from research.climate_policy import retrieval, store


class ContextTests(unittest.TestCase):
    def test_context_index_keeps_original_evidence_and_shared_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "source.sqlite"
            store.build(db)
            result = c.ANALYZE(
                db,
                "Welche Förderquote nennt Straubing für Fahrradabstellanlagen am Bahnhof?",
                "bm25_context",
            )
            self.assertEqual(result["retrieval"]["candidate_units"], 894)
            self.assertNotIn("bm25_context", retrieval.METHODS)
            originals = {
                u["unit_id"]: u
                for u in (
                    __import__("json").loads(line)
                    for line in (store.EXPORT / "corpus.jsonl").read_bytes().splitlines()
                )
            }
            self.assertTrue(
                all(s["quote"] == originals[s["unit_id"]]["text"] for s in result["citations"])
            )
            self.assertEqual(result["api_calls"], 0)


if __name__ == "__main__":
    unittest.main()
