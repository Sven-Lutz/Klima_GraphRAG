"""Remove diagnostic scores and sort sources for human development reference review."""
from pathlib import Path
import argparse,csv,hashlib,json
FIELDS=['unit_id','namespace','source_id','url','body_sha256','representation_sha256','segment_index','text','text_sha256','heading_context','page_ref','segment_type','parser_order_index','fetched_at_utc']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(source,out):
 out.mkdir(exist_ok=False)
 units=[json.loads(line) for line in (source/'corpus.jsonl').read_text().splitlines()]
 docs=[json.loads(line) for line in (source/'catalog.jsonl').read_text().splitlines()]
 units.sort(key=lambda u:(u['url'],u['source_id'],u['segment_index']))
 with (out/'source_order_units.jsonl').open('x') as f:
  for u in units:f.write(json.dumps({k:u[k] for k in FIELDS},ensure_ascii=False)+'\n')
 with (out/'sources.csv').open('x',newline='') as f:
  keys=['source_id','url','body_sha256','fetched_at_utc','source_type','raw_path','units']
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for d in sorted(docs,key=lambda d:d['url']):w.writerow({k:d[k] for k in keys})
 aspects=[('A1','Dokumentierter Stand des kommunalen Wärmeplans'),('A2','Praktische Bedeutung des belegten Planungsstands'),('A3','Dokumentierter nächster Schritt und Grenze zum tatsächlichen Vollzug'),('A4','Benannte Rollen und Zuständigkeiten; Aussageumfang und Zeitpunkt')]
 with (out/'aspect_review.csv').open('x',newline='') as f:
  keys=['aspect_id','information_need','review_status','finding','proposed_answer','locator_ids','time_and_territory','institutional_role','reviewer_id','reviewed_at','rationale']
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for ident,question in aspects:w.writerow({'aspect_id':ident,'information_need':question,'review_status':'NOT_ASSESSED'})
 with (out/'document_aspect_review.csv').open('x',newline='') as f:
  keys=['source_id','aspect_id','review_status','relevance_judgment','locator_ids','rationale']
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for d in sorted(docs,key=lambda d:d['url']):
   for ident,_ in aspects:w.writerow({'source_id':d['source_id'],'aspect_id':ident,'review_status':'NOT_ASSESSED'})
 file_hashes={p.name:sha(p) for p in out.iterdir() if p.is_file()}
 with (out/'manifest.json').open('x') as f:json.dump({'status':'UNREVIEWED_DEVELOPMENT_PACKET','units':len(units),'sources':len(docs),'aspects':len(aspects),'document_aspect_rows':len(docs)*len(aspects),'rankings_or_admission_scores_included':False,'independence_verified':False,'input_corpus_sha256':sha(source/'corpus.jsonl'),'input_catalog_sha256':sha(source/'catalog.jsonl'),'script_sha256':sha(Path(__file__)),'files':file_hashes},f,indent=2);f.write('\n')
 print(f'{len(docs)} sources; {len(units)} ordered units; {len(aspects)} unassessed aspects; no relevance judgments.')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();main(a.source,a.out)
