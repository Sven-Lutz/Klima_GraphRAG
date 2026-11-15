"""Metric denominators and truthful reviewer-package mechanics."""

import json
import tempfile
import unittest
from pathlib import Path

from research.climate_policy.evaluate import equal_value
from research.climate_policy.reviewer_package import package


class EvaluationTests(unittest.TestCase):
    def test_values_dates_and_unknown_not_equated(self):
        self.assertTrue(equal_value("200000.00", "200000"))
        self.assertFalse(equal_value("200000", "2000000"))
        self.assertTrue(equal_value("31.12.2026", "31.12.2026"))
        self.assertFalse(equal_value(None, "0"))

    def test_benchmark_has_provisional_not_independent_labels(self):
        path = Path("research/sprints/sprint-02/acceleration/benchmark01/tasks.json")
        data = json.loads(path.read_bytes())
        self.assertEqual(len(data["tasks"]), 14)
        self.assertEqual(sum(len(t["slots"]) for t in data["tasks"]), 24)
        self.assertEqual(data["held_out_independent_cases"], 0)
        self.assertTrue(all(not t["independent_test"] for t in data["tasks"]))
        self.assertNotIn("global retrieval recall", data["metrics"])

    def test_real_review_package_does_not_fake_human_judgments(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review"
            package(Path("research/sprints/sprint-02/acceleration/benchmark01/tasks.json"), out)
            self.assertTrue((out / "A/TASKS.md").is_file())
            self.assertTrue((out / "B/judgments.csv").is_file())
            self.assertNotIn("17716.26", (out / "A/TASKS.md").read_text())
            rows = (out / "A/judgments.csv").read_text().splitlines()[1:]
            self.assertEqual(len(rows), 14)
            self.assertTrue(all(row.count(",") == 13 for row in rows))
            self.assertTrue(
                all(p.is_symlink() and p.resolve().is_file() for p in (out / "originals").iterdir())
            )


if __name__ == "__main__":
    unittest.main()
