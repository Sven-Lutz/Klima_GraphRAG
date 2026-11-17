import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crawler.scripts.pilot_scope_audit import inspect
from crawler.scripts.create_pilot_benchmark_template import POLICIES, rows


class PilotCohortTests(unittest.TestCase):
    def test_seed_db_and_expected_scope(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite'
            con=sqlite3.connect(path)
            con.executescript('CREATE TABLE seed_jobs(municipality_id TEXT); CREATE TABLE municipality_documents(municipality_id TEXT, document_id TEXT);')
            con.execute("INSERT INTO seed_jobs VALUES('09462000')")
            con.execute("INSERT INTO seed_jobs VALUES('09362000')")
            con.execute("INSERT INTO municipality_documents VALUES('09462000','d1')")
            con.commit();con.close()
            report=inspect(path, ['09362000','09462000'],2)
            self.assertTrue(report['passed'])
            self.assertEqual(report['linked_documents_by_ags']['09462000'],1)
            self.assertEqual(report['linked_documents_by_ags']['09362000'],0)
            self.assertFalse(inspect(path, ['09362000','09162000'],2)['passed'])
            self.assertFalse(inspect(path, expected_count=9)['passed'])

    def test_blank_gold_template_en_de(self):
        result=rows(['09462000','09362000'],'2026-11-03')
        self.assertEqual(len(result),2*len(POLICIES)*2)
        self.assertEqual({r['label'] for r in result},{''})
        self.assertEqual({r['language'] for r in result},{'en','de'})
        self.assertEqual(len({r['question_id'] for r in result}),len(result))


if __name__=='__main__':
    unittest.main()
