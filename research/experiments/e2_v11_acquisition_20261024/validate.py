"""Offline validation only; never requests sources or produces labels."""
import csv,hashlib,importlib.util,json
from pathlib import Path
from datetime import datetime
from collections import Counter
from urllib.parse import urlsplit
root=Path(__file__).resolve().parent;out=root/'export'
def rows(name):return [json.loads(s) for s in (out/name).read_text().splitlines()]
spec=importlib.util.spec_from_file_location('contract',root/'protocol/provenance_contract.py')
import sys
mod=importlib.util.module_from_spec(spec);sys.modules['contract']=mod;spec.loader.exec_module(mod)
records=rows('catalog.jsonl');units=rows('corpus.jsonl');req=rows('requests.jsonl')
keys=mod.load_catalog(records,out/'raw');manifest=json.loads((out/'manifest.json').read_text())
cases=list(csv.DictReader((root/'protocol/frozen/cases_frozen.csv').open()))
assert {c['ags'] for c in cases}==set(manifest['responses'])
assert len(list(csv.DictReader((root/'protocol/frozen/tasks_frozen.csv').open())))==16
freeze=json.loads((root/'protocol/frozen/freeze_manifest.json').read_text())
for name,key in [('cases','case'),('tasks','task')]:assert hashlib.sha256((root/f'protocol/frozen/{name}_frozen.csv').read_bytes()).hexdigest()==freeze[key+'_file_sha256']
assert freeze['cutoff_date']=='2026-10-19'
counts=Counter(r['ags'] for r in req if 'status' in r)
assert dict(counts)=={a:n for a,n in manifest['responses'].items() if n}
assert all(n<=20 for n in counts.values())
last={}
for r in req:
 host=urlsplit(r['url']).hostname;t=datetime.fromisoformat(r['acquisition_time']).timestamp()
 if host in last:assert t-last[host]>=1,(host,t-last[host])
 last[host]=t
 if r.get('complete'):
  b=(out/'raw'/r['body_sha256']).read_bytes();assert len(b)<=5*1024*1024
  assert len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['body_sha256']
for r in records:
 assert r['institutional_provenance'] and r['document_date'] is None
 for p in r['institutional_provenance']:assert (out/'raw'/p['body_sha256']).is_file()
assert len({u['unit_id'] for u in units})==len(units)
for u in units:
 assert all(k in u for k in ['unit_id','acquisition_ags','source_url','raw_hashes','text','heading_context','page_ref','origins'])
 assert u['raw_hashes'] and all((u['acquisition_ags'],u['source_url'],h) in keys for h in u['raw_hashes'])
 assert u['text'].strip()
result={'status':'PASS','checks':['frozen hashes/AGS/16 tasks/cutoff','E2 SourceVersion schema and raw SHA-256','unit-source linkage and unique IDs','response count and body-size caps','per-host minimum spacing','institutional evidence raw references'],'documents':len(records),'units':len(units),'responses':dict(counts),'claim_validity':'NOT_ASSESSED','date_eligibility':'NOT_ESTABLISHED'}
(out/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result))
