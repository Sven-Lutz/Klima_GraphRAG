"""Synthetic dependency/semantic contracts; no human adjudication is performed."""

import copy
import unittest

from research.offline_evaluation.contracts import ValidationError

from .release_gates import dependency_groups, semantics_proposal, validate_split


def fixture(cid, sources, municipalities):
    return {
        "id": cid,
        "source_versions": [
            {
                "raw_sha256": source,
                "source_url": "https://fixture.invalid/" + source,
                "acquisition_ags": municipalities[0],
            }
            for source in sources
        ],
        "municipalities": municipalities,
        "evidence": [],
        "answerability": "PARTIAL",
    }


class GateTests(unittest.TestCase):
    def test_cross_source_bridge_is_transitive_and_prevents_split_leakage(self):
        rows = [
            fixture("A", ["s1"], ["00000001"]),
            fixture("B", ["s2"], ["00000002"]),
            fixture("C", ["s1", "s2"], ["00000001", "00000002"]),
            fixture("D", ["s3"], ["00000002"]),
        ]
        self.assertEqual(dependency_groups(rows, False), [["A", "B", "C"], ["D"]])
        self.assertEqual(dependency_groups(rows, True), [["A", "B", "C", "D"]])
        with self.assertRaisesRegex(ValidationError, "Leakage"):
            validate_split(
                rows,
                {"A": "DEVELOPMENT_EXPOSED", "B": "TEST", "C": "DEVELOPMENT_EXPOSED", "D": "TEST"},
            )
        validate_split(rows, {row["id"]: "DEVELOPMENT_EXPOSED" for row in rows})

    def test_missing_link_and_incomplete_split_rejected(self):
        rows = [fixture("A", ["s1"], ["00000001"])]
        with self.assertRaisesRegex(ValidationError, "Incomplete split"):
            validate_split(rows, {})
        rows[0]["linked_candidates"] = ["missing"]
        with self.assertRaisesRegex(ValidationError, "Missing linked"):
            dependency_groups(rows, False)

    def test_method_proposal_preserves_human_reviewed_candidate_versions(self):
        rows = [fixture(cid, [cid], ["00000001"]) for cid in ["SR-02", "WB-03", "WB-04"]]
        before = copy.deepcopy(rows)
        proposal = semantics_proposal(rows, {row["id"]: "fixture-hash" for row in rows})
        self.assertEqual(rows, before)
        self.assertFalse(proposal["changes_to_frozen_candidates"])
        cases = {row["candidate_id"]: row for row in proposal["cases"]}
        self.assertEqual(cases["SR-02"]["question_response_status"], "ANSWERED")
        self.assertEqual(cases["WB-04"]["question_response_status"], "PARTIALLY_ANSWERED")
        for row in cases.values():
            self.assertEqual(row["real_world_status"], "UNKNOWN")
            self.assertEqual(row["claim_support_in_specified_source"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(row["adjudication"], "PENDING_NOT_A_HUMAN_LABEL")


if __name__ == "__main__":
    unittest.main()
