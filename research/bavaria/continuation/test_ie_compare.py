"""Regression gates for targeted extraction evaluation and conservative fusion."""

import unittest

from .ie_compare import empty, hybrid, normalize, numeric_support, structurally_supported, summary


class IEComparisonTests(unittest.TestCase):
    def test_number_roles_do_not_confuse_source_value(self):
        self.assertTrue(numeric_support("13600000", "13,6 Millionen Euro"))
        self.assertTrue(numeric_support("1113.09", "1.113,09 Euro"))
        self.assertFalse(numeric_support("136", "13,6 Millionen Euro"))
        self.assertFalse(numeric_support("112024", "01.11.2024"))
        self.assertEqual(normalize("amount", "100.00"), normalize("amount", "100"))

    def test_unanchored_model_field_rejected(self):
        model, rules = empty(), empty()
        model["actor"] = "Falsche Gemeinde"
        model["evidence"]["actor"] = "Die Gemeinde Beispiel"
        text = "Die Gemeinde Beispiel plant eine Studie."
        self.assertFalse(structurally_supported("actor", model, text))
        combined, rejected = hybrid(model, rules, {"text": text})
        self.assertIsNone(combined["actor"])
        self.assertIn("actor", rejected)

    def test_conflicting_supported_values_abstain(self):
        model, rules = empty(), empty()
        model.update(amount="100", evidence={"amount": "100 Euro"})
        rules.update(amount="200", evidence={"amount": "200 Euro"})
        combined, rejected = hybrid(model, rules, {"text": "100 Euro und 200 Euro."})
        self.assertIsNone(combined["amount"])
        self.assertIn("amount:conflict_abstention", rejected)

    def test_wrong_value_is_fp_and_fn_unknown_not_positive(self):
        reference, prediction = empty(), empty()
        reference["amount"] = "100"
        prediction["amount"] = "200"
        result = summary([{"reference": reference, "output": prediction}])
        self.assertEqual(result["micro"]["fp"], 1)
        self.assertEqual(result["micro"]["fn"], 1)
        self.assertEqual(result["micro"]["positive_slots"], 1)
        self.assertEqual(result["micro"]["unknown_slots"], 6)


def test_extraction_cannot_call_remote_or_credentialed_provider():
    import pytest

    from .ie_compare import validate_local_endpoint

    for endpoint in (
        "https://provider.example",
        "http://provider.example",
        "http://secret@localhost:11435",
        "http://localhost:11435?token=secret",
    ):
        with pytest.raises(ValueError, match="local HTTP"):
            validate_local_endpoint(endpoint)
    validate_local_endpoint("http://127.0.0.1:11435")
