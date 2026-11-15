"""Targeted source follow-up; reuse historical fetch/robots helpers, no discovery queue.
Run from project root. New output required; never writes historical artifacts.
"""
import ast
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import requests
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
(OUT / 'raw').mkdir()
HELPERS = ROOT / 'research/experiments/e2_v11_acquisition_20261024/acquire.py'
UA = 'E2ResearchSourceAcquisition/1.1'
LIMIT = 5 * 1024 * 1024
session = requests.Session()
session.trust_env = False
session.headers.update({'User-Agent': UA, 'Accept-Encoding': 'identity'})
last = {}; delays = {}; robots = {}; counts = {'09780147': 0}; attempts = {'09780147': 0}
failures = []; log = []; skipped = []
def allowed(ags, url):
    p = urlsplit(url)
    return ags == '09780147' and p.scheme == 'https' and p.hostname == 'www.wildpoldsried.de' and not p.username and p.port in (None, 443) and attempts[ags] < 8
names = {'now', 'append', 'one', 'fetch', 'robot_ok'}
tree = ast.parse(HELPERS.read_text())
selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(selected) == len(names)
exec(compile(ast.Module(body=selected, type_ignores=[]), str(HELPERS), 'exec'), globals())
class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href' and v)
base = 'https://www.wildpoldsried.de/'
page = base + 'kommunale-waermeplanung.html'
targets = [
    (page, 'previously archived D054: source link in content area'),
    (base + 'download/20241216-abschlussbericht-kommunale-waermeplanung.pdf', page),
    (base + 'download/2026-27-06_buergernahe_projektbeschreibung.pdf', page),
    (base + 'impressum.html', page),
]
records = []; actual_links = set()
try:
    for i, (url, parent) in enumerate(targets, 1):
        if i > 1 and url not in actual_links:
            raise RuntimeError('Fixed follow-up URL is not linked from the fetched source page')
        got = fetch('09780147', url, 'document', parent)
        if not got:
            raise RuntimeError('Robots policy did not allow fixed source')
        e, body = got
        if e['status'] != 200:
            raise RuntimeError(f"HTTP {e['status']}: {url}")
        typ = e['headers'].get('Content-Type', e['headers'].get('content-type', '')).lower()
        if i in (2, 3): assert body.startswith(b'%PDF'), 'Expected PDF source'
        else: assert 'html' in typ, 'Expected HTML source'
        if i == 1:
            h = Links(); h.feed(body.decode('utf-8'))
            actual_links = {urljoin(page, link) for link in h.links}
        rec = {'source_id': f'WP26-S{i:02d}', 'ags': '09780147', 'url': e['url'], 'requested_url': url, 'fetched_at_utc': e['acquisition_time'], 'body_sha256': e['body_sha256'], 'bytes': len(body), 'content_type': typ, 'source_type': 'pdf' if i in (2, 3) else 'html', 'raw_path': 'raw/' + e['body_sha256'], 'discovered_from': parent, 'redirects': e['redirects'], 'document_date': None, 'review_status': 'NOT_ADJUDICATED', 'historical_cutoff_eligibility': 'NOT_ESTABLISHED'}
        records.append(rec)
        append('catalog.jsonl', rec)
        print(rec['source_id'], len(body), e['body_sha256'], flush=True)
    status = 'COMPLETE_FIXED_SOURCE_CAPTURE'
except Exception:
    status = 'INCOMPLETE_FIXED_SOURCE_CAPTURE'
    raise
finally:
    data = {'status': status, 'purpose': 'DEVELOPMENT_CASE_FOLLOWUP_NOT_PROSPECTIVE_E2', 'network_scope': 'four fixed documentary URLs, robots and bounded same-host redirects only; no link discovery queue', 'hard_http_attempt_cap': 8, 'http_attempts': attempts['09780147'], 'responses': counts['09780147'], 'document_count': len(records), 'failures': failures, 'skipped': skipped, 'acquire_helper_sha256': hashlib.sha256(HELPERS.read_bytes()).hexdigest(), 'fetch_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'requests_version': requests.__version__, 'original_export_modified': False, 'source_completeness_claim': False, 'scientific_independence_claim': False}
    with (OUT / 'capture_manifest.json').open('x') as f: json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')
