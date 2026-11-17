import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from crawler.scripts.pilot_preflight import inspect


class PreflightTests(unittest.TestCase):
    def test_empty_db_reports_absence(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'empty.sqlite'
            sqlite3.connect(p).close()
            result=inspect(p,['09462000'])
            self.assertTrue(any(x['severity']=='critical' for x in result['findings']))

    def test_readonly_counts(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'tiny.sqlite'
            with sqlite3.connect(p) as con:
                con.executescript('''
                CREATE TABLE documents_raw(document_id TEXT PRIMARY KEY, municipality_id TEXT);
                CREATE TABLE municipality_documents(document_id TEXT, municipality_id TEXT);
                CREATE TABLE segments(segment_id TEXT PRIMARY KEY);
                CREATE TABLE chunks(chunk_id TEXT, segment_id TEXT, embedding BLOB);
                CREATE TABLE seed_jobs(id TEXT);
                INSERT INTO documents_raw VALUES ('d1','09462000');
                INSERT INTO municipality_documents VALUES ('d1','09462000');
                INSERT INTO segments VALUES ('s1');
                INSERT INTO chunks VALUES ('c1','s1',NULL);
                ''')
            r=inspect(p,['09462000'])
            self.assertEqual(r['row_counts']['documents_raw'],1)
            self.assertEqual(r['row_counts']['chunks_with_missing_parent_segment'],0)
            self.assertEqual(r['municipalities']['09462000']['documents_linked'],1)


if __name__=='__main__':
    unittest.main()
