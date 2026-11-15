"""Offline software-preservation proof, not a scientific evaluation.

Run from any directory. --out must be a new file; no historical artifacts are written.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import importlib.util
import json
import socket
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def denied(*args, **kwargs):
    raise RuntimeError('Network disabled for parser repair verification')


socket.socket = denied
socket.create_connection = denied
from crawler.core.models import FetchResult


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function_ast(path, name):
    tree = ast.parse(path.read_text())
    return ast.dump(next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError('Refusing to overwrite proof output')
    before = HERE / 'parser_before.py'
    after = ROOT / 'crawler/core/parsers/html_parser.py'
    protected = json.loads((HERE / 'protected_before.json').read_text())
    changed = [name for name, value in protected.items() if sha(ROOT / name) != value]
    assert not changed, changed
    body_hash = 'e4d4bc3858a691a4b0756ac1853cf5d1656fdae6bab34c868c164b2382beadbd'
    raw = ROOT / 'research/experiments/e2_v11_acquisition_20261024/export/raw' / body_hash
    assert sha(raw) == body_hash
    url = 'https://www.wildpoldsried.de/erneuerbare-energien.html'
    fetch = FetchResult(url, 200, 'text/html; charset=utf-8', raw.read_bytes(), {})
    old = load(before, '_before_parser').parse_html(fetch, url)
    new = load(after, '_repaired_parser').parse_html(fetch, url)
    terms = ['Inbetriebnahme November 2005', 'Dorfentwicklungs-GmbH', 'Daten 2020', '3.572.280']
    old_text = '\n'.join(s.text for s in old.segments)
    new_text = '\n'.join(s.text for s in new.segments)
    assert all(t not in old_text and t in new_text for t in terms)
    assert old.text == new.text
    assert old.out_links == new.out_links
    assert old.meta == new.meta
    assert [s.text for s in old.segments if s.segment_type == 'table'] == [
        s.text for s in new.segments if s.segment_type == 'table']
    assert function_ast(before, '_table_text') == function_ast(after, '_table_text')
    assert function_ast(before, '_build_heading_context') == function_ast(after, '_build_heading_context')
    report = {
        'purpose': 'TECHNICAL_PARSER_PRESERVATION_NOT_RESEARCH_QUALITY',
        'status': 'PASS', 'source_body_sha256': body_hash,
        'before_parser_sha256': sha(before), 'after_parser_sha256': sha(after),
        'regression_tests_sha256': sha(ROOT / 'tests/test_html_parser_content_repair.py'),
        'verification_script_sha256': sha(Path(__file__)),
        'protected_files_checked': len(protected), 'protected_files_changed': changed,
        'same_full_text': old.text == new.text, 'same_outlinks': old.out_links == new.out_links,
        'same_metadata': old.meta == new.meta, 'same_table_texts': True,
        'before_segments': len(old.segments), 'after_segments': len(new.segments),
        'markers_before': {t: t in old_text for t in terms},
        'markers_after': {t: t in new_text for t in terms},
        'marker_contexts': {t: [{'order': s.order_index, 'text': s.text,
                                'heading_context': s.heading_context}
                               for s in new.segments if t in s.text] for t in terms},
        'historical_ids_reusable': False, 'scientific_quality_claim': None,
    }
    with args.out.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'marker_contexts'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
