"""E2 pre-source amendment verification, no network or content inspection."""
import unittest,csv,json,tempfile,hashlib
from pathlib import Path
from freeze_e2 import prepare,SEED,TEAM_METADATA_EXCLUDE,EXCLUDE
R=Path(__file__).parent
def rows(p):
    with p.open(encoding='utf8',newline='') as f:
        return list(csv.DictReader(f))
class AmendmentTests(unittest.TestCase):
    def test_no_team_municipality_overlap(self):
        a=rows(R/'frozen/cases_frozen.csv')
        self.assertEqual({r['ags'] for r in a},{'09263000','09163000','09780147','09471123'})
        self.assertFalse({r['ags'] for r in a}&TEAM_METADATA_EXCLUDE)
        self.assertEqual(len(a),4)
    def test_original_large_cities_fully_overlap(self):
        r=rows(R/'reference_inputs/official_selection_frame_2026-06-30.csv')
        original_eligible={v['ags'] for v in r if v['eligible_A']=='1'}
        self.assertEqual(len(original_eligible),8)
        self.assertTrue(original_eligible.issubset(TEAM_METADATA_EXCLUDE))
        self.assertFalse(original_eligible-EXCLUDE)
    def test_prospective_task_copy_and_dates(self):
        t=rows(R/'frozen/tasks_frozen.csv')
        self.assertEqual(len(t),16)
        self.assertTrue(all(x['cutoff_date']=='2026-10-19' for x in t))
        self.assertTrue(all(x['ground_truth']=='NOT_ESTABLISHED' for x in t))
    def test_case_generator_reproduces_frozen_hashes(self):
        m=json.loads((R/'frozen/freeze_manifest.json').read_text())
        with tempfile.TemporaryDirectory() as p:
            mm=prepare(R/'reference_inputs/official_selection_frame_2026-06-30.csv',
             R/'reference_inputs/municipalities.sqlite',Path(p))
            self.assertEqual(mm['case_file_sha256'],m['case_file_sha256'])
            self.assertEqual(mm['task_file_sha256'],m['task_file_sha256'])
            self.assertEqual(mm['frame_sha256'],m['frame_sha256'])
            self.assertEqual(mm['registry_sha256'],m['registry_sha256'])
    def test_v1_1_manifest(self):
        m=json.loads((R/'frozen/freeze_manifest.json').read_text())
        self.assertEqual(m['version'],'E2-prospective-prereg-1.1-metadata-amendment')
        self.assertEqual(m['sampling_seed'],SEED)
        self.assertEqual(set(m['exclusion_ags_sorted']),EXCLUDE)
if __name__=='__main__':unittest.main()
