import csv,unittest,tempfile,json,hashlib
from pathlib import Path
from freeze_e2 import prepare,pick,SEED
from provenance_contract import *

H=hashlib.sha256(b'document').hexdigest()
S=SourceVersion('09190118','https://example.org/a',H,'2026-10-21T12:00:00Z','2025-02-01')
U={'unit_id':'u1','acquisition_ags':S.ags,'source_url':S.url,'raw_hashes':[H], 'text':'Die Gemeinde Burggen wurde genannt. Netzwerkbetreiber nicht nachgewiesen.'}
A=QuoteAnchor('T1',S.ags,S.url,H,'u1','Die Gemeinde Burggen',0,20,'context_only')
class TestContracts(unittest.TestCase):
 def test_literal(self):
  r=verify_anchor(A,{'u1':U},{(S.ags,S.url,H)})
  self.assertEqual(r['claim_supported'],'NOT_ASSESSED');self.assertEqual(r['territorial_scope'],'UNKNOWN')
 def test_wrong_ags(self):
  from dataclasses import replace
  with self.assertRaises(ProvenanceError):verify_anchor(replace(A,ags='09564000'),{'u1':U},{(S.ags,S.url,H)})
 def test_wrong_body(self):
  from dataclasses import replace
  with self.assertRaises(ProvenanceError):verify_anchor(replace(A,body_sha256='0'*64),{'u1':U},{(S.ags,S.url,H)})
 def test_wrong_quote(self):
  from dataclasses import replace
  with self.assertRaises(ProvenanceError):verify_anchor(replace(A,quote='die Gemeinde Burggen'),{'u1':U},{(S.ags,S.url,H)})
 def test_wrong_offset(self):
  from dataclasses import replace
  with self.assertRaises(ProvenanceError):verify_anchor(replace(A,start_char=1,end_char=21),{'u1':U},{(S.ags,S.url,H)})
 def test_unknown_claim_block(self):
  self.assertFalse(require_safe_claim('unknown','2025-01-01','2026-10-19',territory_validated=True))
 def test_unverified_region_block(self):
  self.assertFalse(require_safe_claim('verified','2025-01-01','2026-10-19',territory_validated=False))
 def test_future_dated_block(self):
  self.assertFalse(require_safe_claim('verified','2026-10-20','2026-10-19',territory_validated=True))
 def test_two_versions(self):
  s2=SourceVersion(S.ags,S.url,'f'*64,S.acquisition_time,'2026-01-01')
  self.assertTrue(same_measure_two_sources(S,s2,'policy-A','policy-A','2026-10-19'))
  self.assertFalse(same_measure_two_sources(S,S,'policy-A','policy-A','2026-10-19'))
  self.assertFalse(same_measure_two_sources(S,s2,'policy-A','policy-B','2026-10-19'))
 def test_source_raw_bytes(self):
  with tempfile.TemporaryDirectory() as p:
   (Path(p)/H).write_bytes(b'document')
   rec={'ags':S.ags,'url':S.url,'body_sha256':H,'acquisition_time':S.acquisition_time}
   self.assertEqual(len(load_catalog([rec],Path(p))),1)
   (Path(p)/H).write_bytes(b'altered')
   with self.assertRaises(ProvenanceError):load_catalog([rec],Path(p))
 def test_duplicate_source_reject(self):
  rec={'ags':S.ags,'url':S.url,'body_sha256':H,'acquisition_time':S.acquisition_time}
  with self.assertRaises(ProvenanceError):load_catalog([rec,rec])
 def test_input_strict_frame(self):
  with tempfile.TemporaryDirectory() as p:
   bad=Path(p)/'frame.csv';bad.write_text('ags,eligible_A,eligible_B\n01010101,1,0\n')
   reg=Path(p)/'empty.sqlite';reg.write_bytes(b'')
   with self.assertRaises(ValueError):prepare(bad,reg,Path(p)/'o')

if __name__=='__main__':unittest.main()

