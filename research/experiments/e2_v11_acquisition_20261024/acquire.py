#!/usr/bin/env python3
"""Bounded E2 v1.1 adapter. Run from repository root; refuses output reuse.
Existing Engine allowlist/scoring and HTML/PDF parsers are reused. No DB writes.
HTTP uses single-hop, no-retry, identity-encoded streaming and strict robots gates.
"""
import csv, hashlib, json, sys, time, re, platform
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin, urldefrag
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from crawler.core.engine import Engine, HIGH_IMPACT_REGEX
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf

ROOT=Path(__file__).resolve().parent
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'export'
OUT.mkdir(parents=True,exist_ok=False)
(OUT/'raw').mkdir()
UA='E2ResearchSourceAcquisition/1.1'
LIMIT=5*1024*1024
session=requests.Session();session.trust_env=False
session.headers.update({'User-Agent':UA,'Accept-Encoding':'identity'})
engine=object.__new__(Engine)
cases=list(csv.DictReader((ROOT/'protocol/frozen/cases_frozen.csv').open()))
engine.allowed_domains_by_muni={c['ags']:{engine._norm_domain(x) for x in c['registry_allowed_domains'].split('|')} for c in cases}
last={}; delays={}; robots={}; counts={}; attempts={}; failures=[]; records=[]; units=[]; log=[]; skipped=[]
def now():return datetime.now(timezone.utc).isoformat()
def dump(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def append(name,value):
 with (OUT/name).open('a') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
def allowed(ags,url):
 p=urlsplit(url)
 return engine._is_allowed(ags,url) and p.scheme=='https' and not p.username and p.port in (None,443)
def one(ags,url,kind,parent):
 if counts[ags]>=20 or attempts[ags]>=20: raise RuntimeError('response/request budget exhausted')
 if not allowed(ags,url):raise RuntimeError('outside HTTPS municipal allowlist: '+url)
 host=urlsplit(url).hostname
 time.sleep(max(0,last.get(host,0)+delays.get(host,1.1)-time.monotonic()))
 attempts[ags]+=1
 entry={'ags':ags,'url':url,'kind':kind,'discovered_from':parent,'acquisition_time':now(),'attempt':attempts[ags]}
 try:
  with session.get(url,stream=True,allow_redirects=False,timeout=(15,30),verify=True) as r:
   counts[ags]+=1
   entry.update(status=r.status_code,headers=dict(r.headers),response_number=counts[ags])
   if int(r.headers.get('Content-Length','0'))>LIMIT:raise RuntimeError('Content-Length exceeds 5 MiB; body not read')
   b=bytearray()
   while len(b)<LIMIT:
    chunk=r.raw.read(min(65536,LIMIT-len(b)),decode_content=False)
    if not chunk:break
    b.extend(chunk)
   if len(b)==LIMIT:raise RuntimeError('5 MiB boundary reached; incomplete body excluded')
   b=bytes(b);h=hashlib.sha256(b).hexdigest();(OUT/'raw'/h).write_bytes(b)
   entry.update(body_sha256=h,bytes=len(b),complete=True)
   if r.headers.get('Content-Encoding','identity').lower() not in ('identity',''):raise RuntimeError('encoded response retained but not parsed')
   return entry,b
 except Exception as e:
  entry['error']=str(e);failures.append({'ags':ags,'url':url,'error':str(e)})
  raise
 finally:
  last[host]=time.monotonic();log.append(entry);append('requests.jsonl',entry)
def fetch(ags,url,kind,parent,trail=None):
 trail=list(trail or [])
 if len(trail)>5:raise RuntimeError('redirect limit')
 if kind!='robots' and not robot_ok(ags,url):
  skipped.append({'ags':ags,'url':url,'reason':'robots denied/unavailable'});return None
 e,b=one(ags,url,kind,parent)
 if e['status'] in (301,302,303,307,308):
  target=urljoin(url,e['headers'].get('Location',e['headers'].get('location','')))
  if not allowed(ags,target):raise RuntimeError('redirect outside HTTPS municipal allowlist: '+target)
  return fetch(ags,target,kind,url,trail+[{'url':url,'status':e['status'],'body_sha256':e['body_sha256'],'location':target}])
 e['redirects']=trail
 return e,b

def robot_ok(ags,url):
 p=urlsplit(url);origin=p.scheme+'://'+p.netloc
 if origin not in robots:
  robots[origin]=False
  try:
   got=fetch(ags,origin+'/robots.txt','robots',None)
   if not got:return False
   e,b=got
   if e['status'] in (404,410):robots[origin]=True
   elif e['status']==200 and not b.lstrip().lower().startswith((b'<!doctype html',b'<html')):
    rp=RobotFileParser();rp.parse(b.decode('utf-8-sig',errors='replace').splitlines());robots[origin]=rp
    delay=rp.crawl_delay(UA) or rp.crawl_delay('*') or 1.1
    rate=rp.request_rate(UA) or rp.request_rate('*')
    if rate:delay=max(delay,rate.seconds/rate.requests)
    delays[p.hostname]=max(1.1,delay)
   else:failures.append({'ags':ags,'url':origin+'/robots.txt','error':'robots unavailable; fail closed','status':e['status']})
  except Exception as e: failures.append({'ags':ags,'url':origin+'/robots.txt','error':str(e)})
 rp=robots[origin]
 return rp if isinstance(rp,bool) else rp.can_fetch(UA,url)

institutions={'09263000':'Stadt Straubing','09163000':'Stadt Rosenheim','09780147':'Gemeinde Wildpoldsried','09471123':'Markt Buttenheim'}
for c in cases:
 ags=c['ags'];counts[ags]=0;attempts[ags]=0
 seed=c['seed_homepage_url'];start='https://'+urlsplit(seed).netloc+(urlsplit(seed).path or '/')
 queue=[(-10000,start,'frozen registry seed (HTTPS transport upgrade)',0)]
 seen=set();pending=[];proof=[]
 while queue and attempts[ags]<20 and counts[ags]<20:
  queue.sort(key=lambda x:(x[0],x[1]));_,url,parent,depth=queue.pop(0)
  url=urldefrag(url)[0]
  if url in seen:continue
  seen.add(url)
  try:
   got=fetch(ags,url,'document',parent)
   if not got:continue
   e,b=got
   if e['status']!=200:
    failures.append({'ags':ags,'url':e['url'],'error':'HTTP status','status':e['status']});continue
   typ=e['headers'].get('Content-Type',e['headers'].get('content-type','')).lower()
   fr=FetchResult(e['url'],200,typ,b,{k.lower():v for k,v in e['headers'].items()})
   if 'html' in typ: parsed=parse_html(fr,e['url']);source_type='html'
   elif 'pdf' in typ or b.startswith(b'%PDF'):parsed=parse_pdf(fr,e['url']);source_type='pdf'
   else:continue
   if not parsed.text.strip():continue
   rec={'ags':ags,'url':e['url'],'body_sha256':e['body_sha256'],'acquisition_time':e['acquisition_time'],'document_date':None,'source_type':source_type,'content_type':typ,'discovered_from':parent,'redirects':e['redirects'],'seed_url':seed,'publishing_institution':institutions[ags],'territorial_scope':'NOT_ASSESSED','cutoff_date':'2026-10-19','cutoff_eligibility':'NOT_ESTABLISHED','bytes':len(b)}
   if source_type=='html':
    text=BeautifulSoup(b,'lxml').get_text(' ',strip=True)
    match=re.search(re.escape(institutions[ags]),text,re.I)
    if match and ('impressum' in e['url'].lower() or 'impressum' in text.lower()):
     proof.append({'url':e['url'],'body_sha256':e['body_sha256'],'institution_literal':match.group(0),'context':text[max(0,match.start()-80):match.end()+180],'basis':'municipal self-identification on allowlisted institutional site'})
   pending.append((rec,parsed))
   print(ags,counts[ags],len(b),e['url'],flush=True)
   if depth<3:
    for link,anchor in parsed.out_links:
     link=urldefrag(link)[0]
     if not allowed(ags,link) or link in seen:continue
     label=link+' '+(anchor or '')
     imprint=bool(re.search('impressum',label,re.I))
     relevant=bool(HIGH_IMPACT_REGEX.search(label))
     if not(imprint or relevant):continue
     if imprint and proof and any('impressum' in p['url'].lower() for p in proof):continue
     queue.append((-9000 if imprint else -engine.score(link,anchor),link,e['url'],depth+1))
  except Exception as ex:
   failures.append({'ags':ags,'url':url,'error':str(ex)})
 if not proof:
  failures.append({'ags':ags,'error':'institutional provenance not verified; parsed documents excluded'})
 for rec,parsed in pending if proof else []:
  rec['institutional_provenance']=proof;rec['provenance_status']='institutional_self_identification_raw_preserved'
  if any(r['ags']==ags and r['url']==rec['url'] and r['body_sha256']==rec['body_sha256'] for r in records):continue
  records.append(rec)
  for i,seg in enumerate(parsed.segments):
   if not seg.text.strip():continue
   units.append({'unit_id':ags+'-'+rec['body_sha256']+'-'+str(i),'acquisition_ags':ags,'source_url':rec['url'],'raw_hashes':[rec['body_sha256']],'text':seg.text,'heading_context':seg.heading_context,'page_ref':seg.page_ref,'origins':[{'source_url':rec['url'],'body_sha256':rec['body_sha256'],'segment_order':seg.order_index,'segment_type':seg.segment_type}]})
 dump('progress.json',{'responses':counts,'attempts':attempts,'documents':len(records),'units':len(units),'failures':failures})
for name,data in [('catalog.jsonl',records),('corpus.jsonl',units)]:
 (OUT/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data))
dump('manifest.json',{'version':'E2-v1.1-acquisition','completed_at':now(),'cutoff_date':'2026-10-19','responses':counts,'attempts':attempts,'documents_by_ags':{a:sum(r['ags']==a for r in records) for a in counts},'units':len(units),'response_bytes':sum(e.get('bytes',0) for e in log),'limits':{'responses_per_municipality':20,'bytes_per_response':LIMIT,'min_delay_per_host_seconds':1.1},'user_agent':UA,'failures':failures,'skipped':skipped,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'python':platform.python_version(),'source_code_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['crawler/core/engine.py','crawler/core/models.py','crawler/core/parsers/html_parser.py','crawler/core/parsers/pdf_parser.py']},'reproduce':'PYTHONPATH=. .venv/bin/python '+str(Path(__file__).relative_to(Path.cwd()))+' /fresh/output/path','live_refetch_is_not_byte_reproducible':True,'offline_reproduction':'Original bodies and parser hashes preserved; no labels or evaluation performed.'})
print(json.dumps({'responses':counts,'documents':len(records),'units':len(units),'failures':failures},ensure_ascii=False),flush=True)
