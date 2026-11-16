#!/usr/bin/env python3
"""E1-v0.4: fixed-first-stage, source-version-bounded evidence-passage selection.

Experimental diagnostic, NOT gold relevance/faithfulness evaluation. Stdlib only.
No network, LLM inference, index mutation, or external embeddings.

This module records the *passages offered* to a hypothetical downstream system;
it never confirms that a quotation legally/factually supports the task.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from lexical import rank

CASE_AGS = {"A": "09564000", "B": "09190118"}
ROLE_TERMS = {
    # These are generic query-side lexical hints, not source-specific gold labels.
    "formal_target": "beschloss beschlossen beschluss stadtrat ziel klimaziel emissionen reduktion beschlossen", 
    "heat_planning": "wärmeplanung wärmeplan kommunal stadtrat beschlossen verabschiedet beschluss beschlossen",
    "actor_roles": "zuständig betreiber netzbetreiber wärmenetz fernwärmenetz verantwortlich versorgung gmbh",
    "temporal_change": "verändert geändert maßnahme datum beschluss fortschreibung umgesetzt geplant seit zuvor aktualisiert",
}


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def load_jsonl(p:Path)->list[dict]:
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]


def load_tasks(p:Path)->dict[str,dict]:
    with p.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    out={r['task_id']:r for r in rows}
    if len(rows)!=8 or len(out)!=8 or {v['case_id'] for v in out.values()}!={'A','B'}:
        raise ValueError('Exactly eight unchanged E0a tasks are required')
    if any(r['cutoff_date']!='2026-10-19' for r in rows):
        raise ValueError('Frozen task cutoff mismatch')
    return out


def load_rankings(l0:Path,l1:Path,l23:Path)->dict[str,dict[str,dict]]:
    rr={}
    for name,path in (('L0',l0),('L1',l1)):
        rows=load_jsonl(path)
        rr[name]={r['task_id']:r for r in rows}
        if len(rr[name])!=8:raise ValueError('Bad ranking table '+name)
    rows=load_jsonl(l23)
    for name in ('L2','L3'):
        vv=[r for r in rows if r['variant']==name]
        rr[name]={r['task_id']:r for r in vv}
        if len(rr[name])!=8:raise ValueError('Bad ranking table '+name)
    return rr


def version_keys(unit:dict)->set[tuple[str,str,str]]:
    """Source *body* identity, not hostname or URL alone."""
    h=unit.get('raw_hashes',[])
    if not h: raise ValueError('Cannot establish versioned source boundary')
    return {(unit['acquisition_ags'],unit['source_url'],x) for x in h}


def source_pool(top: list[dict], by_id:dict, version_index:dict, expected_ags:str)->list[dict]:
    keys=set()
    for h in top:
        uid=h['unit_id']
        if uid not in by_id:raise ValueError('First-stage hit missing')
        u=by_id[uid]
        if u['acquisition_ags']!=expected_ags or h['source_url']!=u['source_url']:
            raise ValueError('First-stage provenance mismatch')
        keys.update(version_keys(u))
    candidate={v['unit_id']:v for key in keys for v in version_index.get(key,[])}
    if any(v['acquisition_ags']!=expected_ags or not version_keys(v)&keys for v in candidate.values()):
        raise AssertionError('Source boundary escaped')
    # Narrow lineage: a passage may exist in multiple crawl body versions, but
    # only versions opened by first-stage hits can be cited by this comparison.
    scoped=[]
    for uid in sorted(candidate):
        item=candidate[uid]
        allowed=[h for h in item['raw_hashes']
                 if (item['acquisition_ags'],item['source_url'],h) in keys]
        if not allowed:raise AssertionError('Candidate without authorized source version')
        scoped.append({**item,'raw_hashes':sorted(allowed)})
    return scoped


def make_index(units:list[dict]):
    by_id={u['unit_id']:u for u in units}
    if len(by_id)!=len(units):raise ValueError('Duplicate unit ID')
    idx=defaultdict(list)
    for u in units:
        for key in version_keys(u):idx[key].append(u)
    return by_id,idx


def budget_cost(u:dict)->int:
    # Explicit, deterministic proxy for context size; not model tokenizer tokens.
    return len(u['text']) + len(u.get('heading_context','')) + len(u['source_url']) + len(u.get('page_ref','')) + 64


def select(scored:list[tuple[float,dict]], *, budget:int, max_units:int=12, max_per_source:int=3)->tuple[list[dict],int]:
    if budget < 1:raise ValueError('Budget must be positive')
    seen=set(); per_source=Counter(); picked=[]; used=0
    for score,u in scored:
        uid=u['unit_id']
        if uid in seen or score<=0:continue
        seen.add(uid)
        if per_source[u['source_url']]>=max_per_source:continue
        cost=budget_cost(u)
        # Prefix selection ensures that increasing the budget cannot evict a passage.
        if used+cost>budget:break
        picked.append({'unit_id':uid,'source_url':u['source_url'], 'raw_hashes':u['raw_hashes'],
                       'acquisition_ags':u['acquisition_ags'], 'passage_chars':len(u['text']),
                       'cost_chars':cost,'source_role':'not_adjudicated','claim_supported':'NOT_ASSESSED'})
        used+=cost;per_source[u['source_url']]+=1
        if len(picked)>=max_units:break
    return picked,used


def rerank(pool:list[dict], task:dict, policy:str)->list[tuple[float,dict]]:
    if policy=='scoped_exact':mode='L2'; q=task['question_de']
    elif policy=='scoped_morph':mode='L3';q=task['question_de']
    elif policy=='scoped_role':
        mode='L3';q=task['question_de']+' '+ROLE_TERMS.get(task['task_type'], '')
    else:raise ValueError('Unsupported policy')
    result,_=rank(pool,q,task['case_id'],mode)
    return result


def first_stage(top:list[dict],by_id:dict)->list[tuple[float,dict]]:
    return [(float(len(top)-i),by_id[h['unit_id']]) for i,h in enumerate(top)]


def anchor_assessment(a:dict,selected:list[dict],pool:list[dict],by_id:dict)->dict:
    uid=set(a['unit_ids'].split('|'))
    if not uid or not uid <= set(by_id):raise ValueError('Unresolved diagnostic anchor')
    for u_id in uid:
        u=by_id[u_id]
        if (u['source_url']!=a['source_url'] or u['acquisition_ags']!=a['acquisition_ags']
            or a['quote'] not in u['text']):
            raise ValueError('Diagnostic anchor provenance/quote mismatch')
    selected_ids=[s['unit_id'] for s in selected]
    pool_ids={u['unit_id'] for u in pool}
    # Quote may be recorded in identical passage units; count any exact declared target.
    return {'anchor_id':a['anchor_id'],'lead_class':a['lead_class'],
            'exact_passage_selected':int(bool(uid & set(selected_ids))),
            'anchor_source_version_reachable':int(bool(uid & pool_ids)),
            'selected_rank':next((i+1 for i,x in enumerate(selected_ids) if x in uid), ''),
            'full_task_verified':'NO','anchor_is_gold':'NO'}


def execute(corpus:Path,tasks:Path,anchors:Path,l0:Path,l1:Path,l23:Path,
            out:Path,budgets:list[int]=[4000,8000,16000]):
    units=load_jsonl(corpus)
    if len(units)!=1779:raise ValueError('Unexpected frozen corpus size')
    by_id,version_index=make_index(units)
    task_table=load_tasks(tasks)
    rankings=load_rankings(l0,l1,l23)
    with anchors.open(encoding='utf-8',newline='') as f:diagnostics=list(csv.DictReader(f))
    if len(diagnostics)!=9:raise ValueError('Changed known-lead diagnostic set')
    diagnostics_by_task=defaultdict(list)
    for a in diagnostics:
        if a['task_id'] not in task_table:raise ValueError('Unknown anchor task')
        diagnostics_by_task[a['task_id']].append(a)
    budget_rows=[]; anchor_rows=[]; selection_rows=[]
    for first_stage_method, tasks_ranks in rankings.items():
        for task_id,task in sorted(task_table.items()):
            top=tasks_ranks[task_id]['results']
            if len(top)>10 or len({v['unit_id'] for v in top})!=len(top):
                raise ValueError('Invalid first-stage ranking')
            ags=CASE_AGS[task['case_id']]
            pool=source_pool(top,by_id,version_index,ags)
            # Stage-1 source boundary frozen before reranking by policies.
            keys={key for h in top for key in version_keys(by_id[h['unit_id']])}
            assert set(version_keys(u).pop() for u in pool if len(version_keys(u))==1) <= keys
            ranking_policy={
                'direct':first_stage(top,by_id),
                'scoped_exact':rerank(pool,task,'scoped_exact'),
                'scoped_morph':rerank(pool,task,'scoped_morph'),
                'scoped_role':rerank(pool,task,'scoped_role'),
            }
            for budget in budgets:
                for policy,scorelist in ranking_policy.items():
                    # The direct baseline follows original top10 order, whose max-2 source
                    # quota was already applied. The bounded rerank has max-3 per URL.
                    selected,used=select(scorelist,budget=budget,max_units=12 if policy!='direct' else 10,
                                         max_per_source=2 if policy=='direct' else 3)
                    selected_ids={x['unit_id'] for x in selected}
                    if not selected_ids <= {x['unit_id'] for x in pool}:
                        raise AssertionError('Selected an out-of-scope passage')
                    if used>budget:raise AssertionError('Budget exceeded')
                    row={'first_stage':first_stage_method,'task_id':task_id,'policy':policy,
                         'budget_chars':budget,'units_selected':len(selected), 'budget_used_chars':used,
                         'source_body_pool_units':len(pool),
                         'source_body_pool_chars':sum(len(u['text']) for u in pool),
                         'first_stage_count':len(top),
                         'distinct_sources_selected':len({u['source_url'] for u in selected}),
                         'attribution_status':'NOT_ASSESSED','answer_status':'NOT_ASSESSED'}
                    budget_rows.append(row)
                    for i,u in enumerate(selected):
                        selection_rows.append({'first_stage':first_stage_method,'task_id':task_id,'policy':policy,
                                               'budget_chars':budget,'rank':i+1,**u})
                    for a in diagnostics_by_task[task_id]:
                        ar=anchor_assessment(a,selected,pool,by_id)
                        anchor_rows.append({'first_stage':first_stage_method,'task_id':task_id,'policy':policy,
                                            'budget_chars':budget,**ar})
    out.mkdir(parents=True,exist_ok=True)
    def csvfile(name:str,rows:list[dict]):
        with (out/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    csvfile('budget_ledger.csv',budget_rows)
    csvfile('selected_passages.csv',selection_rows)
    csvfile('lead_diagnostics.csv',anchor_rows)
    main_budget=8000
    summary=[]
    for stage in rankings:
        for policy in ('direct','scoped_exact','scoped_morph','scoped_role'):
            x=[r for r in anchor_rows if r['first_stage']==stage and r['policy']==policy and r['budget_chars']==main_budget]
            rows=[r for r in budget_rows if r['first_stage']==stage and r['policy']==policy and r['budget_chars']==main_budget]
            summary.append({'first_stage':stage,'policy':policy,'budget_chars':main_budget,
                           'anchored_passages_selected':sum(r['exact_passage_selected'] for r in x),
                           'anchors_total':len(x),
                           'already_source_reachable':sum(r['anchor_source_version_reachable'] for r in x),
                           'context_negative_passages_selected':sum(r['exact_passage_selected'] for r in x if r['lead_class'].startswith('negative')),
                           'mean_used_chars':round(sum(r['budget_used_chars'] for r in rows)/len(rows),1),
                           'tasks_with_zero_passages':sum(r['units_selected']==0 for r in rows),
                           'unverified_relevance':'YES'})
    csvfile('summary_8000.csv',summary)
    meta={'experiment':'E1-v0.4 budgeted source-version bounded selector',
          'status':'EXPLORATORY; POSTHOC DEV LEADS; NOT INDEPENDENT TEST',
          'frozen_tasks':'2026-10-19','units':len(units),'first_stage_methods':list(rankings),
          'selection_policies':['direct','scoped_exact','scoped_morph','scoped_role'],
          'budgets_chars':budgets,
          'data_sha256':{key:sha(p) for key,p in [('corpus',corpus),('tasks',tasks),('anchors',anchors),
                      ('L0',l0),('L1',l1),('L2_L3',l23)]},
          'outputs_sha256':{name:sha(out/name) for name in ('budget_ledger.csv','selected_passages.csv','lead_diagnostics.csv','summary_8000.csv')},
          'guardrails':['fixed_first_stage_per_comparison','exact_source_url_AND_body_sha_AND_acquisition_ags',
                        'no_grading_by_location_or_keyword','no_claim_or_territory_verified',
                        'no_oracle_leakage_into_scoring','character_cost_proxy_not_true_llm_tokens',
                        'development_anchors_not_independent']}
    (out/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for arg in ('corpus','tasks','anchors','l0','l1','l23','out'):ap.add_argument('--'+arg,required=True,type=Path)
    args=ap.parse_args()
    print(json.dumps(execute(**vars(args)),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