class TestEndToEnd(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.root=Path(__file__).parent
  cls.inputs={
   'corpus':cls.root/'smoke/corpus.jsonl',
   'cases':cls.root/'frozen/cases_frozen.csv',
   'tasks':cls.root/'frozen/tasks_frozen.csv',
   'anchors':cls.root/'smoke/anchors.csv',
   'catalog':cls.root/'smoke/catalog.jsonl',
   'freeze_manifest':cls.root/'frozen/freeze_manifest.json'
  }
 def test_replay_and_bounds(self):
  import evaluate_e2,seal_e2
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   seal_e2.seal(self.inputs,d/'lock.json',self.root/'smoke/raw')
   m=evaluate_e2.execute(self.inputs['corpus'],self.inputs['cases'],self.inputs['tasks'],
        self.inputs['anchors'],self.inputs['catalog'],d/'eval1',raw_dir=self.root/'smoke/raw',
        seal_path=d/'lock.json',freeze_manifest_path=self.inputs['freeze_manifest'],dev_smoke=True)
   n=evaluate_e2.execute(self.inputs['corpus'],self.inputs['cases'],self.inputs['tasks'],
        self.inputs['anchors'],self.inputs['catalog'],d/'eval2',raw_dir=self.root/'smoke/raw',
        seal_path=d/'lock.json',freeze_manifest_path=self.inputs['freeze_manifest'],dev_smoke=True)
   self.assertEqual(m,n)
   for fname in ('quote_evaluation.csv','first_stage.csv','selected_passages.csv'):
    self.assertEqual((d/'eval1'/fname).read_bytes(),(d/'eval2'/fname).read_bytes())
   for policy,v in m.items():
    self.assertEqual(v['num_anchors'],16)
    self.assertLessEqual(v['quote_recovered'],v['source_reachable'])
   per={}
   with (d/'eval1/selected_passages.csv').open() as f:
    selected_rows=list(csv.DictReader(f))
   for row in selected_rows:
    key=(row['task_id'],row['policy'])
    per.setdefault(key,[]).append(row)
    self.assertEqual(row['claim_supported'],'NOT_ASSESSED')
   for rows in per.values():
    self.assertLessEqual(sum(int(r['chars_accounted']) for r in rows),8000)
    self.assertLessEqual(len(rows),10)
    from collections import Counter
    self.assertLessEqual(max(Counter(r['source_url'] for r in rows).values()),3)
 def test_seal_blocks_mutation(self):
  from seal_e2 import seal,validate_seal
  with tempfile.TemporaryDirectory() as td:
   d=Path(td);seal(self.inputs,d/'seal.json',self.root/'smoke/raw')
   validate_seal(self.inputs,d/'seal.json')
   copy=d/'mutated.csv';copy.write_bytes(self.inputs['anchors'].read_bytes()+b'\n')
   tampered={**self.inputs,'anchors':copy}
   with self.assertRaises(ValueError):validate_seal(tampered,d/'seal.json')
 def test_no_synthetic_prospective(self):
  import evaluate_e2
  from seal_e2 import seal
  with tempfile.TemporaryDirectory() as td:
   d=Path(td);seal(self.inputs,d/'seal.json',self.root/'smoke/raw')
   with self.assertRaisesRegex(ValueError,'Synthetic sources forbidden'):
    evaluate_e2.execute(self.inputs['corpus'],self.inputs['cases'],self.inputs['tasks'],
             self.inputs['anchors'],self.inputs['catalog'],d/'out',raw_dir=self.root/'smoke/raw',
             seal_path=d/'seal.json',freeze_manifest_path=self.inputs['freeze_manifest'])
 def test_development_ags_rejected(self):
  from evaluate_e2 import validate_corpus,read_csv
  city=read_csv(self.inputs['cases']);tasks=read_csv(self.inputs['tasks'])
  foreign={'unit_id':'X','acquisition_ags':'09564000','source_url':'https://nuernberg.de/a','raw_hashes':['1'*64], 'text':'Klimaziel'}
  with self.assertRaisesRegex(ValueError,'Development municipality'):
   validate_corpus([foreign],city,tasks)
