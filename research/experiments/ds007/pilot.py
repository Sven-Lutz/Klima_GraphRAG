"""Bounded public-source pilot. No hidden seeds, retries or external APIs.

A uses Engine.run. B uses explicit homepage/institutional entrypoints followed by
systematic sitemap enumeration and HTML traversal. Shared transport enforces
budget/robots/TLS/domain policy independently of scheduling.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import redirect_stdout
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib import robotparser
from urllib.parse import urljoin, urlsplit
from unittest.mock import patch
import zlib

from bs4 import BeautifulSoup
import requests

from crawler.core.engine import Engine, EngineLimits, _segment_features
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf
from crawler.core.storage import Storage
from crawler.scripts.chunk_documents import process_segments, setup_db
from research.experiments.ds006.continuity import ROOT

UA = 'KlimaGraphRAG-ResearchPilot/0.1'
INSTITUTION = re.compile(r'ratsinfo|ratsinformations|b[üu]rgerinfo|bürgerserviceportal|'
                         r'gemeinderat|stadtrat|stadtwerke|gemeindewerke|'
                         r'verwaltungsgemeinschaft|zweckverband', re.I)
CAP = 4 * 1024 * 1024
TOTAL = 20 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def host(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def origin(url):
    p = urlsplit(url)
    return f'{p.scheme}://{p.netloc}'


class StopPilot(RuntimeError):
    pass


class Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status_code, self.body = url, status, body
        self.headers = requests.structures.CaseInsensitiveDict(headers)
        self.text = body.decode('utf-8', errors='replace')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.body


class Transport:
    def __init__(self, output, seed, ags, original, budget=20):
        self.output, self.seed, self.ags = output, seed, ags
        self.original = set(original)
        self.allowed = {host('https://' + d) for d in original} | {host(seed)}
        self.budget, self.bytes = budget, 0
        self.events, self.domains, self.blocked = [], [], []
        self.links, self.maps = {}, []
        self.robots, self.robots_responses, self.last = {}, {}, {}
        self.session = requests.Session()
        self.session.trust_env = False  # no netrc credentials/proxy secrets
        self.session.headers.update({'User-Agent': UA, 'Accept-Encoding': 'identity'})
        self.started = time.monotonic()
        self.started_at = utc()
        self.network_errors = 0
        (output/'responses').mkdir(parents=True)

    def allowed_url(self, url):
        p = urlsplit(url)
        return p.scheme in ('https', 'http') and p.username is None and p.password is None and host(url) in self.allowed

    def block(self, url, reason):
        self.blocked.append(dict(url=url, reason=reason, timestamp=utc()))
        raise StopPilot(reason)

    def flush(self):
        (self.output/'transport.json').write_text(json.dumps(dict(
            requests=self.events, domains=self.domains, blocked=self.blocked,
            original_allowlist=sorted(self.original), allowed=sorted(self.allowed),
            bytes=self.bytes, network_errors=self.network_errors), ensure_ascii=False, indent=2))

    def wire(self, url, purpose, parent):
        if len(self.events) >= self.budget:
            self.block(url, 'request_budget_exhausted')
        if self.bytes >= TOTAL or time.monotonic()-self.started >= 300:
            self.block(url, 'byte_or_time_budget_exhausted')
        delay = 1.0
        rp = self.robots.get(origin(url))
        if isinstance(rp, robotparser.RobotFileParser):
            delay = max(delay, rp.crawl_delay(UA) or rp.crawl_delay('*') or 0)
            rate = rp.request_rate(UA)
            if rate and rate.requests:
                delay = max(delay, rate.seconds/rate.requests)
        wait = delay - (time.monotonic()-self.last.get(host(url), 0))
        if wait > 0:
            if time.monotonic()-self.started+wait >= 300:
                self.block(url, 'robots_delay_exceeds_time_budget')
            time.sleep(wait)
        self.last[host(url)] = time.monotonic()
        event = dict(number=len(self.events)+1, ags=self.ags, url=url, purpose=purpose,
                     parent=parent, timestamp_utc=utc(), status=None, content_type=None,
                     bytes=0, error=None, complete=False)
        self.events.append(event)
        start = time.monotonic()
        raw = bytearray()
        try:
            with self.session.get(url, allow_redirects=False, stream=True,
                                  timeout=(8, 15), verify=True) as response:
                event.update(status=response.status_code,
                             content_type=response.headers.get('Content-Type', ''),
                             headers={k:v for k,v in response.headers.items()
                                      if k.lower() in {'content-type','content-encoding','content-length',
                                                      'location','last-modified','etag','x-robots-tag'}})
                max_bytes = min(CAP, TOTAL-self.bytes)
                while len(raw) <= max_bytes:
                    part = response.raw.read(min(65536, max_bytes-len(raw)+1), decode_content=False)
                    if not part:
                        event['complete'] = True
                        break
                    raw.extend(part)
                    self.bytes += len(part)
                if not event['complete']:
                    event['error'] = 'response_size_limit'
                data = bytes(raw)
                event['sha256'] = sha(data)
                event['body_path'] = 'responses/'+event['sha256']
                (self.output/event['body_path']).write_bytes(data)
                body = data if event['complete'] else b''
                encoding = response.headers.get('Content-Encoding', '').lower()
                if encoding in ('gzip','deflate') and body:
                    decoder = zlib.decompressobj(16+zlib.MAX_WBITS if encoding=='gzip' else zlib.MAX_WBITS)
                    body = decoder.decompress(body, CAP+1)
                    if len(body)>CAP or not decoder.eof:
                        body=b''; event['error']='decoded_size_limit'
                elif encoding and encoding != 'identity':
                    body=b''; event['error']='unsupported_content_encoding'
                return Response(url, response.status_code, dict(response.headers), body)
        except requests.RequestException as exc:
            event['error'] = type(exc).__name__+': '+str(exc)
            self.network_errors += 1
            raise StopPilot(event['error']) from exc
        finally:
            event['bytes'] = len(raw)
            event['elapsed_seconds'] = time.monotonic()-start
            self.flush()

    def ensure_robots(self, url):
        base = origin(url)
        if base not in self.robots:
            # Fail closed until a usable robots response or explicit 404/410.
            self.robots[base] = False
            robot_url = base+'/robots.txt'
            response = self.wire(robot_url, 'robots', url)
            for _ in range(3):
                if response.status_code not in (301,302,303,307,308):
                    break
                dest = urljoin(response.url, response.headers.get('Location',''))
                if host(dest)!=host(robot_url) or not self.allowed_url(dest):
                    self.block(dest,'robots_cross_host_redirect_unverified')
                response = self.wire(dest,'robots_redirect',response.url)
            self.robots_responses[base] = response
            if response.status_code in (404,410):
                self.robots[base] = True
            elif response.status_code == 200 and response.body and '<html' not in response.text[:500].lower():
                rp = robotparser.RobotFileParser(); rp.parse(response.text.splitlines())
                self.robots[base] = rp
                self.maps.extend(urljoin(response.url, u) for u in (rp.site_maps() or []))
        rp = self.robots[base]
        if rp is False or (rp is not True and not rp.can_fetch(UA, url)):
            self.block(url,'robots_disallow_or_unavailable')

    def discover(self, response):
        if response.status_code!=200 or 'html' not in response.headers.get('Content-Type','').lower():
            return
        soup = BeautifulSoup(response.body, 'lxml')
        for a in soup.find_all('a', href=True):
            url = urljoin(response.url,a['href']).split('#')[0]
            text = a.get_text(' ',strip=True)
            self.links.setdefault(url,dict(parent=response.url,anchor=text))
            if urlsplit(url).scheme not in ('http','https') or host(url) in self.allowed:
                continue
            # Only the original municipal domain can vouch for an institutional link.
            admitted = host(response.url)==host(self.seed) and bool(INSTITUTION.search(text))
            self.domains.append(dict(url=url, host=host(url), parent=response.url, anchor=text,
                                     original_allowlist_would_block=True, admitted=admitted,
                                     status='municipally_linked_candidate_not_authority_proof'))
            if admitted:
                self.allowed.add(host(url))

    def get(self, url, **ignored):
        if not self.allowed_url(url):
            self.block(url,'domain_not_admitted')
        if urlsplit(url).path=='/robots.txt':
            # Do not ask robots whether robots itself may be read.
            try:
                self.ensure_robots(origin(url)+'/')
            except StopPilot:
                if origin(url) not in self.robots_responses:
                    raise
            return self.robots_responses[origin(url)]
        parent = self.links.get(url,{}).get('parent')
        for _ in range(6):
            if not self.allowed_url(url):
                self.block(url,'redirect_domain_not_admitted')
            self.ensure_robots(url)
            response = self.wire(url,'sitemap' if '.xml' in url else 'document',parent)
            if response.status_code in (301,302,303,307,308):
                parent,url=url,urljoin(url,response.headers.get('Location',''))
                continue
            self.discover(response)
            self.flush()
            return response
        self.block(url,'redirect_limit')


def structured(engine, transport, ags, seed):
    """Homepage -> observed institutional links -> systematic sitemap -> HTML links.

    No target document IDs, search results or evaluation labels guide ordering.
    Only institutional links and sitemap order are prioritized, never inferred Gold.
    """
    queue = deque([(seed,0)])
    seen, maps_seen = set(), set()
    while queue and len(transport.events)<transport.budget:
        url,depth = queue.popleft()
        url = engine.canon.normalize(url)
        if not url or url in seen or depth>6 or not transport.allowed_url(url):
            continue
        seen.add(url)
        try:
            fr=engine.fetch(url)
        except RuntimeError:
            continue
        if fr.status_code!=200 or not fr.body:
            continue
        with engine.storage.transaction():
            doc=engine.storage.store_raw(ags,url,fr)
            if engine._looks_like_html(fr.url_final,fr.content_type):
                parsed=parse_html(fr,fr.url_final)
            elif engine._looks_like_pdf(fr.url_final,fr.content_type):
                parsed=parse_pdf(fr,fr.url_final)
            else:
                continue
            engine.storage.store_segments_scored(doc,parsed.segments,_segment_features)
            engine.storage.store_outlinks(doc,parsed.out_links)
            engine.storage.update_document_meta(doc,parsed.meta)
        institutional=[(u,depth+1) for u,a in parsed.out_links if INSTITUTION.search(a or '') and transport.allowed_url(u)]
        ordinary=[(u,depth+1) for u,a in parsed.out_links if not INSTITUTION.search(a or '')]
        sitemap_links=[]
        for sm in list(transport.maps)[:5]:
            if sm not in maps_seen:
                maps_seen.add(sm)
                sitemap_links.extend((u,1) for u in engine._sitemap_urls(sm))
        queue.extendleft(reversed(institutional+sitemap_links))
        queue.extend(ordinary)


def run(case_id, strategy, output):
    if output.exists():
        raise ValueError('Refuse overwrite or budget reset: use one predeclared run path')
    output.mkdir(parents=True)
    case_ags={'B':'09190118','A':'09564000'}[case_id]
    with sqlite3.connect('file:'+str(ROOT/'crawler/data/db/municipalities.sqlite')+'?mode=ro',uri=True) as con:
        row=con.execute('SELECT homepage_url, allowed_domains FROM municipalities WHERE ags=?',(case_ags,)).fetchone()
    seed, allow=row; original=allow.split('|')
    tasks=list(csv.DictReader((ROOT/'research/experiments/e0a/tasks_instantiated.csv').open()))
    transport=Transport(output,seed,case_ags,original)
    db=output/'pilot.sqlite'
    with Storage(db,raw_dir=output/'raw',create=True):
        pass
    with patch('crawler.core.engine.Storage',side_effect=lambda **kw:Storage(raw_dir=output/'raw',**kw)):
        engine=Engine({},db_path=db,allowed_domains_by_muni={case_ags:set(original)},
                      limits=EngineLimits(max_depth=6,max_pages_per_muni=20,max_file_size_mb=4),
                      user_agent=UA,tls_verify=True,min_delay_seconds_per_domain=0)
    engine._session.close();engine._session=transport
    # Domain admission is a shared control, not a private advantage of either scheduler.
    engine._is_allowed=lambda muni,url:transport.allowed_url(url)
    start=time.monotonic()
    errors=[]
    try:
        with (output/'engine.txt').open('w') as log, redirect_stdout(log):
            if strategy=='heuristic':
                engine.run([(case_ags,seed)])
            else:
                structured(engine,transport,case_ags,seed)
        setup_db(engine.storage.conn)
        process_segments(engine.storage.conn,retain_all=True)
        docs=[dict(r) for r in engine.storage.conn.execute(
            'SELECT document_id,url_canonical,url_final,status_code,content_type,raw_hash,raw_path FROM documents_raw')]
        excerpts=[dict(r) for r in engine.storage.conn.execute(
            'SELECT d.url_final,s.order_index,s.segment_type,s.text,s.heading_context,s.page_ref '
            'FROM segments s JOIN documents_raw d USING(document_id) ORDER BY d.url_final,s.order_index')]
        (output/'segments.json').write_text(json.dumps(excerpts,ensure_ascii=False,indent=2))
        (output/'documents.json').write_text(json.dumps(docs,ensure_ascii=False,indent=2))
    except Exception as exc:
        errors.append(type(exc).__name__+': '+str(exc))
        docs=[]
    finally:
        engine.storage.close();transport.session.close();transport.flush()
    result=dict(case_id=case_id,ags=case_ags,strategy=strategy,seed=seed,
                original_allowlist=original,cutoff_date='2026-10-19',started_at=transport.started_at,ended_at=utc(),
                request_budget=20,requests=len(transport.events),bytes=transport.bytes,
                wall_seconds=time.monotonic()-start,network_errors=transport.network_errors,
                responses=sum(e['status'] is not None for e in transport.events),
                http_200=sum(e['status']==200 for e in transport.events),
                acquired_documents=len([d for d in docs if d['status_code']==200]),
                task_ids=[t['task_id'] for t in tasks if t['case_id']==case_id],
                errors=errors,runner_sha256=sha(Path(__file__).read_bytes()),
                input_hashes={p:sha((ROOT/p).read_bytes()) for p in [
                    'research/experiments/e0a/tasks_instantiated.csv','research/experiments/e0a/cases.csv',
                    'crawler/data/db/municipalities.sqlite']})
    (output/'run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import os
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(ROOT/'.venv/tokenizer-cache'))
    ap=argparse.ArgumentParser();ap.add_argument('--case',choices=['A','B'],required=True)
    ap.add_argument('--strategy',choices=['heuristic','structured_first'],required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    run(args.case,args.strategy,args.output)
