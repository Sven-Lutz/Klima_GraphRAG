"""Synthetic software tests; no municipal labels or human judgments are generated."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from research.offline_evaluation.contracts import ValidationError, canonical

from .human_import import import_handoff
from .metrics import field_counts, retrieval_at_k
from .review import import_decision, render, sha, validate
from .review_log import append_decisions, verify_log


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        body = b"SYNTHETIC FIXTURE ONLY"
        self.digest = sha(body)
        (self.raw / self.digest).write_bytes(body)
        self.corpus = self.root / "corpus.jsonl"
        self.catalog = self.root / "catalog.jsonl"
        self.corpus.write_bytes(
            canonical(
                {
                    "unit_id": "u1",
                    "source_url": "https://fixture.invalid/",
                    "raw_hashes": [self.digest],
                    "text": "Wärme 20 %",
                    "page_ref": None,
                    "acquisition_ags": "00000001",
                }
            )
            + b"\n"
        )
        self.catalog.write_bytes(
            canonical(
                {
                    "url": "https://fixture.invalid/",
                    "body_sha256": self.digest,
                    "source_type": "html",
                }
            )
            + b"\n"
        )
        self.item = {
            "id": "test",
            "task_family": "fixture",
            "question": "Synthetic question?",
            "municipalities": ["00000001"],
            "source_versions": [
                {
                    "source_url": "https://fixture.invalid/",
                    "raw_sha256": self.digest,
                    "acquisition_ags": "00000001",
                }
            ],
            "evidence": [
                {
                    "unit_id": "u1",
                    "start_char": 0,
                    "end_char": 10,
                    "quote": "Wärme 20 %",
                    "source_url": "https://fixture.invalid/",
                    "raw_sha256": self.digest,
                    "page_ref": None,
                }
            ],
            "proposed_answer": "SYNTHETIC ANSWER",
            "answerability": "FULL",
            "annotation_status": "PROVISIONAL_AGENT_SOURCE_CHECKED",
            "review_priority": "HIGH",
            "rationale": "Software fixture only",
            "limitations": ["Not research evidence"],
        }
        self.path = self.root / "candidates.json"
        self.write()
        self.kw = {"corpus": self.corpus, "catalog": self.catalog, "raw_dir": self.raw}

    def write(self):
        self.path.write_bytes(
            canonical({"schema_version": "klima-review-candidates/1", "candidates": [self.item]})
        )

    def decision(self):
        # Invented record confined to this test's temporary directory.
        return {
            "origin": "HUMAN_REVIEWER_SUPPLIED",
            "reviewer_id": "SYNTHETIC_TEST_REVIEWER",
            "candidate_id": "test",
            "candidate_sha256": sha(canonical(self.item)),
            "decision": "ACCEPT",
            "checked_sources": True,
            "reasoning": "SYNTHETIC TEST ONLY",
            "reviewed_at": "2026-11-02T13:00:00+01:00",
        }

    def import_record(self, record, reviewer="SYNTHETIC_TEST_REVIEWER"):
        decision = self.root / "decision.json"
        decision.write_bytes(canonical(record))
        return import_decision(
            self.path, decision, self.root / "reviews.jsonl", reviewer, **self.kw
        )

    def test_unicode_spans_and_original_bytes(self):
        before = self.path.read_bytes()
        self.assertIn("test", validate(self.path, **self.kw)[1]["candidate_sha256"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_tampered_offset_and_quote_rejected(self):
        self.item["evidence"][0]["end_char"] = 8
        self.write()
        with self.assertRaisesRegex(ValidationError, "quote"):
            validate(self.path, **self.kw)

    def test_raw_hash_tamper_rejected(self):
        (self.raw / self.digest).write_bytes(b"changed")
        with self.assertRaisesRegex(ValidationError, "hash"):
            validate(self.path, **self.kw)

    def test_unknown_source_unit_rejected(self):
        self.item["evidence"][0]["unit_id"] = "missing"
        self.write()
        with self.assertRaisesRegex(ValidationError, "Unknown"):
            validate(self.path, **self.kw)

    def test_unchecked_accept_rejected(self):
        record = self.decision()
        record["checked_sources"] = False
        with self.assertRaisesRegex(ValidationError, "Accept"):
            self.import_record(record)

    def test_reviewer_and_candidate_link_required(self):
        with self.assertRaisesRegex(ValidationError, "Reviewer"):
            self.import_record(self.decision(), reviewer="DIFFERENT_REVIEWER")
        record = self.decision()
        self.item["proposed_answer"] += " changed"
        self.write()
        with self.assertRaisesRegex(ValidationError, "current candidate"):
            self.import_record(record)

    def test_log_append_duplicate_and_tamper_detection(self):
        before = self.path.read_bytes()
        first = self.import_record(self.decision())
        log = self.root / "reviews.jsonl"
        original = log.read_bytes()
        self.assertEqual(self.import_record(self.decision()), first)
        self.assertEqual(log.read_bytes(), original)
        record = self.decision()
        record.update(
            decision="REVISE",
            reasoning="SYNTHETIC revision",
            reviewed_at="2026-11-02T13:01:00+01:00",
        )
        second = self.import_record(record)
        self.assertEqual(second["previous_event_sha256"], first["event_sha256"])
        self.assertEqual(self.path.read_bytes(), before)
        log = self.root / "reviews.jsonl"
        log.write_bytes(log.read_bytes().replace(b"SYNTHETIC revision", b"TAMPERED revision"))
        record["reasoning"] = "SYNTHETIC third"
        with self.assertRaisesRegex(ValidationError, "chain"):
            self.import_record(record)

    def test_blind_packet_separates_proposed_answers_and_refuses_overwrite(self):
        out = self.root / "packet"
        render(self.path, out, **self.kw)
        self.assertNotIn("SYNTHETIC ANSWER", (out / "01_questions_and_sources.md").read_text())
        self.assertIn("SYNTHETIC ANSWER", (out / "02_proposed_references.md").read_text())
        self.assertIn("questions/test.md", (out / "REVIEW_START.md").read_text())
        question = (out / "questions/test.md").read_text()
        self.assertNotIn("SYNTHETIC ANSWER", question)
        self.assertNotIn("Software fixture only", question)
        self.assertIn("SYNTHETIC ANSWER", (out / "answers/test.md").read_text())
        self.assertFalse((out / "reviews.jsonl").exists())
        with self.assertRaisesRegex(ValidationError, "fresh"):
            render(self.path, out, **self.kw)

    def test_path_traversal_candidate_ids_rejected_before_render(self):
        for invalid in ("../escape", "a/b", "/absolute", "a\\b", ".", "..", "a b"):
            self.item["id"] = invalid
            self.write()
            with self.assertRaisesRegex(ValidationError, "Unsafe candidate ID"):
                render(self.path, self.root / "packet", **self.kw)
            self.assertFalse((self.root / "packet").exists())

    def test_original_alias_is_relative_and_preserves_raw_bytes(self):
        out = self.root / "packet"
        before = (self.raw / self.digest).read_bytes()
        render(self.path, out, **self.kw)
        alias = out / "originals" / (self.digest + ".html")
        self.assertTrue(alias.is_symlink())
        self.assertFalse(Path(__import__("os").readlink(alias)).is_absolute())
        self.assertEqual(alias.resolve(), (self.raw / self.digest).resolve())
        self.assertEqual(sha(alias.read_bytes()), self.digest)
        self.assertEqual((self.raw / self.digest).read_bytes(), before)
        question = (out / "questions/test.md").read_text()
        self.assertIn(str(out.resolve() / "originals" / (self.digest + ".html")), question)
        self.assertIn("keine vollständige Wiedergabe", question)

    def test_review_index_orders_priority_then_stable_id(self):
        second = copy.deepcopy(self.item)
        second.update(id="aaa", review_priority="LOW")
        third = copy.deepcopy(self.item)
        third.update(id="zzz", review_priority="HIGH")
        self.path.write_bytes(
            canonical(
                {
                    "schema_version": "klima-review-candidates/1",
                    "candidates": [second, third, self.item],
                }
            )
        )
        out = self.root / "packet"
        render(self.path, out, **self.kw)
        index = (out / "REVIEW_START.md").read_text()
        self.assertNotIn("|\n\n|", index)
        self.assertLess(index.index("questions/test.md"), index.index("questions/zzz.md"))
        self.assertLess(index.index("questions/zzz.md"), index.index("questions/aaa.md"))

    def test_cannot_promote_candidate_to_human_gold(self):
        self.item["annotation_status"] = "ADJUDICATED"
        self.write()
        with self.assertRaisesRegex(ValidationError, "provisional"):
            validate(self.path, **self.kw)

    def test_retrieval_requires_complete_real_qrels_and_retains_rank(self):
        with self.assertRaisesRegex(ValidationError, "Complete"):
            retrieval_at_k(["n", "p"], {"n": False, "p": True}, 1, judgments_complete=False)
        result = retrieval_at_k(["n", "p"], {"n": False, "p": True}, 1, judgments_complete=True)
        self.assertEqual(result["recall_at_k"], 0)
        self.assertEqual(result["precision_at_k_returned"], 0)
        with self.assertRaisesRegex(ValidationError, "outside"):
            retrieval_at_k(["unknown"], {"p": True}, 1, judgments_complete=True)

    def test_empty_retrieval_and_negative_scope_have_explicit_na(self):
        result = retrieval_at_k([], {"n": False}, 5, judgments_complete=True)
        self.assertIsNone(result["precision_at_k_returned"])
        self.assertIsNone(result["recall_at_k"])

    def test_fields_distinguish_absent_unknown_wrong_and_missing(self):
        result = field_counts(
            {
                "a": {"status": "PRESENT", "value": 20},
                "b": {"status": "ABSENT"},
                "c": {"status": "UNKNOWN"},
                "d": {"status": "PRESENT", "value": "MW"},
            },
            {
                "a": {"status": "PRESENT", "value": "20"},
                "b": {"status": "ABSENT"},
                "c": {"status": "PRESENT", "value": "not gold"},
            },
        )
        self.assertEqual(result["false_positive"], 1)
        self.assertEqual(result["false_negative"], 2)
        self.assertEqual(result["unknown_gold_fields_excluded"], 1)
        self.assertEqual(result["true_absent"], 1)

    def test_unknown_prediction_is_not_true_absence(self):
        result = field_counts({"a": {"status": "ABSENT"}}, {})
        self.assertEqual(result["true_absent"], 0)
        self.assertEqual(result["absence_not_predicted"], 1)

    def handoff(self, direct=False):
        row = {
            "case_id": "test",
            "decision": "accept",
            "reviewer_id": "SYNTHETIC_TEST_REVIEWER",
            "candidate_sha256": sha(canonical(self.item)),
            "archived_source_sha256": self.digest,
            "original_access_attestation": "reviewer_yes"
            if direct
            else "reviewer_did_not_open_html",
            "read_basis": "Synthetic chat text",
            "reason": "SYNTHETIC testimony, not an actual human review",
        }
        return {
            "artifact_type": "human_review_decisions_transcribed_from_chat",
            "human_gold_release": False,
            "decision_count": 1,
            "decisions": [row],
        }

    def import_handoff_fixture(self, data):
        path = self.root / "handoff.json"
        path.write_text(json.dumps(data, indent=2))
        return import_handoff(
            path,
            self.path,
            self.root / "reviews.jsonl",
            sha(path.read_bytes()),
            "SYNTHETIC_TEST_REVIEWER",
            **self.kw,
        )

    def test_handoff_retry_is_byte_identical_and_preserves_basis_and_unknown_time(self):
        data = self.handoff()
        first = self.import_handoff_fixture(data)
        log = self.root / "reviews.jsonl"
        before = log.read_bytes()
        second = self.import_handoff_fixture(data)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["already_imported"], 1)
        self.assertEqual(log.read_bytes(), before)
        decision = verify_log(before)[0]["decision"]
        self.assertEqual(decision["human_testimony"], data["decisions"][0])
        self.assertFalse(decision["source_basis"]["source_directly_opened"])
        self.assertIsNone(decision["reviewed_at"])
        self.assertEqual(decision["gold_release"], "NOT_AUTHORIZED")

    def test_handoff_preflight_rejects_last_bad_item_without_partial_import(self):
        data = self.handoff()
        second = copy.deepcopy(self.item)
        second["id"] = "second"
        self.path.write_bytes(
            canonical(
                {"schema_version": "klima-review-candidates/1", "candidates": [self.item, second]}
            )
        )
        bad = copy.deepcopy(data["decisions"][0])
        bad.update(case_id="second", candidate_sha256="0" * 64)
        data["decisions"].append(bad)
        data["decision_count"] = 2
        with self.assertRaisesRegex(ValidationError, "Canonical candidate hash"):
            self.import_handoff_fixture(data)
        self.assertFalse((self.root / "reviews.jsonl").exists())

    def test_handoff_rejects_wrong_source_reviewer_and_input_hash(self):
        for field, value, error in (
            ("archived_source_sha256", "0" * 64, "Source version"),
            ("reviewer_id", "WRONG", "Reviewer"),
        ):
            data = self.handoff()
            data["decisions"][0][field] = value
            with self.assertRaisesRegex(ValidationError, error):
                self.import_handoff_fixture(data)
        path = self.root / "handoff.json"
        with self.assertRaisesRegex(ValidationError, "input hash"):
            import_handoff(
                path,
                self.path,
                self.root / "reviews.jsonl",
                "0" * 64,
                "SYNTHETIC_TEST_REVIEWER",
                **self.kw,
            )

    def test_handoff_duplicate_case_and_missing_read_basis_rejected(self):
        data = self.handoff()
        data["decisions"].append(copy.deepcopy(data["decisions"][0]))
        data["decision_count"] = 2
        with self.assertRaisesRegex(ValidationError, "Duplicate case"):
            self.import_handoff_fixture(data)
        data = self.handoff()
        del data["decisions"][0]["read_basis"]
        with self.assertRaisesRegex(ValidationError, "actual source basis"):
            self.import_handoff_fixture(data)

    def test_handoff_direct_original_attestation_kept_separate(self):
        result = self.import_handoff_fixture(self.handoff(direct=True))
        self.assertEqual(
            result["source_basis_counts"], {"direct_original": 1, "chat_editorial_text": 0}
        )

    def test_semantic_retry_survives_json_formatting_and_v1_history(self):
        first = self.import_record(self.decision())
        log = self.root / "reviews.jsonl"
        before = log.read_bytes()
        path = self.root / "decision.json"
        path.write_text(json.dumps(self.decision(), indent=4, sort_keys=True))
        again = import_decision(self.path, path, log, "SYNTHETIC_TEST_REVIEWER", **self.kw)
        self.assertEqual(first, again)
        self.assertEqual(log.read_bytes(), before)
        event = json.loads(before)
        event["schema_version"] = "klima-human-review-log/1"
        event.pop("decision_sha256")
        event["event_sha256"] = sha(
            canonical({k: v for k, v in event.items() if k != "event_sha256"})
        )
        legacy = canonical(event) + b"\n"
        log.write_bytes(legacy)
        self.import_record(self.decision())
        self.assertEqual(log.read_bytes(), legacy)

    def test_batch_conflict_requires_explicit_amendment_preserves_old_history(self):
        first = self.import_handoff_fixture(self.handoff())
        log = self.root / "reviews.jsonl"
        before = log.read_bytes()
        old = verify_log(before)[0]
        changed = copy.deepcopy(old["decision"])
        changed["reasoning"] = "SYNTHETIC explicit amendment"
        manifest = validate(self.path, **self.kw)[1]
        with self.assertRaisesRegex(ValidationError, "supersedes"):
            append_decisions(log, [changed], manifest, "0" * 64)
        self.assertEqual(log.read_bytes(), before)
        changed["supersedes_event_sha256"] = first["chain_tip"]
        append_decisions(log, [changed], manifest, "0" * 64)
        self.assertTrue(log.read_bytes().startswith(before))

    def test_truncated_and_reordered_logs_fail_closed(self):
        self.import_handoff_fixture(self.handoff())
        data = (self.root / "reviews.jsonl").read_bytes()
        with self.assertRaisesRegex(ValidationError, "partial"):
            verify_log(data[:-1])
        event = json.loads(data)
        event["previous_event_sha256"] = "0" * 64
        event["event_sha256"] = sha(
            canonical({k: v for k, v in event.items() if k != "event_sha256"})
        )
        with self.assertRaisesRegex(ValidationError, "chain"):
            verify_log(canonical(event) + b"\n")

    def test_conflicting_original_access_summary_rejected(self):
        data = self.handoff(direct=False)
        data["source_access_summary"] = {
            "reviewer_direct_original_opened_cases": ["test"],
            "chat_rendered_source_content_cases": [],
        }
        with self.assertRaisesRegex(ValidationError, "contradicts summary"):
            self.import_handoff_fixture(data)

    def test_concurrent_retries_append_once(self):
        from concurrent.futures import ThreadPoolExecutor

        path = self.root / "parallel.json"
        path.write_bytes(canonical(self.handoff()))
        expected = sha(path.read_bytes())
        log = self.root / "reviews.jsonl"

        def execute(_):
            return import_handoff(
                path, self.path, log, expected, "SYNTHETIC_TEST_REVIEWER", **self.kw
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(execute, range(3)))
        self.assertEqual(sum(row["imported"] for row in results), 1)
        self.assertEqual(len(verify_log(log.read_bytes())), 1)

    def test_false_premise_target_does_not_overwrite_evidence_municipality(self):
        self.item["municipalities"] = ["00000002"]
        self.write()
        package, _, _ = validate(self.path, **self.kw)
        self.assertEqual(package["candidates"][0]["municipalities"], ["00000002"])
        self.assertEqual(
            package["candidates"][0]["source_versions"][0]["acquisition_ags"], "00000001"
        )


if __name__ == "__main__":
    unittest.main()
