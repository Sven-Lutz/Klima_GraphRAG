"""Offline review views; explicit new namespace, not historical E2 canonical units."""
from pathlib import Path
import hashlib
import json
import platform
import subprocess
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup
from crawler.core.models import FetchResult
from crawler.core.parsers.pdf_parser import parse_pdf
source = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve(); out.mkdir(exist_ok=False)
records = [json.loads(line) for line in (source / 'catalog.jsonl').read_text().splitlines()]
units = []
for rec in records:
    body = (source / rec['raw_path']).read_bytes()
    assert hashlib.sha256(body).hexdigest() == rec['body_sha256']
    if rec['source_type'] == 'pdf':
        result = parse_pdf(FetchResult(rec['url'], 200, rec['content_type'], body, {}), rec['url'])
        assert result.text.strip(), 'PDF extraction failed'
        pages = [(i + 1, text) for i, text in enumerate(result.text.split('\x0c')) if text.strip()]
        method = 'existing parse_pdf.text split at preserved form-feed; page view, not segment export'
    else:
        soup = BeautifulSoup(body, 'lxml')
        for tag in soup.find_all(['script','style','noscript','svg']): tag.decompose()
        pages = [(None, soup.get_text('\n', strip=True))]
        method = 'BeautifulSoup lxml full HTML text review view; not production HTML segmentation'
    for page, text in pages:
        units.append({'view_id': rec['source_id'] + ('-P' + str(page) if page else '-HTML'), 'source_id': rec['source_id'], 'source_url': rec['url'], 'body_sha256': rec['body_sha256'], 'fetched_at_utc': rec['fetched_at_utc'], 'page': page, 'text': text, 'view_text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'method': method, 'status': 'REVIEW_VIEW_NOT_E2_CANONICAL_UNIT'})
with (out / 'review_views.jsonl').open('x') as f:
    for u in units: f.write(json.dumps(u, ensure_ascii=False) + '\n')
metadata = {'purpose': 'OFFLINE_DEVELOPMENT_REVIEW_NOT_BENCHMARK_REFERENCE', 'source_count': len(records), 'review_view_count': len(units), 'pages_per_pdf': {r['source_id']: sum(u['source_id'] == r['source_id'] for u in units) for r in records if r['source_type'] == 'pdf'}, 'python': platform.python_version(), 'pdf_parser_sha256': hashlib.sha256((ROOT / 'crawler/core/parsers/pdf_parser.py').read_bytes()).hexdigest(), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'pdftotext_version': subprocess.run(['pdftotext','-v'], capture_output=True, text=True, timeout=5).stderr.splitlines()[0]}
with (out / 'view_manifest.json').open('x') as f: json.dump(metadata, f, ensure_ascii=False, indent=2); f.write('\n')
assert metadata['pages_per_pdf'] == {'WP26-S02': 29, 'WP26-S03': 4}
print(json.dumps(metadata, ensure_ascii=False))
for u in units:
    for marker in ['16. Oktober 2024', '11.11.2024', 'Dazu wird 2026', '700 kW Wärmeleistung', 'Satelliten-BHKW am Riedbach']:
        if marker in u['text']:
            i = u['text'].index(marker)
            print(u['view_id'], repr(u['text'][max(0,i-70):i+len(marker)+100]))
