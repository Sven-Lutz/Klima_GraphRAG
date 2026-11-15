"""Offline, unlabelled E2 retrieval; reuse immutable ZIP algorithms. No network."""
from pathlib import Path
import argparse, collections, csv, hashlib, json, re, sys, zipfile, platform
from html.parser import HTMLParser
class VisibleText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ("script","style"):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)
from urllib.parse import urlsplit
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]
ACQ=BASE.parent/'e2_v11_acquisition_20261024'
sys.path.insert(0,str(BASE/'kit'))
import evaluate_e2 as ev
import provenance_contract as pc
lexical,selector=ev.lexical,ev.selector

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def jwrite(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
def jlwrite(p,rows):p.write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in rows))
def csvwrite(p,rows):
    with p.open('w',newline='') as f:
        if rows:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def norm(s):return ' '.join(s.casefold().split())
def inventory(root):return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def groups(units,key):
    d=collections.defaultdict(list)
    for u in units:d[key(u)].append(u['unit_id'])
    return [v for v in d.values() if len(v)>1]
def near_pairs(units):
    shingles=[];post=collections.defaultdict(set);pairs=set()
    for i,u in enumerate(units):
        t=re.findall(r'\w+',norm(u['text']))
        s=set(zip(*(t[k:] for k in range(5)))) if len(u['text'])>=100 else set()
        shingles.append(s)
        for sh in s:
            for j in post[sh]:pairs.add((j,i))
            post[sh].add(i)
    result=[]
    for i,j in sorted(pairs):
        a,b=shingles[i],shingles[j]
        if norm(units[i]['text'])==norm(units[j]['text']):continue
        if min(len(a),len(b))/max(len(a),len(b))<.9:continue
        score=len(a&b)/len(a|b)
        if score>=.9:result.append({'a':units[i]['unit_id'],'b':units[j]['unit_id'],'jaccard':score})
    return result

