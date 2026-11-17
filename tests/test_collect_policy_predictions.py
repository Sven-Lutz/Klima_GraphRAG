import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crawler.scripts.collect_policy_predictions import collect


class CollectTests(unittest.TestCase):
    def test_collect_and_duplicate_check(self):
        with TemporaryDirectory() as folder:
            p=Path(folder)/'a.json'
            p.write_text(json.dumps({'decision':{'question_id':'q1','municipality_id':'09462000',
                            'policy_id':'fictional','label':'unknown'}}), encoding='utf-8')
            result=collect([p]);self.assertEqual(result[0]['label'],'unknown')
            with self.assertRaisesRegex(ValueError,'duplicate'):
                collect([p,p])

    def test_reject_untrusted_json(self):
        with TemporaryDirectory() as folder:
            p=Path(folder)/'a.json';p.write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'decide output'):
                collect([p])


if __name__=='__main__':
    unittest.main()
