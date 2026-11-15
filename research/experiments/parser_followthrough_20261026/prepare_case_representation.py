"""New development representation; original E2 sources/results remain read-only."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import platform
import socket
import subprocess
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
def denied(*args,**kwargs): raise RuntimeError('Network disabled for offline development export')
socket.create_connection=denied
socket.socket.connect=denied
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf
from crawler.core.engine import _segment_features

def digest(data): return hashlib.sha256(data).hexdigest()
def build(out):
    out.mkdir(parents=True,exist_ok=False)
    follow=ROOT/'research/experiments/heat_planning_followup_20261026/snapshot01'
    old=ROOT/'research/experiments/e2_v11_reference_20261025/reference01/documents.jsonl'
    d=next(json.loads(line) for line in old.read_text().splitlines() if json.loads(line)['document_id']=='D053')
    inputs=[{'source_id':'DEV-D053','url':d['url'],'body_sha256':d['body_sha256'],'source_type':d['source_type'],'content_type':d['content_type'],'raw_path':d['raw_path_from_repository'],'fetched_at_utc':d['acquisition_time'],'origin':'historical D053 body; parser version changed, not new acquisition'}]
    for line in (follow/'catalog.jsonl').read_text().splitlines():
        r=json.loads(line)
        inputs.append({**r,'raw_path':str((follow/r['raw_path']).relative_to(ROOT)),'origin':'targeted 26.10.2026 development follow-up'})
    tracked=['crawler/core/parsers/html_parser.py','crawler/core/parsers/pdf_parser.py','crawler/core/models.py','crawler/core/engine.py',str(Path(__file__).relative_to(ROOT))]
    code={name:digest((ROOT/name).read_bytes()) for name in tracked}
    config={'namespace':'DEV-WP02','code_sha256':code,'packages':{name:importlib.metadata.version(name) for name in ['beautifulsoup4','lxml']},'python':platform.python_version(),'pdftotext':subprocess.run(['pdftotext','-v'],capture_output=True,text=True,timeout=5).stderr.splitlines()[0]}
    version=digest(json.dumps(config,sort_keys=True).encode())
    units=[];catalog=[]
    for r in inputs:
        b=(ROOT/r['raw_path']).read_bytes();assert digest(b)==r['body_sha256']
        fr=FetchResult(r['url'],200,r['content_type'],b,{})
        result=(parse_pdf if r['source_type']=='pdf' else parse_html)(fr,r['url'])
        assert result.text.strip() and result.segments
        start=len(units)
        for i,s in enumerate(result.segments):
            if not s.text.strip():continue
            ident={'namespace':'DEV-WP02','source_id':r['source_id'],'url':r['url'],'body_sha256':r['body_sha256'],'representation_sha256':version,'segment_index':i}
            score,hits,negative=_segment_features(s.text)
            units.append({**ident,'unit_id':'DEV-WP02-'+digest(json.dumps(ident,sort_keys=True).encode()),'ags_acquisition_frame':'09780147','claim_territorial_scope':'NOT_ADJUDICATED','text':s.text,'text_sha256':digest(s.text.encode()),'heading_context':s.heading_context,'page_ref':s.page_ref,'segment_type':s.segment_type,'parser_order_index':s.order_index,'fetched_at_utc':r['fetched_at_utc'],'review_status':'NOT_ADJUDICATED','default_admission_observation':{'impact_score':score,'hit_count':hits,'negative_flag':bool(negative),'would_pass_existing_length_score_negative_gate':len(s.text)>=80 and not negative and score>=10,'is_relevance_judgment':False}})
        catalog.append({**r,'representation_sha256':version,'units':len(units)-start,'fulltext_sha256':digest(result.text.encode()),'fulltext_characters':len(result.text),'source_completeness_claim':False,'publisher_basis':'municipal same-host self-identification; see separately captured imprint','review_status':'NOT_ADJUDICATED'})
    assert len({u['unit_id'] for u in units})==len(units)
    markers={'DEV-D053':['Inbetriebnahme November 2005','Dorfentwicklungs-GmbH','Daten 2020','3.572.280'],'WP26-S01':['16. Oktober 2024'],'WP26-S02':['11.11.2024'],'WP26-S03':['Satelliten-BHKW']}
    for source,needles in markers.items():
        txt='\n'.join(u['text'] for u in units if u['source_id']==source)
        assert all(n in txt for n in needles),(source,needles)
    for name,data in [('corpus.jsonl',units),('catalog.jsonl',catalog)]:
        with (out/name).open('x') as f:
            for row in data:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    manifest={'purpose':'VERSIONED_DEVELOPMENT_REPRESENTATION_NOT_REFERENCE_OR_TEST_SET','prospective':False,'independent_reference_available':False,'historical_e2_modified':False,'all_units_exported_before_optional_admission':True,'ids_reusable_across_representation_versions':False,'representation_sha256':version,'configuration':config,'source_count':len(catalog),'unit_count':len(units),'by_source':{r['source_id']:r['units'] for r in catalog},'input_body_hashes':{r['source_id']:r['body_sha256'] for r in catalog},'files':{name:digest((out/name).read_bytes()) for name in ['corpus.jsonl','catalog.jsonl']}}
    with (out/'manifest.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2);f.write('\n')
    return manifest
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
    m=build(a.out)
    print(json.dumps({k:m[k] for k in ['source_count','unit_count','by_source','representation_sha256']},ensure_ascii=False))