def main(out,e1zip):
    if out.exists():raise FileExistsError('Use a fresh output path')
    before=inventory(ACQ)
    checks=[]
    for line in (ACQ/'SHA256SUMS').read_text().splitlines():
        h,name=line.split('  ',1);assert sha(ACQ/name)==h,name;checks.append(name)
    ex=ACQ/'export';units=ev.read_jsonl(ex/'corpus.jsonl');catalog=ev.read_jsonl(ex/'catalog.jsonl')
    tasks=ev.read_csv(ACQ/'protocol/frozen/tasks_frozen.csv');cases=ev.read_csv(ACQ/'protocol/frozen/cases_frozen.csv')
    frozen=json.loads((ACQ/'protocol/frozen/freeze_manifest.json').read_text())
    for k in ('case','task'):assert sha(ACQ/f'protocol/frozen/{k}s_frozen.csv')==frozen[k+'_file_sha256']
    for p in (ACQ/'protocol').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            assert p.read_bytes()==(BASE/'kit'/p.relative_to(ACQ/'protocol')).read_bytes(),p
    ev.validate_corpus(units,cases,tasks);keys=pc.load_catalog(catalog,ex/'raw')
    for u in units:assert selector.version_keys(u)<=keys
    for p in (ex/'raw').iterdir():assert sha(p)==p.name
    out.mkdir(parents=True)
    # Existing validators write reports into export; redirect ONLY report destinations.
    for script,old,new in [('validate.py',"(out/'validation.json')",'acquisition_validation.json'),('reparse.py',"(o/'reparse_validation.json')",'reparse_validation.json')]:
        text=(ACQ/script).read_text();assert text.count(old)==1
        text=text.replace(old,'Path('+repr(str(out/new))+')')
        exec(compile(text,str(ACQ/script),'exec'),{'__file__':str(ACQ/script),'__name__':'__audit__'})
    provenance=[]
    cat_by_key={(c['ags'],c['url'],c['body_sha256']):c for c in catalog}
    for c in catalog:
        for p in c['institutional_provenance']:
            k=(c['ags'],p['url'],p['body_sha256']);assert k in keys
            evidence=' '.join(u['text'] for u in units if k in selector.version_keys(u))
            literal=norm(p['institution_literal']) in norm(evidence)
            parser=VisibleText();parser.feed((ex/'raw'/p['body_sha256']).read_bytes().decode('utf-8',errors='replace'))
            raw_visible=' '.join(parser.parts)
            assert norm(p['institution_literal']) in norm(raw_visible),(k,p['institution_literal'])
            assert norm(p['context']) in norm(raw_visible),(k,'publisher context mismatch')
            assert urlsplit(c['url']).hostname==urlsplit(p['url']).hostname
        provenance.append({'ags':c['ags'],'url':c['url'],'body_sha256':c['body_sha256'],
          'institutional_self_identification_verified':True,'institution_literal_in_exported_imprint':literal,'territorial_scope':c['territorial_scope'],
          'document_date':c['document_date'],'cutoff_eligibility':c['cutoff_eligibility'],
          'source_type':c['source_type'],'discovered_from':c['discovered_from']})
    exact=groups(units,lambda u:u['text']);normalized=groups(units,lambda u:norm(u['text']))
    near=near_pairs(units)
    with zipfile.ZipFile(e1zip) as z:e1=[json.loads(s) for s in z.read('corpus.jsonl').decode().splitlines() if s.strip()]
    def values(rows,key):return {v for u in rows for v in (u[key] if isinstance(u[key],list) else [u[key]])}
    overlap={k:sorted(values(units,k)&values(e1,k)) for k in ['acquisition_ags','source_url','raw_hashes']}
    oldtext={norm(u['text']) for u in e1}
    textoverlap=[u['unit_id'] for u in units if norm(u['text']) in oldtext]
    anchors=ev.read_csv(BASE.parent/'e1_selector/fixtures/anchors_dev.csv')
    quoteoverlap=[{'anchor_id':a['anchor_id'],'unit_id':u['unit_id']} for a in anchors for u in units if a['quote'] in u['text']]
    gate=json.loads((ACQ/'metadata_gate.json').read_text())
    assert not {c['ags'] for c in cases}&set(frozen['exclusion_ags_sorted'])
    assert not gate['overlap']
    # Metadata adapter, with explicit identity/text preservation assertions.
    adapted=[{**u,'heading_context':u.get('heading_context') or '', 'page_ref':u.get('page_ref') or '',
              'origins':[{**o,'run':o.get('run','e2_v11_acquisition_20261024')} for o in u['origins']]} for u in units]
    for a,b in zip(adapted,units):
        for k in ['text','unit_id','source_url','acquisition_ags','raw_hashes']:assert a[k]==b[k]
    jwrite(out/'input_lock.json',{'status':'UNLABELLED_RETRIEVAL_DIAGNOSTIC','not_a_prospective_Q_seal':True,
      'acquisition_inventory_sha256':before,'analysis_code_sha256':sha(Path(__file__)),
      'analysis_plan_sha256':sha(BASE/'ANALYSIS_PLAN.md'),'kit_inventory_sha256':inventory(BASE/'kit'),
      'e1_overlap_only_zip_sha256':sha(e1zip),'python':platform.python_version(),
      'adapter':{'null_heading_to_empty':sum(u['heading_context'] is None for u in units),
                 'null_page_to_empty':sum(u['page_ref'] is None for u in units),'origins_run_added':sum('run' not in o for u in units for o in u['origins'])},
      'budget':8000,'max_units':10,'max_per_url':3,'stage1_max_per_url':2,'independent_Q_anchors':0})
    byid,ix=selector.make_index(adapted);bycase={c['case_id']:c for c in cases}
    rankings=[];passages=[];ledger=[];stage=[];requirements=[];term_details=[]
    slotnames={'formal_target':['formal_target','decision_body','decision_date','cutoff_validity','territorial_applicability'],
      'heat_planning':['documented_milestone_or_status','cutoff_validity','territorial_applicability'],
      'actor_roles':['actor','role','documented_actor_role_relationship','territorial_applicability','cutoff_validity'],
      'temporal_change':['same_measure_identity','earlier_dated_version','later_dated_version','documented_change','cutoff_validity','territorial_applicability']}
    for task in sorted(tasks,key=lambda t:t['task_id']):
        city=ev.city_parts(bycase[task['case_id']]['official_name']);lexical.CITY_TERMS['A']=city
        cand=[u for u in adapted if u['acquisition_ags']==task['ags']]
        ranked,terms=lexical.rank(cand,task['question_de'],'A','L2');hits=lexical.get_top(ranked,10)
        term_details.append({'task_id':task['task_id'],**terms})
        hitids={u['unit_id'] for u in hits};hitrank={u['unit_id']:u['rank'] for u in hits};first=[byid[h['unit_id']] for h in hits]
        pool=selector.source_pool(hits,byid,ix,task['ags']);allowed={k for u in first for k in selector.version_keys(u)}
        for rank,(score,u) in enumerate(ranked,1):rankings.append({'task_id':task['task_id'],'rank_before_url_quota':rank,'score':score,'unit_id':u['unit_id'],'source_url':u['source_url'],'admitted_top10':u['unit_id'] in hitids,'admitted_rank':hitrank.get(u['unit_id'])})
        stage.append({'task_id':task['task_id'],'task_type':task['task_type'],'ags':task['ags'],'candidate_units':len(cand),'positive_score_units':len(ranked),'first_stage_hits':len(hits),'source_versions':len(allowed),'source_pool_units':len(pool),'covered_query_terms':terms['covered_terms'],'query_terms':terms['domain_terms'],'term_coverage_is_evidence':False,'Q_anchors':0})
        policies=ev.selection_by_policy(pool,first,task,city,8000)
        for policy,res in policies.items():
            selected=res['originals'];assert res['used']<=8000 and len(selected)<=10
            assert max(collections.Counter(u['source_url'] for u in selected).values(),default=0)<=3
            for rank,(s,u) in enumerate(zip(res['selected'],selected),1):
                assert selector.version_keys(u)<=allowed
                parent=u.get('parent_unit_id',u['unit_id']);start=u.get('start_char',0);end=u.get('end_char',len(u['text']))
                assert byid[parent]['text'][start:end]==u['text']
                passages.append({'task_id':task['task_id'],'policy':policy,'rank':rank,'unit_id':u['unit_id'],
                  'parent_unit_id':parent,'start_char':start,'end_char':end,'source_url':u['source_url'],
                  'raw_hashes':u['raw_hashes'],'ags':u['acquisition_ags'],'text':u['text'],
                  'heading_context':u['heading_context'],'page_ref':u['page_ref'],'cost_chars':s['cost_chars'],
                  'claim_supported':'NOT_ASSESSED'})
            pos=collections.defaultdict(set)
            for u in selected:pos[u.get('parent_unit_id',u['unit_id'])].update(range(u.get('start_char',0),u.get('end_char',len(u['text']))))
            ledger.append({'task_id':task['task_id'],'ags':task['ags'],'task_type':task['task_type'],'policy':policy,
              'passages':len(selected),'parent_units':len(pos),'source_versions':len({k for u in selected for k in selector.version_keys(u)}),
              'cost_chars':res['used'],'text_chars':sum(len(u['text']) for u in selected),
              'overlap_chars_same_parent':sum(len(u['text']) for u in selected)-sum(len(p) for p in pos.values()),
              'duplicate_normalized_passages':len(selected)-len({norm(u['text']) for u in selected}),
              'unused_budget':8000-res['used'],'Q_recovery':None,'S_coverage':None,'V_coverage':None})
        for slot in slotnames[task['task_type']]:requirements.append({'task_id':task['task_id'],'requirement':slot,'label_status':'NOT_ASSESSED','evidence_coverage':None,'note':'Reporting decomposition of frozen question; not new gold labels'})
    summary=[]
    for p in ev.POLICIES:
        rows=[r for r in ledger if r['policy']==p]
        summary.append({'policy':p,'tasks':len(rows),'passages':sum(r['passages'] for r in rows),
          'mean_cost_chars':sum(r['cost_chars'] for r in rows)/len(rows),'min_cost_chars':min(r['cost_chars'] for r in rows),
          'max_cost_chars':max(r['cost_chars'] for r in rows),'empty_tasks':sum(not r['passages'] for r in rows),
          'duplicate_normalized_passages':sum(r['duplicate_normalized_passages'] for r in rows),
          'same_parent_overlap_chars':sum(r['overlap_chars_same_parent'] for r in rows),
          'Q_recovery':None,'quality_rank':None})
    jlwrite(out/'query_term_diagnostics.jsonl',term_details);jlwrite(out/'first_stage_rankings.jsonl',rankings);jlwrite(out/'selected_passages.jsonl',passages)
    csvwrite(out/'first_stage.csv',stage);csvwrite(out/'budget_ledger.csv',ledger);csvwrite(out/'summary.csv',summary)
    csvwrite(out/'requirements.csv',requirements);jlwrite(out/'provenance.jsonl',provenance)
    jwrite(out/'duplicates.json',{'exact_groups':exact,'normalized_groups':normalized,'near_pairs':near})
    jwrite(out/'overlap.json',{'E1_comparison_units':len(e1),'E1_shared_identifiers':overlap,'E1_normalized_text_shared_unit_ids':textoverlap,'E1_anchor_literal_matches':quoteoverlap,'team_metadata_gate':gate,'current_team_document_overlap':'NOT_ASSESSED: no complete local teammate raw-body inventory used','no_E1_labels_used_in_scoring':True})
    audit={'checksummed_files':len(checks),'export_files':len([k for k in before if k.startswith('export/')]),'raw_files':len(list((ex/'raw').iterdir())),
      'documents':len(catalog),'units':len(units),'units_by_ags':dict(collections.Counter(u['acquisition_ags'] for u in units)),
      'exact_duplicate_groups':len(exact),'exact_duplicate_excess':sum(len(g)-1 for g in exact),
      'normalized_duplicate_groups':len(normalized),'normalized_duplicate_excess':sum(len(g)-1 for g in normalized),'near_duplicate_pairs':len(near),
      'catalog_unique_body_hashes':len({c['body_sha256'] for c in catalog}),
      'institutional_imprint_not_in_export_documents':sum(not p['institution_literal_in_exported_imprint'] for p in provenance),'all_institutional_references_linked_and_literal_verified':True,'territory_validated_documents':0,'cutoff_validated_documents':0,
      'labelled_tasks':0,'unlabelled_tasks':16,'requirements_reporting_slots':len(requirements),
      'original_acquisition_unchanged':before==inventory(ACQ)}
    assert audit['original_acquisition_unchanged']
    jwrite(out/'audit.json',audit)
    jwrite(out/'output_manifest.json',{'status':'UNLABELLED_RETRIEVAL_DIAGNOSTIC_COMPLETE','outputs':inventory(out),'answer_quality':'NOT_ESTIMABLE','Q_recovery':'NOT_ESTIMABLE'})
    print(json.dumps({'audit':audit,'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--e1-zip',type=Path,required=True);args=ap.parse_args();main(args.out.resolve(),args.e1_zip)
