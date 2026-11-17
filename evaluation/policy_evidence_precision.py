
"""Read-only evidence screening; never predicts municipal policy labels."""
import csv, hashlib, html, json, re
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = Path('evaluation/policy_evidence_review_v1/cases')
OUT = Path('evaluation/policy_evidence_precision_v2')

PATTERNS = {
 'balkonkraftwerk_subsidy': r'balkonkraftwerk|stecker.?solar|steckerfertig|mini.?solar',
 'rooftop_pv_subsidy': r'photovoltaik|pv.?anlag|solaranlag',
 'heat_pump_heating_subsidy': r'w[aä]rmepump|heizungstausch|heizungsf[oö]rder',
 'building_energy_renovation': r'd[aä]mm|geb[aä]udeh[uü]ll|fenstertausch|w[aä]rmeschutz',
 'ev_charging_emobility': r'wallbox|ladeinfrastruktur|ladestation|lades[aä]ul|ladepunkt|elektromobil'
}

HOSTS = dict(zip(
 ('09161000','09162000','09362000','09462000','09562000','09563000','09564000','09663000','09761000'),
 ('ingolstadt.de','muenchen.de','regensburg.de','bayreuth.de','erlangen.de','fuerth.de','nuernberg.de','wuerzburg.de','augsburg.de')
))

FUND = r'f[oö]rder|zuschuss|bezuschuss|beihilfe|antragsfrist|antragstellung'
DECISION = r'gef[oö]rdert werden|f[oö]rderf[aä]hig|f[oö]rderrichtlinie|nicht mehr.{0,55}(?:gef[oö]rdert|angeboten|m[oö]glich|bezuschusst)|f[oö]rderprogramm.{0,50}(?:eingestellt|beendet|ausgelaufen)'
PROPOSAL = r'stellt folgenden antrag|antrag der .{0,40}fraktion|[öo]ffentlichkeitsbeteiligung|\bvorberatung\b'
PUBLIC = r'\bstadion\b|\bgrundschule\b|\bschulgeb[aä]ude\b|\bparkhaus\b|st[aä]dtische geb[aä]ude'
PRIVATE = r'privat|eigent[uü]mer|mieter|haushalt|wohngeb[aä]ude|b[uü]rger'

def check(packet, c):
    if hashlib.sha256(c['text'].encode()).hexdigest() != c['text_sha256'] or c['source_snapshot_id'] != packet['snapshot_id']:
        raise ValueError('Evidence integrity failure: ' + c['chunk_id'])

    txt = ' '.join(c['text'].split())
    host = (urlsplit(c['source_url']).hostname or '').lower()
    city_host = HOSTS[packet['municipality_id']]
    official = host == city_host or host.endswith('.' + city_host)

    windows = [
        txt[max(0,m.start()-220):m.end()+220]
        for m in list(re.finditer(PATTERNS[packet['policy_family']],txt,re.I))[:15]
    ]

    matched = bool(windows)
    funded = any(re.search(FUND,w,re.I) for w in windows)
    status = any(re.search(DECISION,w,re.I) for w in windows)
    proposal = bool(re.search(PROPOSAL,txt,re.I))
    public = bool(re.search(PUBLIC,txt[:600],re.I))
    private = any(re.search(PRIVATE,w,re.I) for w in windows)
    foreign = bool(re.search(
        r'\b(?:stadt|landkreis)\s+(?:coburg|bamberg|ansbach|erlangen.h[oö]chstadt)',
        txt[:500],re.I
    ))
    utility = host.endswith('rewag.de') or bool(re.search(
        r'\brewag\b|kundenbonus|stromkund',txt[:350],re.I
    ))
    pv_ok = packet['policy_family'] != 'rooftop_pv_subsidy' or any(
        re.search(r'dach|fassad|fest.installiert',w,re.I) for w in windows
    )

    checks = (
        (not official,'unverified_city_host'),
        (not matched,'wrong_policy'),
        (not funded,'funding_not_near_policy'),
        (not status,'no_clear_program_status'),
        (proposal,'proposal_not_decision'),
        (public and not private,'public_infrastructure'),
        (foreign,'foreign_municipality'),
        (utility,'utility_incentive'),
        (not pv_ok,'rooftop_scope_unconfirmed')
    )
    flags = [name for condition,name in checks if condition]
    ready = official and matched and funded and status and not proposal and not foreign and not utility and pv_ok and (private or not public)

    return {
        'url':c['source_url'],
        'chunk_id':c['chunk_id'],
        'evidence_id':c['evidence_id'],
        'priority':'review_first' if ready else 'investigate',
        'flags':flags,
        'excerpt':txt[:750]
    }

def main():
    files = sorted(SOURCE.glob('MPB-*.json'))
    if not files:
        raise SystemExit('No JSON packets found at '+str(SOURCE))

    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'cases').mkdir(exist_ok=True)
    summary=[]
    queue=[]
    page=['<!doctype html><meta charset="utf-8"><h1>Policy evidence screening</h1><p>Review priorities only; NOT verified Yes/No decisions.</p>']

    for path in files:
        p=json.loads(path.read_text(encoding='utf-8'))
        items=[check(p,c) for c in p['candidates']]
        items.sort(key=lambda r:(r['priority']!='review_first',len(r['flags'])))
        first=sum(x['priority']=='review_first' for x in items)

        summary.append({
            'case':p['benchmark_id'],
            'city':p['municipality'],
            'candidates':len(items),
            'review_first':first
        })
        if not first:
            queue.append({'case':p['benchmark_id'],'question':p['question']})

        (OUT/'cases'/path.name).write_text(
            json.dumps({'question':p['question'],'ranked':items},ensure_ascii=False,indent=2),
            encoding='utf-8'
        )

        page.append('<h2>'+html.escape(p['benchmark_id']+' '+p['municipality'])+'</h2><p>'+html.escape(p['question'])+'</p>')
        for item in items[:8]:
            u=html.escape(item['url'],quote=True)
            page.append(
                '<p><b>'+item['priority']+'</b> — '+
                html.escape(', '.join(item['flags']))+
                '</p><a href="'+u+'">'+u+'</a><pre style="white-space:pre-wrap">'+
                html.escape(item['excerpt'])+'</pre>'
            )

    for name,rows,fields in [
        ('summary.csv',summary,['case','city','candidates','review_first']),
        ('investigation_queue.csv',queue,['case','question'])
    ]:
        with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    (OUT/'index.html').write_text('\n'.join(page),encoding='utf-8')

    print('CASES:',len(files),'| PASSAGES:',sum(s['candidates'] for s in summary),
          '| CASES NEEDING REVIEW:',len(queue))
    print('REPORT:',OUT/'index.html')
    print('No original packets modified. No verdicts inferred.')

if __name__=='__main__':
    main()
