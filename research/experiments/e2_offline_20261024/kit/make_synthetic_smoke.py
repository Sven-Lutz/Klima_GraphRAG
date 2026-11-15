#!/usr/bin/env python3
"""ONLY synthetic, never representative of E2 generalization or policy truth."""
from pathlib import Path
import csv,hashlib,json

def make(where:Path,cases:Path,tasks:Path):
    where.mkdir(exist_ok=True,parents=True);raw=where/'raw';raw.mkdir(exist_ok=True)
    city={r['case_id']:r['official_name'].split(' /')[0].split(',')[0] for r in csv.DictReader(cases.open())}
    units=[];sources=[];anchors=[]
    for idx,t in enumerate(csv.DictReader(tasks.open())):
        c=city[t['case_id']];typ=t['task_type'];tid=t['task_id']
        if typ=='formal_target':v=f'Der Stadtrat von {c} beschloss das Klimaschutzziel eines um 40 Prozent reduzierten Ausstoßes bis 2030.'
        elif typ=='heat_planning':v=f'Die kommunale Wärmeplanung für {c} wird in einem Bericht über den Stadtratsbeschluss zur Wärmeplanung beschrieben.'
        elif typ=='actor_roles':v=f'Der Betreiber des Wärmenetzes in {c} berichtet über Zuständigkeiten der Wärmenetzversorgung im Ort.'
        else:v=f'Die Maßnahme in {c} wurde von 2021 bis 2024 fortgeschrieben und im Fortschrittsbericht als Veränderung beschrieben.'
        # Synthetic claim sounding realistic is NOT an authoritative municipal fact.
        body=v.encode('utf8');digest=hashlib.sha256(body).hexdigest();(raw/digest).write_bytes(body)
        url='https://synthetic.invalid/'+tid+'.txt';unit_id=hashlib.sha256(tid.encode()).hexdigest()
        units.append(dict(unit_id=unit_id,acquisition_ags=t['ags'],source_url=url,raw_hashes=[digest],
                          text=v,heading_context='',page_ref='1',origins=[{'run':'synthetic-only'}]))
        sources.append(dict(ags=t['ags'],url=url,body_sha256=digest,acquisition_time='2026-10-21T00:00:00Z',
                            document_date='2025-01-01',source_type='synthetic'))
        quote=v[:min(65,len(v))]
        anchors.append(dict(task_id=tid,ags=t['ags'],url=url,body_sha256=digest,unit_id=unit_id,
                            quote=quote,start_char=0,end_char=len(quote),evidence_role='synthetic_quote_only'))
    with (where/'corpus.jsonl').open('w') as f:
        for x in units:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    with (where/'catalog.jsonl').open('w') as f:
        for x in sources:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    with (where/'anchors.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(anchors[0]));w.writeheader();w.writerows(anchors)

if __name__=='__main__':
    root=Path(__file__).parent
    make(root/'smoke',root/'frozen/cases_frozen.csv',root/'frozen/tasks_frozen.csv')
