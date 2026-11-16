#!/usr/bin/env python3
"""E1 post-hoc *diagnostic* lexical ablations; standard library, offline, no gold metrics.

L2 = original exact-token BM25 with already-filtered municipality name removed.
L3 = same city exclusion + light German suffix normalization and narrow compound suffix
     retrieval, including at most one character of noise in a longer compound.
Neither confirms factual relevance or territorial applicability.
"""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, math, re, unicodedata
from pathlib import Path

STOP = set('welche welcher welches welchem was wie sind ist war hat haben wurde wurden werden einer einem einen eine ein der die das des dem den und oder zum zur von für im in am an auf über unter mit als bei zum zu bis zwischen gilt gelten aktuell dokumentiert dokumentierte dokumentierten öffentlich kommune gemeinde stadt untersuchungsstichtag beziehungsweise'.split())
TOKEN = re.compile(r'[a-zäöüß0-9]+', re.IGNORECASE)
TASK_TO_MUNI={'A':'09564000','B':'09190118'}
CITY_TERMS={'A':{'nürnberg','nurnberg','nuernberg'},'B':{'burggen'}}
ENDINGS=('ern','em','es','er','en','e','n','s')

def norm(t:str)->str:
    return ''.join(c for c in unicodedata.normalize('NFKD',t.casefold()) if not unicodedata.combining(c))

def tokenise(text:str)->list[str]:
    # Preserve the original L0 exact-token semantics; L3 normalizes explicitly.
    return [t for t in TOKEN.findall(text.casefold()) if t not in STOP and len(t)>=2]

def term_root(t:str)->str:
    """Conservative suffix rules (not full German Snowball stemming)."""
    for _ in range(2):
        suffix=next((x for x in ENDINGS if t.endswith(x) and len(t)-len(x)>=6),None)
        if suffix is None:break
        t=t[:-len(suffix)]
    return t

def edit_le1(a:str,b:str)->bool:
    """True iff strings are within one insertion/deletion/substitution."""
    if abs(len(a)-len(b))>1:return False
    if a==b:return True
    if len(a)==len(b):return sum(x!=y for x,y in zip(a,b))<=1
    if len(a)>len(b):a,b=b,a
    i=j=err=0
    while i<len(a) and j<len(b):
        if a[i]==b[j]:i+=1;j+=1
        else:
            err+=1;j+=1
            if err>1:return False
    return True

def extension_match(query_root:str,document_root:str)->float:
    """Longer document compound's final component equals query root (or one typo).

    Direction is intentional: 'Wärmeplanung' may match 'KommunaleWärmeplanung',
    but MUST NOT match the generic word 'Planung'.
    """
    if query_root==document_root:return 1.
    if len(query_root)<8 or len(document_root)<len(query_root)+3:return 0.
    if document_root.endswith(query_root):return .85
    for n in (len(query_root)-1,len(query_root),len(query_root)+1):
        if n>0 and edit_le1(query_root,document_root[-n:]):return .6
    return 0.

def rank(candidates:list[dict],query:str,case:str,method:str)->tuple[list[tuple[float,dict]],dict]:
    city=CITY_TERMS[case]
    qtokens=[t for t in tokenise(query) if t not in city]
    if method=='L3':qtokens=[term_root(norm(t)) for t in qtokens]
    qtf=collections.Counter(qtokens)
    termdocs=[collections.Counter((term_root(norm(t)) if method=='L3' else t) for t in tokenise(u['text'])) for u in candidates]
    lengths=[sum(tf.values()) for tf in termdocs]
    avglen=(sum(lengths)/len(lengths)) if lengths else 1
    vocab=set().union(*(set(doc) for doc in termdocs)) if termdocs else set()
    if method=='L3':
        expand={q:{t:extension_match(q,t) for t in vocab if extension_match(q,t)>0} for q in qtf}
    else:
        expand={q:({q:1.} if q in vocab else {}) for q in qtf}
    df={q:sum(any(t in d for t in alternatives) for d in termdocs) for q,alternatives in expand.items()}
    scores=[]
    for u,tf,doclen in zip(candidates,termdocs,lengths):
        s=0.
        for q,qty in qtf.items():
            opts=expand[q]; matches=[(t,w) for t,w in opts.items() if t in tf]
            if not matches:continue
            freq=sum(tf[t]*w for t,w in matches)
            idf=math.log1p((len(candidates)-df[q]+.5)/(df[q]+.5))
            s+=qty*idf*freq*2.2/(freq+1.2*(1-.75+.75*doclen/avglen))
        if s>0:scores.append((s,u))
    scores.sort(key=lambda row:(-row[0],row[1]['unit_id']))
    return scores,{'tokens':list(qtf),'covered_terms':sum(v>0 for v in df.values()),'domain_terms':len(df),'df':df,'expansions':{q:len(v) for q,v in expand.items()}}

