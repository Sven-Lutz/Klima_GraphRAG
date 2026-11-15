"""Offline E0b transport/measurement rehearsal; never accesses municipal websites."""
from __future__ import annotations

import argparse
from collections import deque
import csv
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import socket
import tempfile
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

from crawler.core.engine import Engine, EngineLimits, _segment_features
from crawler.core.parsers.html_parser import parse_html
from crawler.core.storage import Storage
from crawler.scripts.chunk_documents import process_segments, setup_db
from research.experiments.ds006.continuity import ROOT, digest


class OfflineResponse:
    def __init__(self, url, body, status=200, content_type='text/html'):
        self.url=url; self.content=body; self.status_code=status
        self.headers={'Content-Type':content_type, 'Content-Length':str(len(body))}
        self.text=body.decode('utf-8')

    def iter_content(self, chunk_size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Replay:
    """One get = one simulated wire request; no external fallback or hidden cache."""
    def __init__(self, responses, budget):
        self.responses=responses; self.budget=budget; self.log=[]; self.denied=0

    def get(self, url, **kwargs):
        if len(self.log) >= self.budget:
            self.denied += 1
            raise RuntimeError('offline_request_budget_exhausted')
        start=perf_counter()
        body, ctype = self.responses.get(url, (b'fixture not found', 'text/plain'))
        status=200 if url in self.responses else 404
        self.log.append(dict(request=len(self.log)+1, url=url, status=status,
                             timestamp_utc=datetime.now(timezone.utc).isoformat(),
                             bytes=len(body), sha256=digest(body), elapsed_seconds=perf_counter()-start))
        return OfflineResponse(url,body,status,ctype)


def locked_inputs():
    base=ROOT/'research/experiments/e0a'
    cases=list(csv.DictReader((base/'cases.csv').open()))
    tasks=list(csv.DictReader((base/'tasks_instantiated.csv').open()))
    assert [(c['case_id'],c['ags']) for c in cases] == [('A','09564000'),('B','09190118')]
    assert len(tasks)==8 and {t['task_id'] for t in tasks} == {f'{c}-T{i}' for c in 'AB' for i in range(1,5)}
    hashes={name:digest((base/name).read_bytes()) for name in [
        'cases.csv','tasks.csv','tasks_instantiated.csv','official_selection_frame_2026-06-30.csv']}
    return cases,tasks,hashes


def portal(case, tasks):
    # Same neutral synthetic homepage and observable discovery channels for both arms.
    root=f"https://case-{case['case_id'].lower()}.example.test/"
    documents={}
    for n,task in enumerate(tasks,1):
        text='; '.join(f'{slot}: synthetic-{n}' for slot in task['required_evidence_slots'].split('|'))
        documents[root+f'doc/{n}']=f'<meta charset="utf-8"><p>SYNTHETIC {task["task_id"]}: {text}</p>'.encode()
    site={root: (b'<nav><a href="/ris">Ratsinformationssystem</a></nav>'
                 b'<a href="/energie">Energie Aktuelles</a>', 'text/html'),
          root+'robots.txt': (f'User-agent: *\nAllow: /\nSitemap: {root}sitemap.xml\n'.encode(),'text/plain'),
          root+'sitemap.xml': (('<urlset>'+''.join(f'<url><loc>{u}</loc></url>' for u in documents)+'</urlset>').encode(),'application/xml'),
          root+'ris': (('<main>'+''.join(f'<a href="{u}">Dokument</a>' for u in documents)+'</main>').encode(),'text/html'),
          root+'energie': (b'<p>SYNTHETIC: allgemeine Nachricht ohne wissenschaftliche Belegfunktion.</p>','text/html')}
    site.update({u:(b,'text/html') for u,b in documents.items()})
    return root,site,documents


def structured_first(engine, muni, seed):
    """Minimal comparator: enumerate all sitemap entries, then follow HTML links.

    Same engine fetch/robots/canonicalization/parser/storage; no task labels or
    reference URLs enter priority selection. No undocumented OParl support.
    """
    _,maps=engine._load_robots(seed)
    queue=deque()
    for sm in maps[:5]:
        queue.extend((u,1) for u in engine._sitemap_urls(sm))
    queue.append((seed,0)); seen=set()
    while queue:
        url,depth=queue.popleft(); url=engine.canon.normalize(url)
        if not url or url in seen or depth>6 or not engine._is_allowed(muni,url):
            continue
        seen.add(url)
        if not engine._robots_allowed(url):
            continue
        try:
            fr=engine.fetch(url)
        except RuntimeError:
            if len(engine._session.log)>=engine._session.budget:
                break
            raise
        if fr.status_code!=200:
            continue
        with engine.storage.transaction():
            doc=engine.storage.store_raw(muni,url,fr)
            parsed=parse_html(fr,fr.url_final)
            engine.storage.store_segments_scored(doc,parsed.segments,_segment_features)
            engine.storage.store_outlinks(doc,parsed.out_links)
        queue.extend((u,depth+1) for u,_ in parsed.out_links)


def dry_run(budget=8):
    if budget<1:
        raise ValueError('budget must be positive')
    cases,tasks,hashes=locked_inputs()
    results=[]
    with patch.object(socket,'socket',side_effect=RuntimeError('offline only')):
        for case in cases:
            case_tasks=[t for t in tasks if t['case_id']==case['case_id']]
            seed,responses,gold=portal(case,case_tasks)
            for strategy in ('heuristic','structured_first'):
                with tempfile.TemporaryDirectory(prefix='ds006-e0b-') as tmp:
                    db=Path(tmp)/'dry.sqlite'
                    with Storage(db,raw_dir=Path(tmp)/'raw',create=True):
                        pass
                    # Inject only fixture storage; do not write the default raw directory.
                    with patch('crawler.core.engine.Storage', side_effect=lambda **kw:
                               Storage(raw_dir=Path(tmp)/'raw', **kw)):
                        engine=Engine({},db_path=db,
                                      allowed_domains_by_muni={case['ags']:{urlsplit(seed).netloc}},
                                      limits=EngineLimits(max_pages_per_muni=100),
                                      min_delay_seconds_per_domain=0,tls_verify=True)
                    engine._session.close()
                    replay=Replay(responses,budget); engine._session=replay
                    start=perf_counter()
                    try:
                        with redirect_stdout(io.StringIO()):
                            if strategy=='heuristic':
                                engine.run([(case['ags'],seed)])
                            else:
                                structured_first(engine,case['ags'],seed)
                        setup_db(engine.storage.conn)
                        process_segments(engine.storage.conn,retain_all=True)
                        acquired={r[0] for r in engine.storage.conn.execute('SELECT url_canonical FROM documents_raw')}
                        provenance=[dict(r) for r in engine.storage.conn.execute(
                            'SELECT url_canonical, municipality_id, raw_hash, status_code FROM documents_raw')]
                        per_task=[]
                        for n,t in enumerate(case_tasks,1):
                            url=seed+f'doc/{n}'
                            texts=[r[0] for r in engine.storage.conn.execute(
                                'SELECT c.text FROM chunks c JOIN documents_raw d ON c.document_id=d.document_id '
                                'WHERE d.url_canonical=? AND c.municipality_id=?',(url,case['ags']))]
                            per_task.append(dict(task_id=t['task_id'],synthetic_acquired=url in acquired,
                                synthetic_slots_in_chunk=any(all(slot in text for slot in t['required_evidence_slots'].split('|')) for text in texts)))
                        results.append(dict(case_id=case['case_id'], municipality=case['municipality_name'],
                            strategy=strategy, seed=seed, budget=budget, requests=len(replay.log),
                            budget_denied=replay.denied,
                            completion='budget_exhausted' if replay.denied else 'frontier_exhausted',
                            bytes=sum(r['bytes'] for r in replay.log),
                            wall_seconds=perf_counter()-start, fixture_document_recall=sum(u in acquired for u in gold)/len(gold),
                            tasks=per_task, provenance=provenance, request_log=replay.log,
                            input_manifest={u:digest(body) for u,(body,_) in responses.items()}))
                    finally:
                        engine.storage.close()
    return dict(status='synthetic_dry_run_not_municipal_evidence',locked_input_hashes=hashes,
                human_search_minutes=0, live_http_requests=0,
                runner_sha256=digest(Path(__file__).read_bytes()), results=results)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--budget',type=int,default=8);args=ap.parse_args()
    result=dry_run(args.budget)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print([(r['case_id'],r['strategy'],r['requests'],r['fixture_document_recall']) for r in result['results']])
