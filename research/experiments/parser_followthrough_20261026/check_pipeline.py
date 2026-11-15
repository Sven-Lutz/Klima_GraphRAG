"""Offline 2x2 source-preservation diagnosis, not retrieval quality evaluation."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import socket
import sys
import tempfile
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.venv/tokenizer-cache')
def denied(*args, **kwargs): raise RuntimeError('Offline diagnosis prohibits network')
socket.create_connection = denied
socket.socket.connect = denied
from crawler.core.engine import _segment_features
from crawler.core.models import FetchResult
from crawler.core.storage import Storage
from crawler.scripts.chunk_documents import process_segments, setup_db
from crawler.core.segmentation.chunker import _get_encoding

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def run():
    digest = 'e4d4bc3858a691a4b0756ac1853cf5d1656fdae6bab34c868c164b2382beadbd'
    raw = ROOT / 'research/experiments/e2_v11_acquisition_20261024/export/raw' / digest
    assert sha(raw) == digest
    terms = ['Inbetriebnahme November 2005','Dorfentwicklungs-GmbH','Daten 2020','3.572.280']
    url = 'https://www.wildpoldsried.de/erneuerbare-energien.html'
    source = FetchResult(url, 200, 'text/html; charset=utf-8', raw.read_bytes(), {})
    parsers = {'before': ROOT/'research/experiments/parser_repair_20261026/parser_before.py', 'repaired': ROOT/'crawler/core/parsers/html_parser.py'}
    _get_encoding('cl100k_base')  # locally cached, network remains disabled
    rows = []
    for version, path in parsers.items():
        parsed = load(path, '_parser_' + version).parse_html(source, url)
        for retain in (False, True):
            with tempfile.TemporaryDirectory(prefix='heat-continuity-') as tmp:
                with Storage(Path(tmp)/'fixture.sqlite',raw_dir=Path(tmp)/'raw',create=True) as store:
                    doc = store.store_raw('09780147', url, source)
                    store.store_segments_scored(doc,parsed.segments,_segment_features)
                    setup_db(store.conn)
                    process_segments(store.conn,retain_all=retain)
                    segments = store.conn.execute('SELECT * FROM segments WHERE document_id=? ORDER BY order_index',(doc,)).fetchall()
                    chunks = store.conn.execute('SELECT c.*,d.url_canonical,d.raw_hash FROM chunks c JOIN documents_raw d ON c.document_id=d.document_id WHERE c.document_id=?',(doc,)).fetchall()
                    for term in terms:
                        matching = [s for s in segments if term in s['text']]
                        found = [c for c in chunks if term in c['text']]
                        with_context = [c for c in chunks if term in c['text']+' '+(c['heading_context'] or '')]
                        lineage = all(c['segment_id'] in {s['segment_id'] for s in segments} and c['url_canonical']==url and c['raw_hash']==digest and c['municipality_id']=='09780147' for c in with_context) if with_context else None
                        rows.append({'parser':version,'retain_all':retain,'marker':term,'in_parsed_segments':any(term in s.text for s in parsed.segments),'in_stored_segments':bool(matching),'in_chunk_text':bool(found),'in_chunk_text_or_heading':bool(with_context),'source_lineage_valid_when_present':lineage,'matching_segment_length_score_negative':[{'order':s['order_index'],'length':len(s['text']),'score':s['impact_score'],'negative':bool(s['is_negative'])} for s in matching],'segment_count':len(segments),'chunk_count':len(chunks),'total_chunk_tokens':sum(c['token_count'] for c in chunks)})
    assert all(r['in_parsed_segments'] and r['in_stored_segments'] and r['in_chunk_text'] and r['source_lineage_valid_when_present'] for r in rows if r['parser']=='repaired' and r['retain_all'])
    tracked=[*parsers.values(),ROOT/'crawler/core/engine.py',ROOT/'crawler/core/storage.py',ROOT/'crawler/scripts/chunk_documents.py',ROOT/'crawler/core/segmentation/chunker.py']
    return {'purpose':'POSTHOC_DEVELOPMENT_MARKER_CONTINUITY_NOT_RETRIEVAL_QUALITY','source_sha256':digest,'sample_size':1,'selected_markers':len(terms),'same_chunking_parameters':{'chunk_size':600,'overlap':60,'segment_limit':100000},'admission_default':{'min_score':10,'min_length':80,'exclude_negative':True},'retain_all_bypasses_only_admission':True,'equal_index_size_or_context_budget_claim':False,'network_disabled':True,'databases':'temporary explicit paths only; deleted after measurement','code_sha256':{str(p.relative_to(ROOT)):sha(p) for p in tracked},'rows':rows}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    if a.out.exists():raise FileExistsError('Output already exists')
    result=run()
    with a.out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    for version in ('before','repaired'):
        for retain in (False,True):
            part=[r for r in result['rows'] if r['parser']==version and r['retain_all']==retain]
            print(version,'retain_all='+str(retain),'markers_in_chunk_text='+str(sum(r['in_chunk_text'] for r in part))+'/4','chunks='+str(part[0]['chunk_count']),'tokens='+str(part[0]['total_chunk_tokens']))
