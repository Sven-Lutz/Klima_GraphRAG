#!/usr/bin/env python3
"""POST-HOC fairness sensitivity on E1 development anchors, never prospective gold.
Compares unchanged E1 first-stage rankings under a single 8000-char budget,
max 10 selected outputs, max 3 outputs per source URL for all policies.
"""
from __future__ import annotations
import csv,json,hashlib,sys,collections
from pathlib import Path
ROOT=Path(__file__).parent;V=ROOT/'vendor';sys.path.insert(0,str(V))
import selector,span_selector

def run(corpus:Path,pack:Path,out:Path,*,max_units:int=10,max_per_source:int=3):
    units=selector.load_jsonl(corpus)
    if len(units)!=1779:raise ValueError('Wrong E1 development corpus')
    byid,ix=selector.make_index(units)
    tasks=selector.load_tasks(pack/'tasks_instantiated.csv')
    first=selector.load_rankings(pack/'L0_union.jsonl',pack/'L1_union.jsonl',pack/'L2_L3_union.jsonl')
    anchors=list(csv.DictReader((pack/'anchors_dev.csv').open(encoding='utf8',newline='')))
    rows=[]
    for method in ('L0','L1','L2','L3'):
        for tid,task in sorted(tasks.items()):
            top=first[method][tid]['results'];pool=selector.source_pool(top,byid,ix,selector.CASE_AGS[task['case_id']])
            pspans=span_selector.spans(pool); pbyid={u['unit_id']:u for u in pspans}
            scores={'direct':selector.first_stage(top,byid),
                    'scoped_chunks':selector.rerank(pool,task,'scoped_morph'),
                    'scoped_role':selector.rerank(pool,task,'scoped_role'),
                    'spans_lexical':span_selector.score_spans(pspans,task['question_de'],task['case_id']),
                    'spans_role':span_selector.score_spans(pspans,task['question_de'],task['case_id'],selector.ROLE_TERMS.get(task['task_type'],''))}
            for policy,ranked in scores.items():
                sel,used=selector.select(ranked,budget=8000,max_units=max_units,max_per_source=max_per_source)
                selected_ids={s['unit_id'] for s in sel}
                for a in anchors:
                    if a['task_id']!=tid:continue
                    targets=set(a['unit_ids'].split('|'))
                    recovered=int(any(byid[t]['unit_id'] in selected_ids and a['quote'] in byid[t]['text'] for t in targets)) if policy!='spans_lexical' and policy!='spans_role' else int(any(pbyid[s['unit_id']]['parent_unit_id'] in targets and a['quote'] in pbyid[s['unit_id']]['text'] for s in sel))
                    reachable=int(any(byid[t]['unit_id'] in {u['unit_id'] for u in pool} for t in targets))
                    rows.append({'first_stage':method,'policy':policy,'task_id':tid,'anchor_id':a['anchor_id'],
                                 'lead_class':a['lead_class'],'source_reachable':reachable,'exact_quote_recovered':recovered,
                                 'budget_chars':8000,'budget_used_chars':used,'max_units':max_units,'per_source_limit':max_per_source,
                                 'gold':'NO','claim_supported':'NOT_ASSESSED'})
    out.mkdir(exist_ok=True,parents=True)
    with (out/'fairness_anchors.csv').open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    agg=[]
    for method in ('L0','L1','L2','L3'):
        for policy in ('direct','scoped_chunks','scoped_role','spans_lexical','spans_role'):
            subset=[r for r in rows if r['first_stage']==method and r['policy']==policy]
            agg.append({'first_stage':method,'policy':policy,'anchors':len(subset),'reach':sum(r['source_reachable'] for r in subset),
                        'exact_quote_recovered':sum(r['exact_quote_recovered'] for r in subset),
                        'context_only_recovered':sum(r['exact_quote_recovered'] for r in subset if r['lead_class']=='context_only')})
    with (out/'fairness_summary.csv').open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)
    return agg

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--corpus',required=True,type=Path);p.add_argument('--pack',default=ROOT/'E1_Source_Selector_v04',type=Path);p.add_argument('--out',default=ROOT/'results_dev',type=Path);p.add_argument('--max-units',type=int,default=10);p.add_argument('--per-source',type=int,default=3)
    a=p.parse_args();print(json.dumps(run(a.corpus,a.pack,a.out,max_units=a.max_units,max_per_source=a.per_source),indent=2))
