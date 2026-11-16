#!/usr/bin/env python3
"""Experimental source-scoped span selector, with exact byte-preserving offsets.

All source identities come from frozen first-stage hits. No external fetch, no
claim validity or document publication-date inference. Exploratory dev anchors.
"""
from __future__ import annotations
import csv
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

from . import lexical, selector

# Boundaries are *only* at whitespace; never fabricate characters.
SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])|\n[ \t]*\n+',re.UNICODE)
MAX_SPAN_CHARS=620
OVERLAP_CHARS=110
MIN_SPAN_CHARS=20


def clean_token(t):
    # Moderate suffix normalization; no fuzzy edit-distance expansion.
    return lexical.term_root(lexical.norm(t))


def raw_span_windows(text:str):
    """Return stable (start,end) windows; span text is always text[start:end]."""
    start=0
    parts=[]
    for match in SPLIT.finditer(text):
        end=match.start()
        if end>start and text[start:end].strip():parts.append((start,end))
        start=match.end()
    if text[start:].strip():parts.append((start,len(text)))
    result=[]
    for a,b in parts:
        if b-a<=MAX_SPAN_CHARS:
            result.append((a,b));continue
        pos=a
        while pos<b:
            stop=min(pos+MAX_SPAN_CHARS,b)
            if stop<b:
                # Use actual whitespace as a safe cut, but do not drop any span characters.
                for cut in range(stop,max(pos+MAX_SPAN_CHARS//2,pos+1),-1):
                    if text[cut-1].isspace():stop=cut;break
            result.append((pos,stop))
            if stop>=b:break
            pos=max(pos+1,stop-OVERLAP_CHARS)
    # Very short paragraphs can still be evidential; do not discard on a 30-char rule.
    return [(a,b) for a,b in result if text[a:b].strip()]


def spans(pool:list[dict])->list[dict]:
    out=[]
    for u in pool:
        for a,b in raw_span_windows(u['text']):
            rec={**u,'text':u['text'][a:b], 'parent_unit_id':u['unit_id'],
                 'start_char':a,'end_char':b}
            rec['unit_id']=hashlib.sha256(f'{u["unit_id"]}:{a}:{b}'.encode()).hexdigest()
            out.append(rec)
    return out


def score_spans(units:list[dict],query:str,case:str,role:str=''):
    qtokens=lexical.tokenise(query+' '+role)
    qterms=[clean_token(t) for t in qtokens if t not in lexical.CITY_TERMS[case]]
    qtf=Counter(qterms)
    termdocs=[];doclens=[];df=Counter()
    for u in units:
        # German PDF line-hyphenation is normalized in the *scoring representation*
        # but source substring remains the unchanged original text.
        dehyph=re.sub(r'(?<=\w)-\s*\n\s*(?=\w)','',u['text'])
        tf=Counter(clean_token(t) for t in lexical.tokenise(dehyph))
        termdocs.append(tf);doclens.append(sum(tf.values()))
        df.update(tf)
    n=len(units)
    avg=sum(doclens)/n if n else 1
    # Precompute corpus DF once per query; never scan all documents per score.
    vocab=set(t for doc in termdocs for t in doc)
    equivalents={q:{t:(1. if t==q else .8) for t in vocab
                    if t==q or (len(q)>=5 and len(t)>=len(q)+3 and t.endswith(q))}
                 for q in qtf}
    idf={q:math.log1p((n-sum(any(t in d for t in eq)
                                for d in termdocs)+.5)/
                        (sum(any(t in d for t in eq) for d in termdocs)+.5))
         for q,eq in equivalents.items()}
    out=[]
    for u,tf,dl in zip(units,termdocs,doclens):
        value=0.
        for q,nq in qtf.items():
            f=sum(tf[t]*w for t,w in equivalents[q].items() if t in tf)
            if f:
                value+=nq*idf[q]*f*2.2/(f+1.2*(.25+.75*dl/max(1,avg)))
        if value>0:out.append((value,u))
    out.sort(key=lambda a:(-a[0],a[1]['unit_id']))
    return out


def selector_spans(pool:list[dict],task:dict,budget:int,role:bool=False):
    seq=spans(pool)
    rolehint=selector.ROLE_TERMS.get(task['task_type'],'') if role else ''
    rank=score_spans(seq,task['question_de'],task['case_id'],rolehint)
    # Strict finite context budget, including URL and locator overhead.
    selected,used=selector.select(rank,budget=budget,max_units=25,max_per_source=8)
    positions={u['unit_id']:u for u in seq}
    for x in selected:
        original=positions[x['unit_id']]
        x.update(parent_unit_id=original['parent_unit_id'],start_char=original['start_char'],
                 end_char=original['end_char'],source_hashes=original['raw_hashes'])
    return selected,used,positions


def anchor_from_spans(anchor:dict,selected:list[dict],spans_by_id:dict):
    ids=set(anchor['unit_ids'].split('|'))
    for i,x in enumerate(selected,1):
        s=spans_by_id[x['unit_id']]
        if s['parent_unit_id'] in ids and anchor['quote'] in s['text']:
            return i
    return None


def run(corpus:Path,tasks:Path,anchors:Path,l0:Path,l1:Path,l23:Path,
        out:Path,budgets=(4000,8000,16000)):
    units=selector.load_jsonl(corpus)
    byid,index=selector.make_index(units)
    task_table=selector.load_tasks(tasks)
    rr=selector.load_rankings(l0,l1,l23)
    with anchors.open(encoding='utf8',newline='') as f: anchorlist=list(csv.DictReader(f))
    output=[];lead=[];chosen=[]
    for stage,task_ranks in rr.items():
        for task_id,task in sorted(task_table.items()):
            pool=selector.source_pool(task_ranks[task_id]['results'],byid,index,selector.CASE_AGS[task['case_id']])
            v=spans(pool)
            allspans={x['unit_id']:x for x in v}
            for role in (False,True):
                rolehint=selector.ROLE_TERMS.get(task['task_type'],'') if role else ''
                ranked=score_spans(v,task['question_de'],task['case_id'],rolehint)
                typ='spans_role' if role else 'spans_lexical'
                for budget in budgets:
                    sel,used=selector.select(ranked,budget=budget,max_units=25,max_per_source=8)
                    assert used<=budget
                    assert all(x['acquisition_ags']==selector.CASE_AGS[task['case_id']] for x in sel)
                    output.append({'first_stage':stage,'task_id':task_id,'policy':typ,'budget_chars':budget,
                                   'units_selected':len(sel),'budget_used_chars':used,
                                   'source_body_pool_units':len(pool),
                                   'source_body_pool_chars':sum(len(u['text']) for u in pool),
                                   'extract_spans_in_pool':len(allspans),
                                   'attribution_status':'NOT_ASSESSED', 'answer_status':'NOT_ASSESSED'})
                    for i,x in enumerate(sel,1):
                        original=allspans[x['unit_id']]
                        chosen.append({'first_stage':stage,'task_id':task_id,'policy':typ,'budget_chars':budget,
                                       'rank':i,**x,'parent_unit_id':original['parent_unit_id'],
                                       'start_char':original['start_char'],'end_char':original['end_char']})
                    for a in anchorlist:
                        if a['task_id']!=task_id:continue
                        parentids=set(a['unit_ids'].split('|'))
                        match=anchor_from_spans(a,sel,allspans)
                        lead.append({'first_stage':stage,'task_id':task_id,'policy':typ,
                                     'budget_chars':budget,'anchor_id':a['anchor_id'],
                                     'lead_class':a['lead_class'],'exact_quote_in_selected_span':int(match is not None),
                                     'matched_span_rank':match or '',
                                     'source_body_reachable':int(any(x['unit_id'] in parentids for x in pool)),
                                     'claim_verified':'NO'})
    out.mkdir(parents=True,exist_ok=True)
    def write_csv(fname,rs):
        with (out/fname).open('w',encoding='utf8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rs[0]));writer.writeheader();writer.writerows(rs)
    write_csv('span_ledger.csv',output)
    write_csv('span_selected.csv',chosen)
    write_csv('span_leads.csv',lead)
    return output,lead

if __name__=='__main__':
    ap=__import__('argparse').ArgumentParser()
    for name in ('corpus','tasks','anchors','l0','l1','l23','out'):
        ap.add_argument('--'+name,required=True,type=Path)
    args=ap.parse_args()
    output,leads=run(**vars(args))
    for stage in ('L0','L1','L2','L3'):
        for policy in ('spans_lexical','spans_role'):
            x=[a for a in leads if a['first_stage']==stage and a['policy']==policy and a['budget_chars']==8000]
            print(stage,policy,'anchor hits',sum(r['exact_quote_in_selected_span'] for r in x),'/',len(x))
