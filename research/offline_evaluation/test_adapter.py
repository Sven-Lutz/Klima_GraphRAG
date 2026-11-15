"""TECHNICAL TESTS ONLY: invented sources, rankings and judgments; no research results."""
from __future__ import annotations

import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .__main__ import execute, main
from .contracts import Inputs, ValidationError, canonical, digest
from .evaluate import Evaluation
from .synthetic import SyntheticPackage


class AdapterTechnicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = SyntheticPackage()
        self.network = patch.object(socket, "socket", side_effect=AssertionError("NETWORK FORBIDDEN"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def evaluate(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        self.evaluation = Evaluation(Inputs(path, checksum))
        return self.evaluation.compute()

    def metric(self, report, mid, oid="o1", **filters):
        return next(r for r in report["rows"] if r["metric_id"] == mid and r["output_id"] == oid
                    and all(r.get(k) == v for k, v in filters.items()))

    def test_01_empty_reference_is_diagnostic_without_quality(self):
        self.fixture.documents["references"] = {}
        report = self.evaluate()
        self.assertEqual(report["status"], "DIAGNOSTIC")
        self.assertTrue(all(r["value"] is None and r["NA_reason"] for r in report["rows"]
                            if r["metric_id"] in {f"M{i:02}" for i in range(3, 15)}))

    def test_01_unadjudicated_release_is_diagnostic(self):
        self.fixture.documents["references"]["release"]["review"] = {"status": "IN_REVIEW"}
        self.assertEqual(self.evaluate()["status"], "DIAGNOSTIC")

    def test_01_checkbox_without_bound_audit_is_not_release(self):
        self.fixture.documents["references"]["release"]["review"] = {"status": "ADJUDICATED"}
        self.assertEqual(self.evaluate()["status"], "DIAGNOSTIC")

    def test_02_unknown_grade_is_not_zero(self):
        grade = self.fixture.documents["references"]["relevance"][0]
        grade["grade"] = None
        grade["review"] = {"status": "NOT_ASSESSED"}
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M02")["value"], 2 / 3)
        self.assertIsNone(self.metric(report, "M03")["value"])

    def test_02_explicit_zero_remains_judged(self):
        self.fixture.documents["references"]["relevance"][0]["grade"] = 0
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M02")["value"], 1)
        self.assertIsNotNone(self.metric(report, "M03")["value"])

    def test_03_invalid_sha(self):
        self.fixture.documents["inventory"]["sources"][0]["body_sha256"] = "wrong"
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            self.evaluate()

    def test_03_offset_is_unicode_interval_not_raw_body(self):
        self.fixture.documents["inventory"]["locators"][0]["end"] = 999
        with self.assertRaisesRegex(ValidationError, "offset"):
            self.evaluate()

    def test_03_unknown_unit(self):
        self.fixture.documents["outputs"][0]["ranked"][0]["unit_id"] = "unknown"
        with self.assertRaisesRegex(ValidationError, "Unknown E2"):
            self.evaluate()

    def snapshot(self):
        inv = self.fixture.documents["inventory"]
        unit, source = inv["units"][0], inv["sources"][0]
        mapping = {"database_snapshot_id": "snap1", "evidence_id": "ev1", "source_type": "segment",
                   "source_id": "db-source-9", "segment_id": "seg1", "document_id": "doc1",
                   "unit_id": "u1", "start": 0, "end": len(unit["text"]),
                   "body_sha256": source["body_sha256"], "url": source["url"], "evidence_text": unit["text"]}
        mapping["snapshot_export_asset"] = "original_snapshot"
        original = {k: mapping[k] for k in ("database_snapshot_id", "evidence_id", "source_type",
                                            "source_id", "segment_id", "evidence_text")}
        original["document"] = {"document_id": "doc1", "source_url": source["url"]}
        self.fixture.assets["original_snapshot"] = canonical(original)
        inv["snapshot_mappings"] = [mapping]
        view = {"kind": "SNAPSHOT", **{k: mapping[k] for k in (
            "database_snapshot_id", "evidence_id", "source_type", "source_id", "segment_id",
            "document_id", "evidence_text")}}
        self.fixture.documents["outputs"][0]["ranked"][0] = view
        self.fixture.documents["outputs"][0]["selected"][0]["view"] = copy.deepcopy(view)
        return mapping

    def test_03_explicit_snapshot_mapping_preserves_original(self):
        self.snapshot()
        report = self.evaluate()
        self.assertEqual(report["resolved_selections"]["o1"]["ranges"][0], ("u1", 0, 22))
        self.assertEqual(self.metric(report, "M04")["value"], 1)

    def test_03_ambiguous_snapshot_mapping_aborts(self):
        mapping = self.snapshot()
        self.fixture.documents["inventory"]["snapshot_mappings"].append(copy.deepcopy(mapping))
        with self.assertRaisesRegex(ValidationError, "Ambiguous"):
            self.evaluate()

    def test_03_no_fuzzy_snapshot_fallback(self):
        self.snapshot()
        self.fixture.documents["inventory"]["snapshot_mappings"] = []
        with self.assertRaisesRegex(ValidationError, "No explicit"):
            self.evaluate()

    def test_03_wrong_snapshot_body_rejected(self):
        mapping = self.snapshot()
        mapping["body_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValidationError, "source-version"):
            self.evaluate()

    def test_03_context_truncation_cannot_claim_full_locator(self):
        f = self.fixture
        f.strip_answers()
        selected = f.documents["outputs"][0]["selected"][0]
        selected["view"]["end"] = 10
        selected["serialized_end"] = selected["serialized_start"] + 10
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M04")["value"], 0.5)

    def test_03_claim_cannot_cite_undelivered_locator(self):
        self.fixture.select([1])
        with self.assertRaisesRegex(ValidationError, "not present"):
            self.evaluate()

    def test_04_shared_source_requires_disclosure(self):
        task = copy.deepcopy(self.fixture.documents["tasks"]["tasks"][0])
        task.update(task_id="t2", case_id="case2", municipality_id="00000002", split="DEVELOPMENT")
        self.fixture.documents["tasks"]["tasks"].append(task)
        with self.assertRaisesRegex(ValidationError, "split disclosure"):
            self.evaluate()

    def test_04_disclosed_shared_sources_are_restricted(self):
        task = copy.deepcopy(self.fixture.documents["tasks"]["tasks"][0])
        task.update(task_id="t2", case_id="case2", municipality_id="00000002", split="DEVELOPMENT")
        self.fixture.documents["tasks"]["tasks"].append(task)
        self.fixture.manifest["split_disclosures"] = [{"case_ids": ["case1", "case2"],
            "reason": "TECHNICAL shared source exception", "analysis_cluster": "shared-family"}]
        report = self.evaluate()
        self.assertEqual(report["status"], "SCORABLE_RESTRICTED")
        self.assertTrue(report["split_disclosures"])
        self.assertEqual(report["excluded_tasks"], ["t2"])

    def test_05_partial_conjunction_is_not_a_complete_bundle(self):
        self.fixture.select([1])
        self.fixture.strip_answers()
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M04")["value"], 0)

    def test_05_alternative_bundle_suffices(self):
        self.fixture.select([3])
        self.fixture.strip_answers()
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M04")["numerator"], 1)
        self.assertEqual(self.metric(report, "M04")["denominator"], 2)

    def test_05_unreviewed_bundle_is_na_not_miss(self):
        self.fixture.documents["references"]["bundles"][0]["review"] = {"status": "NOT_ASSESSED"}
        self.assertIsNone(self.metric(self.evaluate(), "M04")["value"])

    def test_06_restricted_release_only_allowed_metrics(self):
        refs = self.fixture.documents["references"]
        refs["release"].update(state="CLOSED_RESTRICTED", allowed_metrics=["M04"])
        refs["facets"][1]["review"] = {"status": "NOT_ASSESSED"}
        report = self.evaluate()
        self.assertEqual(report["status"], "SCORABLE_RESTRICTED")
        self.assertEqual(report["missing_aspect_judgments"], {"t1": ["next"]})
        self.assertEqual(self.metric(report, "M04")["denominator"], 1)
        self.assertEqual(self.metric(report, "M05")["NA_reason"], "METRIC_NOT_RELEASED_IN_SCOPE")

    def test_07_negative_reference_allows_nonanswer_not_retrieval_coverage(self):
        f = self.fixture
        refs = f.documents["references"]
        refs["tasks"][0]["answerability"] = "UNSUPPORTED_IN_SCOPE"
        for facet in refs["facets"]:
            facet.update(finding="NO_EVIDENCE_IN_SCOPE", bundle_ids=[])
        refs["bundles"] = []
        for rel in refs["relevance"]:
            rel["grade"] = 0
        f.select([])
        f.documents["answer_reviews"][0].update(claims=[], facets=[])
        f.documents["answer_reviews"][0]["behavior"].update(properly_qualified_nonanswer=True,
                                                           invented_answer=False, only_refusal=True)
        report = self.evaluate()
        self.assertTrue(report["fully_negative_reference"])
        self.assertEqual(self.metric(report, "M10")["value"], 1)
        for mid in ["M03", "M04", "M05", "M06", "M07"]:
            self.assertIsNone(self.metric(report, mid)["value"], mid)
        self.assertTrue(report["no_architecture_winner_inferred"])

    def test_08_valid_locator_does_not_validate_wrong_qualifier(self):
        for field in ("municipality", "time", "institution"):
            self.fixture.documents["answer_reviews"][0]["claims"][0]["qualifiers"][field]["validity"] = "INVALID"
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M01")["value"], 1)
        self.assertEqual(self.metric(report, "M06")["value"], 1)
        self.assertEqual(self.metric(report, "M05")["value"], 0.5)
        self.assertEqual(self.metric(report, "M08")["value"], 0.5)

    def test_08_unreviewed_qualifier_is_na(self):
        self.fixture.documents["answer_reviews"][0]["claims"][0]["qualifiers"]["time"]["review"] = {"status": "IN_REVIEW"}
        report = self.evaluate()
        self.assertIsNone(self.metric(report, "M08")["value"])
        self.assertIsNone(self.metric(report, "M05")["value"])

    def test_09_tokens_use_serialization_not_character_count(self):
        report = self.evaluate()
        value = self.metric(report, "M15", measure="serialized_context_tokens")["value"]
        self.assertLess(value, len(self.fixture.assets["context"].decode()))
        self.assertGreater(value, 0)

    def extra_call(self):
        row = copy.deepcopy(self.fixture.documents["resources"][0])
        row.update(invocation_id="call2", operation="QUERY_EXPANSION", input_asset="expansion_in", output_asset="expansion_out")
        self.fixture.assets["expansion_in"] = b"Extra input"
        self.fixture.assets["expansion_out"] = b"Extra output"
        self.fixture.documents["resources"].append(row)
        self.fixture.documents["outputs"][0]["invocation_ids"].append("call2")

    def test_09_extra_calls_count_towards_total_budget(self):
        self.extra_call()
        self.fixture.manifest["conditions"]["limits"]["total_input_tokens"] = 1
        with self.assertRaisesRegex(ValidationError, "All-call input budget"):
            self.evaluate()

    def test_09_unassigned_extra_call_rejected(self):
        self.extra_call()
        self.fixture.documents["outputs"][0]["invocation_ids"].pop()
        with self.assertRaisesRegex(ValidationError, "Unassigned resource"):
            self.evaluate()

    def test_09_recorded_tokens_must_match_tokenizer(self):
        self.fixture.documents["resources"][0]["input_tokens"] = 0
        with self.assertRaisesRegex(ValidationError, "Recorded tokens"):
            self.evaluate()

    def test_09_missing_serialized_prompt_is_not_measured(self):
        self.fixture.documents["resources"][0]["input_asset"] = None
        report = self.evaluate()
        self.assertIsNone(self.metric(report, "M04")["value"])
        self.assertIsNone(self.metric(report, "M15", measure="input_tokens")["value"])

    def test_10_post_freeze_asset_mutation_rejected(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        (path.parent / "raw.fixture").write_bytes(b"mutated")
        with self.assertRaisesRegex(ValidationError, "Frozen asset changed"):
            execute(path, checksum, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_10_manifest_mutation_rejected(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValidationError, "Manifest changed"):
            execute(path, checksum, self.root / "out")

    def test_10_no_overwrite_and_complete_marker(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        out = self.root / "out"
        execute(path, checksum, out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        self.assertEqual((out / "COMPLETE").read_text(), "TECHNICAL_TEST\n")
        with self.assertRaisesRegex(ValidationError, "already exists"):
            execute(path, checksum, out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})

    def test_11_posthoc_e2_cannot_be_prospective(self):
        self.fixture.manifest.update(purpose="RESEARCH_EVALUATION", study_origin="POSTHOC_E2", claim_prospective=True)
        with self.assertRaisesRegex(ValidationError, "prospectivity"):
            self.evaluate()

    def test_11_posthoc_e2_remains_diagnostic(self):
        self.fixture.manifest.update(purpose="RESEARCH_DIAGNOSTIC", study_origin="POSTHOC_E2")
        self.fixture.manifest["conditions"]["tokenizer"]["algorithm"] = "UNAVAILABLE_HISTORICAL_TOKENIZER"
        self.fixture.documents["tasks"]["tasks"][0]["known_exposure"] = "E2_POSTHOC"
        report = self.evaluate()
        self.assertEqual(report["status"], "DIAGNOSTIC")
        self.assertFalse(report["prospectivity_certified"])
        self.assertIsNone(self.metric(report, "M04")["value"])

    def test_11_draft_task_cards_rejected(self):
        self.fixture.documents["tasks"]["status"] = "DESIGN_DRAFT_NOT_REFERENCE"
        with self.assertRaisesRegex(ValidationError, "DRAFT"):
            self.evaluate()

    def test_12_all_tests_forbid_network_and_remain_technical(self):
        with self.assertRaisesRegex(AssertionError, "NETWORK FORBIDDEN"):
            socket.socket()
        report = self.evaluate()
        self.assertEqual(report["purpose"], "TECHNICAL_TEST")
        self.assertFalse(report["scientific_truth_or_blinding_verified"])

    def test_metrics_perfect_synthetic_contract(self):
        report = self.evaluate()
        self.assertEqual(report["status"], "SCORABLE_COMPLETE")
        for mid in ("M01", "M02", "M03", "M04", "M05", "M06", "M08"):
            self.assertEqual(self.metric(report, mid)["value"], 1, mid)
        for mid in ("M07", "M11"):
            self.assertEqual(self.metric(report, mid)["value"], 0, mid)
        self.assertEqual({r["metric_id"] for r in report["rows"]}, {f"M{i:02}" for i in range(1, 17)})

    def test_partial_claim_support_separate_from_unsupported(self):
        self.fixture.documents["answer_reviews"][0]["claims"][0]["support"] = "PARTIAL"
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M06")["value"], 0.5)
        self.assertEqual(self.metric(report, "M07")["value"], 0)
        self.assertEqual(self.metric(report, "M07")["partial_claim_count"], 1)

    def test_unreviewed_claim_not_unsupported(self):
        self.fixture.documents["answer_reviews"][0]["claims"][0]["review"] = {"status": "NOT_ASSESSED"}
        report = self.evaluate()
        for mid in ("M05", "M06", "M07"):
            self.assertIsNone(self.metric(report, mid)["value"], mid)

    def test_gap_conflict_and_intent_metrics_need_separate_reviews(self):
        f = self.fixture
        refs = f.documents["references"]
        refs["gaps"] = [f.reviewed({"task_id": "t1", "gap_id": "g1", "description": "fictional gap"})]
        refs["conflicts"] = [f.reviewed({"task_id": "t1", "conflict_id": "x1", "description": "fictional conflict",
                "case_kind": "CONFLICT", "time_relation": "same fictional period",
                "reviewed_conclusion": "conflict unresolved in fixture", "locator_ids": ["l1", "l2"]})]
        refs["intents"] = [f.reviewed({"task_id": "t1", "information_need": f.documents["tasks"]["tasks"][0]["information_need"],
                "recorded_at": "1999-01-01T00:00:00+00:00"})]
        review = f.documents["answer_reviews"][0]
        review["gaps"] = [f.reviewed({"gap_id": "g1", "correct": True})]
        review["conflicts"] = [f.reviewed({"conflict_id": "x1", "correct": False})]
        review["intent"] = f.reviewed({"preserved": True})
        f.documents["outputs"][0]["experiment"] = "V2"
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M09")["value"], 1)
        self.assertEqual(self.metric(report, "M12")["value"], 0)
        self.assertEqual(self.metric(report, "M13")["value"], 1)

    def test_national_frame_and_stages_are_distinct(self):
        f = self.fixture
        f.documents["frame"] = {"frame_date": "2000-01-01", "identity_rules": "FICTIONAL stable IDs",
                                "municipality_ids": ["00000001", "00000002"]}
        f.documents["references"]["national"] = f.reviewed({"frame_asset": "frame", "stages": {
            "IDENTIFIED": ["00000001", "00000002"], "ATTEMPTED": ["00000001"],
            "SOURCE_AVAILABLE": [], "TASK_REVIEWED": None}})
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M16", national_stage="IDENTIFIED")["value"], 1)
        self.assertEqual(self.metric(report, "M16", national_stage="SOURCE_AVAILABLE")["value"], 0)
        self.assertIsNone(self.metric(report, "M16", national_stage="TASK_REVIEWED")["value"])

    def test_audit_record_mismatch_rejected(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        inputs = Inputs(path, checksum)
        asset = self.fixture.documents["references"]["release"]["review"]["audit_asset"]
        audit = json.loads(inputs.assets[asset])
        audit["object_sha256"] = "a" * 64
        inputs.assets[asset] = canonical(audit)
        with self.assertRaisesRegex(ValidationError, "Audit/record"):
            Evaluation(inputs)

    def test_no_judged_only_reranking(self):
        f = self.fixture
        f.documents["outputs"][0]["ranked"].reverse()
        report = self.evaluate()
        self.assertLess(self.metric(report, "M03")["value"], 1)

    def test_duplicate_ranked_canonical_unit_rejected(self):
        self.fixture.documents["outputs"][0]["ranked"].append(self.fixture.view(1))
        with self.assertRaisesRegex(ValidationError, "Duplicate canonical"):
            self.evaluate()

    def test_real_research_cannot_use_synthetic_token_counts(self):
        self.fixture.manifest.update(purpose="RESEARCH_EVALUATION", study_origin="NEW_PROTOCOL")
        with self.assertRaisesRegex(ValidationError, "test-only"):
            self.evaluate()

    def test_missing_variant_output_remains_visible(self):
        self.fixture.manifest["expected_outputs"] = [
            {"task_id": "t1", "variant_id": "LEX", "experiment": "V1"},
            {"task_id": "t1", "variant_id": "SEM", "experiment": "V1"}]
        report = self.evaluate()
        self.assertEqual(report["status"], "SCORABLE_RESTRICTED")
        rows = [r for r in report["rows"] if r["variant_id"] == "SEM"]
        self.assertEqual(len(rows), 16)
        self.assertTrue(all(r["value"] is None and r["NA_reason"] == "MISSING_OUTPUT" for r in rows))

    def test_extra_call_totals_are_not_just_final_answer(self):
        self.extra_call()
        report = self.evaluate()
        total = self.metric(report, "M15", measure="all_request_and_acquisition_input_tokens")["value"]
        answer = self.metric(report, "M15", measure="input_tokens", invocation_id="call1")["value"]
        self.assertGreater(total, answer)

    def test_10_dangling_output_symlink_is_not_reused(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        output = self.root / "symlink"
        output.symlink_to(self.root / "not-created")
        with self.assertRaisesRegex(ValidationError, "symlink"):
            execute(path, checksum, output)
        self.assertFalse((self.root / "not-created").exists())

    def test_mutation_during_evaluation_prevents_publish(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        original = Evaluation.compute
        def mutate(evaluation):
            result = original(evaluation)
            (path.parent / "raw.fixture").write_bytes(b"changed during run")
            return result
        with patch.object(Evaluation, "compute", mutate):
            with self.assertRaisesRegex(ValidationError, "mutated during"):
                execute(path, checksum, self.root / "result")
        self.assertFalse((self.root / "result").exists())

    def test_unavailable_tokenizer_cannot_enable_quality_scores(self):
        self.fixture.manifest["conditions"]["tokenizer"]["algorithm"] = "unavailable"
        report = self.evaluate()
        self.assertIsNone(self.metric(report, "M04")["value"])
        self.assertIsNone(self.metric(report, "M15", measure="serialized_context_tokens")["value"])
        self.assertEqual(report["status"], "SCORABLE_RESTRICTED")

    def test_frozen_raw_source_hash_does_not_validate_changed_original_text(self):
        row = json.loads(self.fixture.assets["original_corpus"].splitlines()[0])
        row["text"] = "changed original export"
        self.fixture.assets["original_corpus"] = canonical(row) + b"\n" + b"\n".join(
            self.fixture.assets["original_corpus"].splitlines()[1:])
        with self.assertRaisesRegex(ValidationError, "Canonical export"):
            self.evaluate()

    def test_build_usage_is_separate_from_request_totals(self):
        f = self.fixture
        build = copy.deepcopy(f.documents["resources"][0])
        build.update(invocation_id="build1", stage="BUILD", operation="INDEX_BUILD",
                     input_asset=None, output_asset=None, elapsed_ms=100, storage_bytes=4096)
        f.documents["resources"].append(build)
        f.documents["outputs"][0]["invocation_ids"].append("build1")
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M04")["value"], 1)
        self.assertEqual(self.metric(report, "M15", measure="storage_bytes", invocation_id="build1")["value"], 4096)
        self.assertEqual(self.metric(report, "M15", measure="all_request_and_acquisition_input_tokens")["value"],
                         self.metric(report, "M15", measure="input_tokens", invocation_id="call1")["value"])

    def test_reviewed_refusal_on_full_answerable_task_is_visible(self):
        self.fixture.documents["answer_reviews"][0]["behavior"]["only_refusal"] = True
        self.assertEqual(self.metric(self.evaluate(), "M11")["value"], 1)

    def test_unresolved_second_review_prevents_release(self):
        self.fixture.documents["references"]["second_reviews"][0]["agreement"] = "UNRESOLVED"
        self.assertEqual(self.evaluate()["status"], "DIAGNOSTIC")

    def test_incomplete_claim_inventory_has_unknown_denominator(self):
        self.fixture.documents["answer_reviews"][0]["claim_inventory_complete"] = False
        report = self.evaluate()
        for mid in ("M06", "M07", "M08"):
            row = self.metric(report, mid)
            self.assertIsNone(row["value"])
            self.assertIsNone(row["denominator"])

    def test_string_false_cannot_complete_reference_scope(self):
        self.fixture.documents["references"]["tasks"][0]["scope_complete"] = "false"
        with self.assertRaisesRegex(ValidationError, "must be a boolean"):
            self.evaluate()

    def test_original_parser_export_must_match(self):
        self.fixture.documents["inventory"]["units"][0]["text"] = "Altered canonical text"
        with self.assertRaisesRegex(ValidationError, "Canonical export"):
            self.evaluate()

    def test_missing_second_review_keeps_diagnostic(self):
        self.fixture.documents["references"]["second_reviews"] = []
        self.assertEqual(self.evaluate()["status"], "DIAGNOSTIC")

    def test_same_reviewer_cannot_be_independent_second_review(self):
        second = self.fixture.documents["references"]["second_reviews"][0]
        self.fixture.audit_assets[second["review"]["audit_asset"]]["reviewer_id"] = "FICTIONAL_SOFTWARE_TEST_REVIEWER"
        self.assertEqual(self.evaluate()["status"], "DIAGNOSTIC")

    def test_empty_context_is_not_proof_of_nonanswerability(self):
        self.fixture.select([])
        self.fixture.strip_answers()
        report = self.evaluate()
        self.assertTrue(self.metric(report, "M02")["empty_context"])
        self.assertEqual(self.metric(report, "M04")["value"], 0)
        self.assertIsNone(self.metric(report, "M10")["value"])

    def test_v3_negative_increment_is_reported_not_clamped(self):
        f = self.fixture
        before = f.documents["outputs"][0]
        before["experiment"] = "V3_BEFORE"
        after = copy.deepcopy(before)
        after.update(output_id="o2", experiment="V3_AFTER", before_output_id="o1",
                     invocation_ids=["answer2", "acquisition2"], acquisition_budget=2)
        f.documents["outputs"].append(after)
        answer_review = copy.deepcopy(f.documents["answer_reviews"][0])
        # Rebind each copied review document rather than reusing the first output's audit IDs.
        def rebind(value):
            if isinstance(value, list):
                return [rebind(v) for v in value]
            if isinstance(value, dict):
                obj = {k: rebind(v) for k, v in value.items() if k != "review"}
                return f.reviewed(obj) if "review" in value else obj
            return value
        answer_review = rebind(answer_review)
        answer_review["output_id"] = "o2"
        answer_review["facets"][0]["verdict"] = "INCORRECT"
        f.documents["answer_reviews"].append(answer_review)
        answer_call = copy.deepcopy(f.documents["resources"][0])
        answer_call.update(invocation_id="answer2", output_id="o2")
        acquisition = copy.deepcopy(answer_call)
        acquisition.update(invocation_id="acquisition2", stage="ACQUISITION", operation="OFFLINE_REPLAY",
                           input_asset="empty", output_asset="empty", responses=1)
        f.assets["empty"] = b""
        f.documents["resources"].extend([answer_call, acquisition])
        report = self.evaluate()
        self.assertEqual(self.metric(report, "M14", oid="o2")["value"], -1)

    def test_unchanged_inputs_and_cli_exit_status(self):
        path, checksum = self.fixture.freeze(self.root / "input")
        before = {p.name: digest(p.read_bytes()) for p in path.parent.iterdir()}
        self.assertEqual(main(["--manifest", str(path), "--manifest-sha256", checksum,
                               "--out", str(self.root / "result")]), 0)
        self.assertEqual(before, {p.name: digest(p.read_bytes()) for p in path.parent.iterdir()})


if __name__ == "__main__":
    unittest.main()
