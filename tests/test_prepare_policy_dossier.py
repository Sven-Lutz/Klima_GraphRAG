import unittest
from crawler.scripts.prepare_policy_dossier import prepare


class DossierTests(unittest.TestCase):
    def test_excludes_wrong_city_and_no_fabricated_claims(self):
        data={"items":[
            {"evidence_id":"x1", "document":{"municipality_ids":["09462000"],"source_url":"https://example.org/a"},"evidence_text":"Die Stadt plant ein Programm."},
            {"evidence_id":"x2", "document":{"municipality_ids":["09362000"],"source_url":"https://example.org/b"},"evidence_text":"Gemeinde fördert Solar."}]}
        d=prepare(data,"q1","09462000","p","2026-11-03")
        self.assertEqual(len(d['evidence']),1)
        self.assertFalse(d['evidence'][0]['verified'])
        self.assertEqual(d['evidence'][0]['claim'],'mentions')
        self.assertEqual(len(d['review_metadata']['excluded_retrieval_items']),1)

    def test_missing_municipality_tag_excluded(self):
        d=prepare({"items":[{"evidence_text":"test", "source_url":"https://example.org"}]},"q1","09462000","p","2026-11-03")
        self.assertEqual(d['evidence'],[])

    def test_duplicates_fail(self):
        items=[{"evidence_id":"x", "document":{"municipality_ids":["09462000"],"source_url":"https://example.org"},"text":"abc"}]*2
        with self.assertRaises(ValueError):
            prepare(items,"q1","09462000","p","2026-11-03")


if __name__ == '__main__':
    unittest.main()
