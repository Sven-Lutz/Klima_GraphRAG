from __future__ import annotations
import unittest
from crawler.core.policy_decision import (
    PolicyQuestion, PolicyEvidence, EvidenceValidationError, decide, decide_dict
)

AGS = "09462000"  # Identifier only, synthetic scenarios (NOT real Bayreuth policy facts).
OTHER = "09362000"
POLICY = "balkon_pv_municipal_grant"


def question(**changes):
    data = dict(question_id="q1", municipality_id=AGS, policy_id=POLICY, as_of="2026-11-03")
    data.update(changes)
    return PolicyQuestion(**data)


def evidence(**changes):
    base = dict(evidence_id="e1", municipality_id=AGS, policy_id=POLICY,
                issuer_ags=AGS, authority="municipality", claim="offers",
                source_url="https://example.org/foerderung",
                document_text="Die Stadt bietet einen Zuschuss für Steckersolargeräte.",
                quote="bietet einen Zuschuss für Steckersolargeräte",
                published_at="2026-10-01", verified=True)
    base.update(changes)
    return PolicyEvidence(**base)


class DecisionTests(unittest.TestCase):
    def test_verified_yes(self):
        d = decide(question(), [evidence()])
        self.assertEqual(d.label, "yes")
        self.assertEqual(d.evidence_ids, ("e1",))
        self.assertTrue(d.citations[0]["url"].startswith("https://"))

    def test_verified_no_only_for_explicit_denial(self):
        self.assertEqual(decide(question(), [evidence(claim="explicitly_not_offered")]).label, "no")

    def test_verified_end_statement_no(self):
        self.assertEqual(decide(question(), [evidence(claim="ended")]).label, "no")

    def test_missing_evidence_never_no(self):
        self.assertEqual(decide(question(), []).label, "unknown")

    def test_unverified_never_yes(self):
        self.assertEqual(decide(question(), [evidence(verified=False)]).label, "unknown")

    def test_missing_date_abstains(self):
        self.assertEqual(decide(question(), [evidence(published_at=None)]).label, "unknown")

    def test_future_document_abstains(self):
        self.assertEqual(decide(question(), [evidence(published_at="2026-12-01")]).label, "unknown")

    def test_future_effective_date_is_planned(self):
        d=decide(question(), [evidence(effective_from="2027-01-01")])
        self.assertEqual(d.label,"unknown")
        self.assertIn("planned",d.reason)

    def test_planned_policy_is_not_yes(self):
        self.assertEqual(decide(question(), [evidence(claim="planned")]).label, "unknown")

    def test_expired_offer_is_not_yes_or_no(self):
        self.assertEqual(decide(question(), [evidence(effective_until="2026-09-30")]).label, "unknown")

    def test_stale_without_validity_abstains(self):
        self.assertEqual(decide(question(), [evidence(published_at="2022-10-01")]).label, "unknown")

    def test_document_with_explicit_current_validity(self):
        self.assertEqual(decide(question(), [evidence(published_at="2021-01-01", effective_until="2027-01-01")]).label, "yes")

    def test_wrong_city_never_yes(self):
        d=decide(question(), [evidence(municipality_id=OTHER)])
        self.assertEqual(d.label, "unknown")
        self.assertEqual(d.excluded_evidence_ids, ("e1",))

    def test_wrong_policy_never_yes(self):
        self.assertEqual(decide(question(), [evidence(policy_id="electric_bus_pilot")]).label, "unknown")

    def test_district_source_does_not_count(self):
        self.assertEqual(decide(question(), [evidence(authority="district", issuer_ags=OTHER)]).label,"unknown")

    def test_utility_offer_does_not_count_as_municipal(self):
        self.assertEqual(decide(question(), [evidence(authority="municipal_utility", issuer_ags=AGS)]).label,"unknown")

    def test_issuer_mismatch_does_not_count(self):
        self.assertEqual(decide(question(), [evidence(issuer_ags=OTHER)]).label, "unknown")

    def test_conflicting_official_evidence_flagged(self):
        d=decide(question(), [evidence(), evidence(evidence_id="e2", claim="explicitly_not_offered")])
        self.assertEqual(d.label,"conflict")
        self.assertEqual(len(d.citations),2)

    def test_mentions_are_not_policies(self):
        self.assertEqual(decide(question(), [evidence(claim="mentions")]).label,"unknown")

    def test_quote_must_be_source_anchored(self):
        with self.assertRaises(EvidenceValidationError):
            evidence(quote="approved funding of 100000 EUR")

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(EvidenceValidationError):
            decide(question(),[evidence(), evidence()])

    def test_invalid_date_rejected(self):
        with self.assertRaises(EvidenceValidationError):
            evidence(published_at="yesterday")

    def test_bad_ags_rejected(self):
        with self.assertRaises(EvidenceValidationError):
            question(municipality_id="Bayreuth")

    def test_bad_url_rejected(self):
        with self.assertRaises(EvidenceValidationError):
            evidence(source_url="javascript:alert(1)")

    def test_json_adapter(self):
        d=decide_dict({"question": {"question_id":"q1", "municipality_id": AGS,"policy_id": POLICY,"as_of":"2026-11-03"},
                       "evidence": []})
        self.assertEqual(d["label"],"unknown")


if __name__ == '__main__':
    unittest.main()
