"""Reparse saved original bodies offline and compare every E2 text unit."""
from pathlib import Path
import hashlib,json
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf
r=Path(__file__).resolve().parent;o=r/'export'
m=json.loads((o/'manifest.json').read_text())
for name,h in m['source_code_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h
units=[json.loads(x) for x in (o/'corpus.jsonl').read_text().splitlines()]
rebuilt=[]
for line in (o/'catalog.jsonl').read_text().splitlines():
 c=json.loads(line);b=(o/'raw'/c['body_sha256']).read_bytes()
 f=FetchResult(c['url'],200,c['content_type'],b,{})
 p=(parse_html if c['source_type']=='html' else parse_pdf)(f,c['url'])
 for i,s in enumerate(p.segments):
  if s.text.strip():rebuilt.append({'unit_id':c['ags']+'-'+c['body_sha256']+'-'+str(i),'acquisition_ags':c['ags'],'source_url':c['url'],'raw_hashes':[c['body_sha256']],'text':s.text,'heading_context':s.heading_context,'page_ref':s.page_ref,'origins':[{'source_url':c['url'],'body_sha256':c['body_sha256'],'segment_order':s.order_index,'segment_type':s.segment_type}]})
assert rebuilt==units
(o/'reparse_validation.json').write_text(json.dumps({'status':'PASS','offline_units_reproduced':len(rebuilt)})+'\n')
print('PASS: offline units reproduced:',len(rebuilt))
