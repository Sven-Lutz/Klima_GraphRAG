#!/usr/bin/env python3
"""E2: prospective Level-Q quote-access experiment. Offline/stdlib, no factual scoring.

First-stage: fixed, place-name-delexicalized lexical BM25 (L2) + Top10.
Policies: direct source chunks vs bounded within-source chunk and span re-ranking.
All use SAME first-stage source versions, per-URL quota and budget proxy.
No task is claimed answered; outcomes are quote recovery only.
"""
from __future__ import annotations
import sys, argparse, csv, json, hashlib, re
from pathlib import Path
from collections import defaultdict
from provenance_contract import QuoteAnchor,verify_anchor,load_catalog,ProvenanceError

VENDOR=Path(__file__).parent/'vendor'
sys.path.insert(0,str(VENDOR))
import lexical,selector,span_selector

BUDGET=8000;TOP_K=10;MAX_UNITS=10;MAX_PER_SOURCE=3
POLICIES=('direct','scoped_exact_chunks','scoped_morph_chunks','scoped_role_chunks','scoped_morph_spans','scoped_role_spans')

def read_jsonl(p:Path)->list[dict]:
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]

def read_csv(p:Path)->list[dict]:
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def filehash(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()

def city_parts(name:str)->set[str]:
    # Standardized *query term* only, not attribution inference.
    city=name.split(' /')[0].split(',')[0].strip().casefold()
    return {city,city.replace('ü','ue').replace('ä','ae').replace('ö','oe'),city.replace('ü','u').replace('ä','a').replace('ö','o')}

def source_ver(u):return selector.version_keys(u)

def validate_corpus(corpus:list[dict],cases:list[dict],tasks:list[dict]):
    allowed={c['ags'] for c in cases}
    cases_by_id={c['case_id']:c for c in cases}
    if len(cases)!=4 or len(cases_by_id)!=4 or len(tasks)!=16 or len({t['task_id'] for t in tasks})!=16:
        raise ValueError('Expected locked four cases and sixteen tasks')
    seen=set()
    for t in tasks:
        if t['case_id'] not in cases_by_id or t['ags']!=cases_by_id[t['case_id']]['ags'] or t['cutoff_date']!='2026-10-19':
            raise ValueError('Task/case/cutoff mismatch')
    for u in corpus:
        if u['unit_id'] in seen:raise ValueError('Duplicate unit identity')
        seen.add(u['unit_id'])
        if u['acquisition_ags'] not in allowed:raise ValueError('Development municipality or unrelated AGS in E2 corpus')
        if not isinstance(u['text'],str) or not u['text'].strip():raise ValueError('Empty text')
        if not u.get('raw_hashes'):raise ValueError('Missing body hash')
        for h in u['raw_hashes']:
            if not re.fullmatch('[a-f0-9]{64}',h):raise ValueError('Invalid body hash')
        if not u['source_url'].startswith(('http://','https://')):raise ValueError('Bad URL')
    for c in cases:
        if not any(u['acquisition_ags']==c['ags'] for u in corpus):
            raise ValueError('No newly acquired source text for '+c['case_id']+'; do not substitute E1 corpus')

def selection_by_policy(pool:list[dict],first:list[dict],task:dict,city:set[str],budget:int)->dict[str,dict]:
    # Inherited pre-registered v0.4 algorithms, no tuning against E2 labels.
    lexical.CITY_TERMS['A']=city
    results={}
    direct=[(float(len(first)-idx),u) for idx,u in enumerate(first)]
    ranked_exact,_=lexical.rank(pool,task['question_de'],'A','L2')
    ranked_morph,_=lexical.rank(pool,task['question_de'],'A','L3')
    ranked_role,_=lexical.rank(pool,task['question_de']+' '+selector.ROLE_TERMS[task['task_type']],'A','L3')
    span_units=span_selector.spans(pool)
    span_scored=span_selector.score_spans(span_units,task['question_de'],'A','')
    span_role=span_selector.score_spans(span_units,task['question_de'],'A',selector.ROLE_TERMS[task['task_type']])
    for name,order,lookup in (
        ('direct',direct,{u['unit_id']:u for u in first}),
        ('scoped_exact_chunks',ranked_exact,{u['unit_id']:u for u in pool}),
        ('scoped_morph_chunks',ranked_morph,{u['unit_id']:u for u in pool}),
        ('scoped_role_chunks',ranked_role,{u['unit_id']:u for u in pool}),
        ('scoped_morph_spans',span_scored,{u['unit_id']:u for u in span_units}),
        ('scoped_role_spans',span_role,{u['unit_id']:u for u in span_units})):
        selected,used=selector.select(order,budget=budget,max_units=MAX_UNITS,max_per_source=MAX_PER_SOURCE)
        originals=[lookup[x['unit_id']] for x in selected]
        if used>budget or any(x['acquisition_ags']!=task['ags'] for x in selected):
            raise AssertionError('Boundary/budget violation')
        results[name]={'selected':selected,'originals':originals,'used':used}
    return results

def quote_in(selected_originals:list[dict],anchor:QuoteAnchor)->int:
    # Literal quote must occur in a selected passage with its same source body.
    return int(any(u['acquisition_ags']==anchor.ags and u['source_url']==anchor.url
                   and anchor.body_sha256 in u['raw_hashes'] and anchor.quote in u['text']
                   and u.get('parent_unit_id',u['unit_id'])==anchor.unit_id
                   for u in selected_originals))

def execute(corpus_path:Path, cases_path:Path,tasks_path:Path,anchor_path:Path,catalog_path:Path,
            out:Path,budget:int=BUDGET, raw_dir:Path|None=None,seal_path:Path|None=None,
            freeze_manifest_path:Path|None=None,dev_smoke:bool=False)->dict:
    if out.exists():raise FileExistsError('Evaluation outputs immutable; use a fresh directory')
    if budget<=0:raise ValueError('Budget must be positive')
    if seal_path is None or freeze_manifest_path is None:raise ValueError('Prospective comparison requires two independent freeze manifests')
    if not dev_smoke and raw_dir is None:raise ValueError('Prospective Q validation requires original raw bytes')
    from seal_e2 import validate_seal
    validate_seal({'corpus':corpus_path,'cases':cases_path,'tasks':tasks_path,'anchors':anchor_path,'catalog':catalog_path,
                   'freeze_manifest':freeze_manifest_path},seal_path)
    units=read_jsonl(corpus_path);cases=read_csv(cases_path);tasks=read_csv(tasks_path)
    validate_corpus(units,cases,tasks)
    records=read_jsonl(catalog_path)
    if not dev_smoke and any(r.get('source_type')=='synthetic' for r in records):
        raise ValueError('Synthetic sources forbidden in prospective results')
    catalog=load_catalog(records,raw_dir)
    byid,version_ix=selector.make_index(units)
    for u in units:
        for key in source_ver(u):
            if key not in catalog:raise ProvenanceError('Corpus refers to non-catalogued body version')
    anchors=[]
    for a in read_csv(anchor_path):
        k={x:a[x] for x in ('task_id','ags','url','body_sha256','unit_id','quote','evidence_role')}
        k['start_char']=int(a['start_char']);k['end_char']=int(a['end_char'])
        x=QuoteAnchor(**k);verify_anchor(x,byid,catalog);anchors.append(x)
    tid={t['task_id']:t for t in tasks}
    if any(a.task_id not in tid or a.ags!=tid[a.task_id]['ags'] for a in anchors):
        raise ValueError('Quote anchor/task mismatch')
    anchor_by_task=defaultdict(list)
    for a in anchors:anchor_by_task[a.task_id].append(a)
    cases_by_id={c['case_id']:c for c in cases}
    stage=[];per_anchor=[];selection=[]
    for task in sorted(tasks,key=lambda x:x['task_id']):
        cand=[u for u in units if u['acquisition_ags']==task['ags']]
        city=city_parts(cases_by_id[task['case_id']]['official_name'])
        lexical.CITY_TERMS['A']=city
        first_rank,_=lexical.rank(cand,task['question_de'],'A','L2')
        first_hits=lexical.get_top(first_rank,TOP_K)
        first_units=[byid[x['unit_id']] for x in first_hits]
        pool=selector.source_pool(first_hits,byid,version_ix,task['ags'])
        permitted={k for h in first_hits for k in source_ver(byid[h['unit_id']])}
        if any(not source_ver(u).intersection(permitted) for u in pool):raise AssertionError('Source boundary escaped')
        stage.append({'task_id':task['task_id'],'first_stage_units':len(first_units),
                      'eligible_source_body_keys':len(permitted),'source_pool_units':len(pool),
                      'anchors_declared':len(anchor_by_task[task['task_id']]), 'first_stage_has_no_evidence':len(first_units)==0})
        policies=selection_by_policy(pool,first_units,task,city,budget)
        for name,res in policies.items():
            for rank,s in enumerate(res['selected'],1):
                selection.append({'task_id':task['task_id'],'policy':name,'rank':rank,
                  'unit_id':s['unit_id'],'source_url':s['source_url'],'acquisition_ags':s['acquisition_ags'],
                  'raw_hashes':json.dumps(s['raw_hashes']), 'chars_accounted':s['cost_chars'],
                  'claim_supported':'NOT_ASSESSED'})
            for a in anchor_by_task[task['task_id']]:
                reachable=int(any(a.ags==u['acquisition_ags'] and a.url==u['source_url'] and
                     a.body_sha256 in u['raw_hashes'] and a.quote in u['text']
                      and u['unit_id']==a.unit_id for u in pool))
                found=quote_in(res['originals'],a)
                if found and not reachable:raise AssertionError('Quote without source reachability')
                per_anchor.append({'task_id':task['task_id'],'policy':name,'anchor_unit_id':a.unit_id,
                   'evidence_role':a.evidence_role,'source_body_reached':reachable,'literal_quote_selected':found,
                   'budget_chars':budget,'budget_used':res['used'], 'claim_supported':'NOT_ASSESSED'})
    out.mkdir(parents=True)
    def write_csv(file:str,ls:list[dict]):
        with (out/file).open('w',newline='',encoding='utf8') as f:
            if ls:
                w=csv.DictWriter(f,fieldnames=list(ls[0]));w.writeheader();w.writerows(ls)
            else:f.write('NO_ROWS\n')
    write_csv('first_stage.csv',stage);write_csv('quote_evaluation.csv',per_anchor);write_csv('selected_passages.csv',selection)
    counts={}
    for policy in POLICIES:
        subset=[r for r in per_anchor if r['policy']==policy]
        n=len(subset); reached=sum(r['source_body_reached'] for r in subset); recovered=sum(r['literal_quote_selected'] for r in subset)
        counts[policy]={'num_anchors':n,'source_reachable':reached,'quote_recovered':recovered,
                        'q_recovery_rate':recovered/n if n else None,
                        'q_conditioned_rate':recovered/reached if reached else None}
    manifest={'phase':'synthetic_development_smoke_only' if dev_smoke else 'prospective_level_Q_only','no_gold_standard':True,
              'evaluation_contract_version':'E2-1.0','case_frame_version':json.loads(freeze_manifest_path.read_text(encoding='utf8'))['version'],'budget_unit':'accounted_characters_not_tokens',
              'budget':budget,'max_units':MAX_UNITS,'max_per_source':MAX_PER_SOURCE,
              'first_stage':'L2-place-delexicalized-BM25-Top10','source_scope':'AGS+URL+SHA256',
              'policy_names':POLICIES,'no_inference':True,'claim_status':'NOT_ASSESSED',
              'seal_sha256':filehash(seal_path),
              'inputs_sha256':{str(k):filehash(k) for k in [corpus_path,cases_path,tasks_path,anchor_path,catalog_path]},
              'output_sha256':{x:filehash(out/x) for x in ['first_stage.csv','quote_evaluation.csv','selected_passages.csv']},
              'summary':counts}
    (out/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    return counts

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in ('corpus','cases','tasks','anchors','catalog','out'):
        ap.add_argument('--'+name,required=True,type=Path)
    ap.add_argument('--raw-dir',type=Path);ap.add_argument('--seal',type=Path,required=True)
    ap.add_argument('--freeze-manifest',type=Path,required=True);ap.add_argument('--dev-smoke',action='store_true')
    ap.add_argument('--budget',type=int,default=BUDGET)
    args=ap.parse_args();result=execute(args.corpus,args.cases,args.tasks,args.anchors,args.catalog,args.out,args.budget,args.raw_dir,args.seal,args.freeze_manifest,args.dev_smoke)
    print(json.dumps(result,indent=2))
