"""Small synthetic probes; does not modify parser files or original data."""
from pathlib import Path
import importlib.util
import json
import socket
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
def denied(*a, **kw):
    raise RuntimeError('Network access disabled')
socket.socket = denied
socket.create_connection = denied
from crawler.core.models import FetchResult
path = ROOT / 'research/experiments/e2_v11_acquisition_20261024/implementation_snapshot/crawler/core/parsers/html_parser.py'
spec = importlib.util.spec_from_file_location('_frozen_parser_probes', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
long_text = 'Dorfentwicklungs-GmbH Wildpoldsried ist Betreiber der Dorfheizung.'
short_text = 'Wärmeerzeugung: 3.572.280 kWh'
def probe(html, marker):
    r = m.parse_html(FetchResult('https://example.invalid/', 200, 'text/html', html.encode(), {}), 'https://example.invalid/')
    return {'marker_in_fulltext': marker in r.text, 'marker_in_segments': any(marker in s.text for s in r.segments), 'segments': [s.text for s in r.segments]}
results = {}
results['long_div'] = probe(f'<div>{long_text}</div>', long_text)
results['long_p'] = probe(f'<p>{long_text}</p>', long_text)
results['short_li_default_threshold'] = probe(f'<ul><li>{short_text}</li></ul>', short_text)
original_threshold = m.MIN_SEGMENT_LENGTH
try:
    m.MIN_SEGMENT_LENGTH = 0
    results['short_li_threshold_zero_in_memory'] = probe(f'<ul><li>{short_text}</li></ul>', short_text)
finally:
    m.MIN_SEGMENT_LENGTH = original_threshold
assert not results['long_div']['marker_in_segments']
assert results['long_p']['marker_in_segments']
assert not results['short_li_default_threshold']['marker_in_segments']
assert results['short_li_threshold_zero_in_memory']['marker_in_segments']
print(json.dumps({'purpose': 'SYNTHETIC_CAUSAL_PROBES_NOT_RESEARCH_SCORES', 'original_threshold': original_threshold, 'short_text_length': len(short_text), 'results': results}, ensure_ascii=False, indent=2))