def get_top(scored,k=10):
    selected=[]; byurl=collections.Counter()
    for score,u in scored:
        if byurl[u['source_url']]>=2:continue
        byurl[u['source_url']]+=1
        selected.append({'rank':len(selected)+1,'unit_id':u['unit_id'],'source_url':u['source_url'],'score':round(score,7),'page_ref':u['page_ref'],'heading_context':u['heading_context'],'excerpt':u['text'][:350],'acquisition_ags':u['acquisition_ags'],'provenance_runs':sorted(set(o['run'] for o in u['origins'])),'territorial_scope':'not_adjudicated'})
        if len(selected)>=k:break
    return selected

def run(corpus:Path,tasks:Path,out:Path):
    rows=[json.loads(line) for line in corpus.open(encoding='utf8') if line.strip()]
    questions=list(csv.DictReader(tasks.open(encoding='utf-8-sig')))
    if len(rows)!=1779:raise ValueError('Unexpected corpus count; this experiment is frozen to E1 corpus')
    if len(questions)!=8:raise ValueError('Need eight E0a questions')
    out.mkdir(parents=True,exist_ok=True)
    result=[];metrics=[]
    for method in ('L2','L3'):
        for q in questions:
            ags=TASK_TO_MUNI[q['case_id']]
            candidates=[u for u in rows if u['acquisition_ags']==ags]
            scored,diag=rank(candidates,q['question_de'],q['case_id'],method)
            top=get_top(scored)
            record={'variant':method,'task_id':q['task_id'],'question_de':q['question_de'],'count_candidates':len(candidates),'count_positive_score':len(scored),'term_diagnostic':diag,'results':top}
            result.append(record)
            metrics.append({'variant':method,'task_id':q['task_id'],'candidate_units':len(candidates),'positive_score_units':len(scored),'query_terms':diag['domain_terms'],'covered_terms':diag['covered_terms'],'top1_url':top[0]['source_url'] if top else '','top1_text':top[0]['excerpt'].replace('\n',' ')[:100] if top else '','returned':len(top)})
    with (out/'rankings.jsonl').open('w',encoding='utf8') as fp:
        for x in result:fp.write(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n')
    with (out/'query_coverage.csv').open('w',encoding='utf8',newline='') as fp:
        w=csv.DictWriter(fp,fieldnames=list(metrics[0]));w.writeheader();w.writerows(metrics)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps({'status':'posthoc_exploratory','corpus_sha256':sha(corpus),'tasks_sha256':sha(tasks),'variants':['L2','L3'],'no_gold_standard':True,'city_removed_due_to_pre-filtered_acquisition_ags':True,'result_sha256':sha(out/'rankings.jsonl')},indent=2)+'\n')
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',type=Path,required=True);ap.add_argument('--tasks',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();rs=run(a.corpus,a.tasks,a.out)
    for x in rs:
        t=x['term_diagnostic']; print(x['variant'],x['task_id'],'positive',x['count_positive_score'],'coverage',t['covered_terms'],'/',t['domain_terms'],'rank1',x['results'][0]['source_url'].rsplit('/',1)[-1] if x['results'] else '[ABSTAIN]')
if __name__=='__main__':main()
