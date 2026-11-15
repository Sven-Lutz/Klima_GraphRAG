"""Offline preservation check on one archived source; exits 1 on observed loss."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import socket
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
def denied(*args, **kwargs):
    raise RuntimeError('Network access disabled for offline source replay')
socket.socket = denied
socket.create_connection = denied
from bs4 import BeautifulSoup
from crawler.core.models import FetchResult
p = argparse.ArgumentParser()
p.add_argument('--parser', default='research/experiments/e2_v11_acquisition_20261024/implementation_snapshot/crawler/core/parsers/html_parser.py')
a = p.parse_args()
parser = (ROOT / a.parser).resolve()
spec = importlib.util.spec_from_file_location('_offline_parser_under_review', parser)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
body_hash = 'e4d4bc3858a691a4b0756ac1853cf5d1656fdae6bab34c868c164b2382beadbd'
raw = ROOT / 'research/experiments/e2_v11_acquisition_20261024/export/raw' / body_hash
body = raw.read_bytes()
assert hashlib.sha256(body).hexdigest() == body_hash
url = 'https://www.wildpoldsried.de/erneuerbare-energien.html'
result = module.parse_html(FetchResult(url, 200, 'text/html; charset=utf-8', body, {}), url)
original_text = ' '.join(BeautifulSoup(body, 'lxml').stripped_strings)
segmented = '\n'.join(s.text for s in result.segments)
terms = ['Inbetriebnahme November 2005', 'Dorfentwicklungs-GmbH', 'Daten 2020', '3.572.280']
assert all(t in original_text for t in terms), 'Archived original no longer contains expected source markers'
corpus = ROOT / 'research/experiments/e2_v11_reference_20261025/reference01/units_source_order.jsonl'
frozen = [json.loads(line) for line in corpus.read_text().splitlines()]
frozen_texts = [u['text'] for u in frozen if u['document_id'] == 'D053']
checks = {term: term in segmented for term in terms}
report = {'purpose': 'OFFLINE_SOURCE_PRESERVATION_DIAGNOSIS', 'parser': str(parser.relative_to(ROOT)), 'parser_sha256': hashlib.sha256(parser.read_bytes()).hexdigest(), 'body_sha256': body_hash, 'segment_count': len(result.segments), 'matches_frozen_texts': [s.text for s in result.segments] == frozen_texts, 'markers_in_segments': checks, 'markers_in_parse_result_text': {t: t in result.text for t in terms}, 'status': 'PASS' if all(checks.values()) else 'FAIL_INFORMATION_LOSS'}
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if all(checks.values()) else 1)
