#!/usr/bin/env python3
"""E2 municipality selection from a previously published *official* selection frame.
Offline/stdlib only. Never reads municipal climate evidence or crawl documents.

Selection is a hash lottery, ordered by sha256(SEED|stratum|AGS).
Two per group in different Regierungsbezirke to avoid within-group geographic duplicates.
No rerandomizing in response to retrieval outcomes is allowed.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,sqlite3
from pathlib import Path

SEED='E2-2026-10-21-prereg-v1'  # Preserve initial hash ranking: no rerandomization
# E2 v1.1 pre-source amendment. All nine named municipalities in Sriveni's
# Balkonkraftwerk_Benchmark_Bayern.csv, plus prior E0a pilot Burggen.
TEAM_METADATA_EXCLUDE={
    '09161000', # Ingolstadt
    '09162000', # Muenchen
    '09362000', # Regensburg
    '09462000', # Bayreuth
    '09562000', # Erlangen
    '09563000', # Fuerth
    '09564000', # Nuernberg
    '09663000', # Wuerzburg
    '09761000', # Augsburg
}
EXCLUDE=TEAM_METADATA_EXCLUDE | {'09190118'}
CUTOFF='2026-10-19'

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()

def eligible(r,kind):
    if r['ags'] in EXCLUDE or (kind=='B' and r['eligible_B']!='1'):return False
    try:pop=int(r['population_2026_06_30'])
    except ValueError:return False
    # Originally A required >100k, but every such independent city overlaps
    # Sriveni's 9-municipality metadata. Revised A is all kreisfreie cities
    # with 40,000+ inhabitants; record this changed estimand in the amendment.
    if kind=='A':
        return r['is_kreisfrei_registry']=='1' and pop>=40000
    return 500<=pop<=4000

def pick(rows,kind,n=2):
    candidates=[r for r in rows if eligible(r,kind)]
    candidates.sort(key=lambda r:(hashlib.sha256((SEED+'|'+kind+'|'+r['ags']).encode()).hexdigest(),r['ags']))
    chosen=[]
    for r in candidates:
        if r['regierungsbezirk_official'] not in [v['regierungsbezirk_official'] for v in chosen]:chosen.append(r)
        if len(chosen)==n:break
    if len(chosen)!=n:raise RuntimeError(f'Cannot fill stratum {kind}')
    return chosen,candidates

def prepare(frame:Path,registry:Path,output:Path)->dict:
    with frame.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=2056 or len(set(r['ags'] for r in rows))!=2056:raise ValueError('Official selection frame changed')
    conn=sqlite3.connect(f'file:{registry.resolve()}?mode=ro',uri=True)
    selection=[];alternates={}
    for kind in ('A','B'):
        chosen,candidates=pick(rows,kind)
        alternates[kind]=[{'ags':r['ags'],'official_name':r['official_name'], 'selection_rank':i+1,
             'rb':r['regierungsbezirk_official']}
            for i,r in enumerate(candidates) if r['ags'] not in {x['ags'] for x in chosen}][:15]
        for j,r in enumerate(chosen,1):
            home=conn.execute('select homepage_url,allowed_domains,source from municipalities where ags=?',(r['ags'],)).fetchone()
            if not home:raise ValueError('Registry AGS missing: '+r['ags'])
            if not (home[0] or '').startswith(('http://','https://')):raise ValueError('Missing homepage for '+r['ags'])
            selection.append({'case_id':f'E2-{kind}{j}','stratum':kind,'ags':r['ags'],
                 'official_name':r['official_name'],'population_2026_06_30':int(r['population_2026_06_30']),
                 'regierungsbezirk':r['regierungsbezirk_official'],'seed_homepage_url':home[0],
                 'registry_allowed_domains':home[1], 'seed_selection_sha256':hashlib.sha256((SEED+'|'+kind+'|'+r['ags']).encode()).hexdigest(),
                 'site_verified_official':False})
    conn.close()
    output.mkdir(parents=True,exist_ok=True)
    with (output/'cases_frozen.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(selection[0]));w.writeheader();w.writerows(selection)
    templates=[('T1','formal_target','Welche formal beschlossenen kommunalen Klimaschutzziele gelten für {name} zum Stichtag, und durch welches Gremium sowie wann wurden sie beschlossen?'),
               ('T2','heat_planning','Welcher dokumentierte Meilenstein bzw. Status der kommunalen Wärmeplanung gilt für {name} zum Stichtag?'),
               ('T3','actor_roles','Welche institutionellen Zuständigkeiten und Rollen bei Wärmeversorgung beziehungsweise Wärmenetzen sind für {name} belegbar?'),
               ('T4','temporal_change','Welche Veränderung eines konkret identifizierten kommunalen Klimaziels oder einer Maßnahme ist in {name} zwischen zwei datierten Quellen nachvollziehbar?')]
    qs=[]
    for r in selection:
        shortname=r['official_name'].split(',')[0].split(' /')[0].strip()
        for id,typ,fmt in templates:
            qs.append({'task_id':r['case_id']+'-'+id,'case_id':r['case_id'],'ags':r['ags'],
                       'task_type':typ,'question_de':fmt.format(name=shortname),'cutoff_date':CUTOFF,
                       'ground_truth':'NOT_ESTABLISHED','label_source':'NOT_COLLECTED'})
    with (output/'tasks_frozen.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(qs[0]));w.writeheader();w.writerows(qs)
    manifest={'version':'E2-prospective-prereg-1.1-metadata-amendment','sampling_seed':SEED,'selection_method':'unchanged v1.0 sha256 ascending seed, distinct RB per stratum; A kreisfreie pop >=40000',
              'amendment_trigger':'9-city Sriveni benchmark municipality metadata overlap before any new-source acquisition',
              'amendment_scope':'A threshold >=100000 revised to kreisfrei >=40000; B unchanged',
              'original_v1_case_sha256':'29644f5108c57775d3a0211788a641f33ef319c8293d6565c71fe768caef87b4',
              'original_v1_task_sha256':'40ab98e7b6b98a93792a543aa46cc880efeab4ea9069b4a76ba5050b68d3806d',
              'frame_sha256':sha(frame),'registry_sha256':sha(registry),
              'case_count':len(selection),'task_count':len(qs),'cutoff_date':CUTOFF,
              'exclusion_ags_sorted':sorted(EXCLUDE),'phase':'frozen_frame_only_no_new_sources_inspected',
              'source_homepages_unverified_official':True,'case_file_sha256':sha(output/'cases_frozen.csv'),
              'task_file_sha256':sha(output/'tasks_frozen.csv'),
              'alternates_predeclared':alternates}
    (output/'freeze_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--frame',type=Path,required=True);p.add_argument('--registry',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('frozen'))
    a=p.parse_args();m=prepare(a.frame,a.registry,a.output);print(json.dumps({k:m[k] for k in ('case_count','task_count','case_file_sha256','task_file_sha256')},indent=2))
