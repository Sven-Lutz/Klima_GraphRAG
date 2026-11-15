"""Integrity, binding and query-equivalence gates without network or inference."""

import json
import sqlite3
import unittest
from pathlib import Path

from .evaluate import context_select, graph_matches, sql_matches, validate_patterns
from .prepare import sha


class RelationalTests(unittest.TestCase):
    def test_shared_actor_join_does_not_merge_cooccurring_actors(self):
        rows = [
            ("r1", "a1", "FUNDS", "p1"),
            ("r2", "a1", "FUNDS", "p2"),
            ("r3", "a2", "FUNDS", "p1"),
            ("r4", "a2", "PROJECT_CARRIER_FOR", "p2"),
        ]
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE relations(relation_id,subject_id,predicate,object_id)")
            conn.executemany("INSERT INTO relations VALUES (?,?,?,?)", rows)
            records = [
                dict(zip(("relation_id", "subject_id", "predicate", "object_id"), row))
                for row in rows
            ]
            request = [("?actor", "FUNDS", "p1"), ("?actor", "FUNDS", "p2")]
            actual, _ = graph_matches(records, request)
            self.assertEqual(actual, ["r1", "r2"])
            self.assertEqual(actual, sql_matches(conn, request))
            self.assertFalse(sql_matches(conn, [("a2", "FUNDS", "p2")]))

    def test_values_are_bound_as_values_not_executed_as_sql(self):
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE relations(relation_id,subject_id,predicate,object_id)")
            self.assertFalse(sql_matches(conn, [("x' OR 1=1 --", "FUNDS", "?project")]))
            self.assertEqual(conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_plan_bound_and_whole_context_budget(self):
        with self.assertRaises(ValueError):
            validate_patterns([])
        with self.assertRaises(ValueError):
            validate_patterns([("a", "?unknown_predicate", "b")])
        chosen, chars = context_select(["long", "small"], {"long": "X" * 20, "small": "ABC"}, 2, 10)
        self.assertEqual(chosen, ["small"])
        self.assertEqual(chars, 3)

    def test_frozen_relations_resolve_exact_original_bound_spans(self):
        folder = Path("generated/bavaria/continuation/relational/dataset")
        if not folder.is_dir():
            self.skipTest("Local research subset not materialized")
        freeze = json.loads((folder / "FREEZE.json").read_bytes())
        for name, expected in freeze["files_sha256"].items():
            self.assertEqual(sha((folder / name).read_bytes()), expected)
        with sqlite3.connect(
            (folder / "evidence.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as conn:
            texts = dict(conn.execute("SELECT evidence_id,text FROM evidence"))
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        records = [
            json.loads(line) for line in (folder / "relations.jsonl").read_text().splitlines()
        ]
        for record in records:
            text = texts[record["evidence_id"]]
            self.assertEqual(text[record["start_char"] : record["end_char"]], record["quote"])
            self.assertTrue(record["original_locator"]["original_sha256_verified"])
            self.assertEqual(record["source_type"], "segment")
            self.assertEqual(record["status"], "PROVISIONAL_AGENT_SOURCE_REVIEWED")
            self.assertTrue(set(record["context_evidence_ids"]).issubset(texts))

    def test_no_held_out_development_component_overlap(self):
        folder = Path("generated/bavaria/continuation/relational/dataset")
        if not folder.is_dir():
            self.skipTest("Local research subset not materialized")
        tasks = [json.loads(line) for line in (folder / "tasks.jsonl").read_text().splitlines()]
        dev = {c for task in tasks if task["split"] == "development" for c in task["component_ids"]}
        evaluation = {
            c
            for task in tasks
            if task["split"] == "evaluation_provisional"
            for c in task["component_ids"]
        }
        self.assertFalse(dev & evaluation)
        self.assertEqual(sum(t["split"] == "quarantine_cross_component" for t in tasks), 2)


if __name__ == "__main__":
    unittest.main()
