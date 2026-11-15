#!/usr/bin/env python3
"""Reproducible input lock for the prospective *mechanical quote* comparison.
Not a trusted timestamp or independently judged gold label.
"""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
from provenance_contract import load_catalog,QuoteAnchor,verify_anchor
from evaluate_e2 import read_jsonl,read_csv,validate_corpus,filehash

FILES=('corpus','cases','tasks','anchors','catalog','freeze_manifest')
CODE=('freeze_e2.py','provenance_contract.py','evaluate_e2.py','seal_e2.py','vendor/lexical.py','vendor/selector.py','vendor/span_selector.py')

def seal(inputs:dict[str,Path],out:Path,raw_dir:Path|None=None)->dict:
    if out.exists():raise FileExistsError('Seal immutable; use new version')
    frame_lock=json.loads(inputs['freeze_manifest'].read_text(encoding='utf8'))
    if frame_lock.get('version')!='E2-prospective-prereg-1.1-metadata-amendment' or frame_lock.get('case_file_sha256')!=filehash(inputs['cases']) or frame_lock.get('task_file_sha256')!=filehash(inputs['tasks']) or frame_lock.get('sampling_seed')!='E2-2026-10-21-prereg-v1':
        raise ValueError('Municipality/task freeze changed after prior preregistration')
    corpus=read_jsonl(inputs['corpus']);cases=read_csv(inputs['cases']);tasks=read_csv(inputs['tasks'])
    validate_corpus(corpus,cases,tasks)
    catalog=load_catalog(read_jsonl(inputs['catalog']),raw_dir)
    byid={u['unit_id']:u for u in corpus}
    seen=set()
    for a in read_csv(inputs['anchors']):
        x=QuoteAnchor(task_id=a['task_id'],ags=a['ags'],url=a['url'],body_sha256=a['body_sha256'],
                      unit_id=a['unit_id'],quote=a['quote'],start_char=int(a['start_char']),
                      end_char=int(a['end_char']),evidence_role=a['evidence_role'])
        if x.task_id not in {t['task_id'] for t in tasks}:raise ValueError('Task not frozen')
        key=(x.task_id,x.ags,x.url,x.body_sha256,x.unit_id,x.start_char,x.end_char)
        if key in seen:raise ValueError('Duplicate anchor identity')
        seen.add(key)
        verify_anchor(x,byid,catalog)
    base=Path(__file__).parent
    obj={'type':'E2-mechanical-input-seal','note':'hash-based freeze only; not trusted timestamp, human labels or V-level truth',
         'raw_body_sha_verified':raw_dir is not None,
         'input_sha256':{k:filehash(v) for k,v in inputs.items()},
         'code_sha256':{k:filehash(base/k) for k in CODE},
         'source_unit_count':len(corpus),'q_anchor_count':len(seen),'task_count':len(tasks),
         'no_claim_truth_labels':True}
    out.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf8')
    return obj

def validate_seal(inputs:dict[str,Path],sealed:Path):
    ob=json.loads(sealed.read_text(encoding='utf8'))
    if ob.get('type')!='E2-mechanical-input-seal':raise ValueError('Bad seal type')
    for k,v in inputs.items():
        if filehash(v)!=ob['input_sha256'].get(k):raise ValueError('Input changed after seal: '+k)
    root=Path(__file__).parent
    for k,h in ob['code_sha256'].items():
        if filehash(root/k)!=h:raise ValueError('Evaluation code changed after seal: '+k)
    return ob

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in FILES:ap.add_argument('--'+name.replace('_','-'),dest=name,required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path);ap.add_argument('--raw-dir',type=Path)
    a=ap.parse_args();inputs={x:getattr(a,x) for x in FILES};r=seal(inputs,a.out,a.raw_dir)
    print(f"Sealed {r['source_unit_count']} units, {r['q_anchor_count']} Q anchors")
