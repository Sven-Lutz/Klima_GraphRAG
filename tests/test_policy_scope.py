import unittest
from crawler.core.policy_scope import validate_policy_answer_scope


class ScopeTests(unittest.TestCase):
    def test_infers_single_city(self):
        self.assertEqual(validate_policy_answer_scope(True,(),"09462000"),("09462000",))

    def test_answer_requires_city(self):
        with self.assertRaises(ValueError):
            validate_policy_answer_scope(True,(),None)

    def test_answer_rejects_multiple_cities(self):
        with self.assertRaises(ValueError):
            validate_policy_answer_scope(True,("09462000","09362000"),None)

    def test_answer_detects_question_cli_mismatch(self):
        with self.assertRaises(ValueError):
            validate_policy_answer_scope(True,("09462000",),"09362000")

    def test_malformed_ags_rejected(self):
        with self.assertRaises(ValueError):
            validate_policy_answer_scope(True,("Bayreuth",),None)


if __name__=='__main__':
    unittest.main()
