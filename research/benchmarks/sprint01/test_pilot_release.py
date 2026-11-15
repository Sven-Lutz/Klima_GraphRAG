"""Synthetic software fixtures only; owner authorization here is invented test data."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from research.offline_evaluation.contracts import ValidationError, canonical

from .pilot_release import build_manifest, digest, freeze, require_pilot_metric, verify_release
from .release_gates import validate_split
from .review import validate
from .review_log import append_decisions


class PilotReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        body = b"SYNTHETIC source fixture, never a municipal gold item"
        self.body_hash = digest(body)
        (self.raw / self.body_hash).write_bytes(body)
        self.corpus = self.root / "corpus.jsonl"
        self.catalog = self.root / "catalog.jsonl"
        self.candidates = self.root / "candidates.json"
        self.log = self.root / "human.jsonl"
        self.auth = self.root / "authorization.json"
        url = "https://fixture.invalid/source"
        text = "SYNTHETIC Wärme 20 %"
        self.corpus.write_bytes(
            canonical(
                {
                    "unit_id": "u1",
                    "source_url": url,
                    "raw_hashes": [self.body_hash],
                    "text": text,
                    "page_ref": None,
                    "acquisition_ags": "00000001",
                }
            )
            + b"\n"
        )
        self.catalog.write_bytes(
            canonical(
                {
                    "url": url,
                    "body_sha256": self.body_hash,
                    "ags": "00000001",
                    "source_type": "html",
                    "publishing_institution": "SYNTHETIC TEST PUBLISHER",
                }
            )
            + b"\n"
        )
        self.rows = [
            {
                "id": cid,
                "task_family": "SYNTHETIC",
                "question": "Synthetic " + cid,
                "municipalities": ["00000001"],
                "source_versions": [
                    {"source_url": url, "raw_sha256": self.body_hash, "acquisition_ags": "00000001"}
                ],
                "evidence": [
                    {
                        "unit_id": "u1",
                        "source_url": url,
                        "raw_sha256": self.body_hash,
                        "start_char": 0,
                        "end_char": len(text),
                        "quote": text,
                        "page_ref": None,
                    }
                ],
                "proposed_answer": "SYNTHETIC answer " + cid,
                "answerability": "PARTIAL",
                "annotation_status": "PROVISIONAL_AGENT_SOURCE_CHECKED",
                "review_priority": "HIGH",
                "rationale": "SYNTHETIC SOFTWARE TEST",
                "limitations": ["Not real annotation"],
            }
            for cid in ["SR-02", "WB-03", "WB-04"]
        ]
        self.candidates.write_bytes(
            canonical({"schema_version": "klima-review-candidates/1", "candidates": self.rows})
        )
        self.kw = {"corpus": self.corpus, "catalog": self.catalog, "raw_dir": self.raw}
        _, identity, _ = validate(self.candidates, **self.kw)
        records = [
            {
                "schema_version": "klima-human-decision/2",
                "reviewer_id": "SYNTHETIC_REVIEWER",
                "candidate_id": row["id"],
                "candidate_sha256": identity["candidate_sha256"][row["id"]],
                "decision": "ACCEPT",
                "acceptance_scope": "REFERENCE_ANSWER_TEXT_ONLY",
                "source_basis": {
                    "source_directly_opened": i == 0,
                    "material": "ARCHIVED_ORIGINAL_FILE"
                    if i == 0
                    else "CHAT_EDITORIAL_SOURCE_TEXT",
                    "archived_source_sha256": [self.body_hash],
                    "read_basis_verbatim": "SYNTHETIC",
                },
                "reasoning": "SYNTHETIC TEST ONLY",
                "reviewed_at": None,
                "reviewed_at_status": "NOT_RECORDED_BY_REVIEWER",
                "fully_blind_review": "NOT_ASSERTED",
            }
            for i, row in enumerate(self.rows)
        ]
        receipt = append_decisions(self.log, records, identity, "0" * 64)
        self.authority = {
            "schema_version": "klima-pilot-authorization/1",
            "release_id": "SYNTHETIC_RELEASE",
            "status": "PILOT_GOLD_APPROVED",
            "research_gold_approved": False,
            "m4_m5_authorized": False,
            "case_ids": [row["id"] for row in self.rows],
            "candidate_file_sha256": identity["candidate_file_sha256"],
            "catalog_sha256": identity["catalog_sha256"],
            "corpus_sha256": identity["corpus_sha256"],
            "human_log_sha256": digest(self.log.read_bytes()),
            "human_log_chain_tip": receipt["chain_tip"],
            "decision_source": {
                "kind": "EXPLICIT_PROJECT_OWNER_DECISION",
                "url": "https://fixture.invalid/decision",
            },
        }
        self.write_auth()

    def write_auth(self):
        self.auth.write_bytes(canonical(self.authority))

    def build(self):
        return build_manifest(self.candidates, self.log, self.auth, **self.kw)

    def test_repeat_freeze_is_byte_identical_and_preserves_sources_answers_history(self):
        before = {p: p.read_bytes() for p in [self.candidates, self.log, self.corpus, self.catalog]}
        a = freeze(self.candidates, self.log, self.auth, self.root / "a", **self.kw)
        b = freeze(self.candidates, self.log, self.auth, self.root / "b", **self.kw)
        self.assertEqual(a, b)
        self.assertEqual(
            (self.root / "a/manifest.json").read_bytes(),
            (self.root / "b/manifest.json").read_bytes(),
        )
        verify_release(
            self.root / "a/manifest.json",
            a["manifest_sha256"],
            self.candidates,
            self.log,
            self.auth,
            **self.kw,
        )
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))
        manifest = self.build()
        self.assertEqual(
            [r["reference_answer"] for r in manifest["cases"]],
            [r["proposed_answer"] for r in self.rows],
        )
        self.assertEqual(
            manifest["research_gold_status"], "RESEARCH_GOLD_PENDING_EXTERNAL_VALIDATION"
        )
        self.assertFalse(manifest["m4_m5_authorized"])

    def test_historical_partial_and_actual_source_basis_are_not_upgraded(self):
        manifest = self.build()
        self.assertEqual(
            sum(
                c["human_review"]["source_basis"]["source_directly_opened"]
                for c in manifest["cases"]
            ),
            1,
        )
        for case in manifest["cases"]:
            self.assertEqual(case["answerability_metadata"]["historical_answerability"], "PARTIAL")
            proposal = case["answerability_metadata"]["orthogonal_interpretation_proposal"]
            self.assertEqual(proposal["adjudication"], "PENDING_NOT_A_HUMAN_LABEL")
            self.assertIsNone(case["human_review"]["reviewed_at"])

    def test_changed_answer_or_source_refuses_new_manifest(self):
        self.rows[0]["proposed_answer"] += " UNAPPROVED"
        self.candidates.write_bytes(
            canonical({"schema_version": "klima-review-candidates/1", "candidates": self.rows})
        )
        with self.assertRaisesRegex(ValidationError, "owner-approved version"):
            self.build()
        (self.raw / self.body_hash).write_bytes(b"TAMPERED")
        with self.assertRaisesRegex(ValidationError, "hash"):
            self.build()

    def test_manifest_mutation_even_with_refreshed_file_hash_is_rejected(self):
        freeze(self.candidates, self.log, self.auth, self.root / "a", **self.kw)
        path = self.root / "a/manifest.json"
        value = json.loads(path.read_bytes())
        value["research_gold_status"] = "RESEARCH_GOLD_VALIDATED"
        path.write_bytes(canonical(value) + b"\n")
        with self.assertRaisesRegex(ValidationError, "content/version"):
            verify_release(
                path, digest(path.read_bytes()), self.candidates, self.log, self.auth, **self.kw
            )

    def test_authorization_cannot_broaden_scope_or_authorize_research_gold(self):
        for key, value in [
            ("research_gold_approved", True),
            ("m4_m5_authorized", True),
            ("case_ids", ["missing"]),
            ("human_log_sha256", "0" * 64),
            ("corpus_sha256", "0" * 64),
        ]:
            original = copy.deepcopy(self.authority)
            self.authority[key] = value
            self.write_auth()
            with self.assertRaises(ValidationError):
                self.build()
            self.authority = original

    def test_missing_ground_truth_metrics_fail_closed_despite_pilot_gold(self):
        manifest = self.build()
        for metric in [
            "retrieval_precision_recall",
            "extraction_field_precision_recall",
            "citation_semantic_correctness",
            "answer_factual_accuracy",
            "answerability_accuracy",
        ]:
            with self.assertRaisesRegex(ValidationError, "unavailable"):
                require_pilot_metric(manifest, metric)
        self.assertEqual(
            require_pilot_metric(manifest, "answer_exact_text_match")["scope"],
            "LITERAL_REFERENCE_ONLY",
        )
        with self.assertRaisesRegex(ValidationError, "Unknown"):
            require_pilot_metric(manifest, "invented_metric")

    def test_connected_source_questions_cannot_cross_random_splits(self):
        assignments = {row["id"]: "DEVELOPMENT_EXPOSED" for row in self.rows}
        assignments["WB-04"] = "TEST"
        with self.assertRaisesRegex(ValidationError, "Leakage"):
            validate_split(self.rows, assignments)

    def test_existing_release_directory_is_never_overwritten(self):
        out = self.root / "existing"
        out.mkdir()
        with self.assertRaisesRegex(ValidationError, "fresh"):
            freeze(self.candidates, self.log, self.auth, out, **self.kw)

    def test_approval_flag_cannot_replace_missing_metric_ground_truth(self):
        manifest = self.build()
        del manifest["cases"][0]["reference_answer"]
        with self.assertRaisesRegex(ValidationError, "intact reference-answer"):
            require_pilot_metric(manifest, "answer_exact_text_match")
        manifest = self.build()
        manifest["cases"][0]["evidence_spans"] = []
        with self.assertRaisesRegex(ValidationError, "evidence anchors"):
            require_pilot_metric(manifest, "citation_locator_integrity")


if __name__ == "__main__":
    unittest.main()
