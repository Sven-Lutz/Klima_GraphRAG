import unittest
from crawler.scripts.evaluate_policy_decisions import evaluate


def g(i, label):
    return {"question_id": i,"municipality_id":"09462000","policy_id":"p", "label":label}


def p(i,label):
    return {"question_id":i, "municipality_id":"09462000","policy_id":"p", "label":label}


class EvalTests(unittest.TestCase):
    def test_abstention_measured_separately(self):
        r=evaluate([g('q1','yes'),g('q2','no'),g('q3','unknown')],
                   [p('q1','yes'),p('q2','unknown'),p('q3','unknown')])
        self.assertEqual(r['false_no'],0)
        self.assertEqual(r['decision_coverage'],1/3)
        self.assertEqual(r['selective_accuracy'],1)
        self.assertEqual(r['exact_accuracy'],2/3)

    def test_false_yes_and_no_tracked(self):
        r=evaluate([g('q1','no'),g('q2','yes')],[p('q1','yes'),p('q2','no')])
        self.assertEqual((r['false_yes'],r['false_no']),(1,1))

    def test_duplicate_predictions_fail(self):
        with self.assertRaises(ValueError):
            evaluate([g('q1','yes')],[p('q1','yes'),p('q1','yes')])

    def test_wrong_city_fails(self):
        bad=p('q1','yes');bad['municipality_id']='09362000'
        with self.assertRaises(ValueError):
            evaluate([g('q1','yes')],[bad])

    def test_missing_prediction_fails(self):
        with self.assertRaises(ValueError):
            evaluate([g('q1','yes')],[])

    def test_conflict_is_abstention(self):
        r=evaluate([g('q1','yes')],[p('q1','conflict')])
        self.assertEqual(r['abstained'],1)
        self.assertIsNone(r['selective_accuracy'])


if __name__ == '__main__':
    unittest.main()
