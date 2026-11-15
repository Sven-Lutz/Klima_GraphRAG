"""Real parser/storage/scorer/chunker; synthetic oracle, network disabled."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import tempfile
from time import perf_counter
from unittest.mock import patch

from bs4 import BeautifulSoup

from crawler.core.engine import _segment_features
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.storage import Storage
from crawler.scripts.chunk_documents import process_segments, setup_db
from research.experiments.ds006.fixtures import fixtures

ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / ".venv/tokenizer-cache"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def matches(text, context, needles):
    combined = BeautifulSoup(text, "html.parser").get_text(" ", strip=True) + " " + (context or "")
    return all(n in combined for n in needles)


def measure(variant, selected):
    start = perf_counter()
    matrix = []
    with tempfile.TemporaryDirectory(prefix="ds006-") as tmp:
        with Storage(Path(tmp) / "fixture.sqlite", raw_dir=Path(tmp) / "raw", create=True) as store:
            docs = []
            for item in selected:
                raw = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
                       + item["html"] + '</body></html>').encode()
                fr = FetchResult(item["url"], 200, "text/html", raw, {})
                doc = store.store_raw(item["municipality"], item["url"], fr)
                parsed = parse_html(fr, item["url"])
                store.store_segments_scored(doc, parsed.segments, _segment_features)
                store.store_outlinks(doc, parsed.out_links)
                docs.append((item, doc, raw, parsed))
            setup_db(store.conn)
            chunk_start = perf_counter()
            if variant == "A":
                process_segments(store.conn)
            else:
                process_segments(store.conn, retain_all=True)
            chunk_seconds = perf_counter() - chunk_start
            for item, doc, raw, parsed in docs:
                segments = store.conn.execute(
                    "SELECT * FROM segments WHERE document_id=? ORDER BY order_index", (doc,)
                ).fetchall()
                chunks = store.conn.execute(
                    "SELECT c.*, d.url_canonical, d.raw_hash FROM chunks c "
                    "JOIN documents_raw d ON c.document_id=d.document_id WHERE c.document_id=?", (doc,)
                ).fetchall()
                relevant = [s for s in segments if matches(s['text'], s['heading_context'], item['needles'])]
                admitted = [s for s in relevant if variant == 'B' or (
                    len(s['text']) >= 80 and not s['is_negative'] and s['impact_score'] >= 10)]
                found = [c for c in chunks if matches(c['text'], c['heading_context'], item['needles'])]
                lineage = any(c['segment_id'] in {s['segment_id'] for s in relevant}
                              and c['municipality_id'] == item['municipality']
                              and c['url_canonical'] == item['url'] and c['raw_hash'] == digest(raw)
                              and c['heading_context'] == next(s['heading_context'] for s in relevant
                                                               if s['segment_id']==c['segment_id'])
                              for c in found)
                # Oracle is evaluation-only. No semantic attribution is implemented by the pipeline.
                wrong = bool(found) and bool(item['subjects']) and item['municipality'] not in item['subjects']
                unresolved = bool(found) and not item['subjects']
                row = dict(variant=variant, fixture=item['id'], split=item['split'], kind=item['kind'],
                           relevant=item['relevant'], source_present=matches(item['html'], '', item['needles']),
                           parsed=any(matches(s.text, s.heading_context, item['needles']) for s in parsed.segments),
                           stored=bool(relevant), admitted=bool(admitted), chunk_present=bool(found),
                           lineage_preserved=lineage, subject_correct=bool(found) and not wrong and not unresolved,
                           wrong_subject=wrong, subject_unresolved=unresolved, raw_sha256=digest(raw),
                           input_municipality=item['municipality'], oracle_subjects=item['subjects'],
                           source_url=item['url'],
                           target_scoped_hit=bool(found) and item['municipality']=='SYN-A',
                           segment_scores=[s['impact_score'] for s in relevant],
                           segment_lengths=[len(s['text']) for s in relevant],
                           locators=[dict(order=s['order_index'], type=s['segment_type'],
                                          heading=s['heading_context'], page=s['page_ref']) for s in relevant])
                if item['kind'] == 'discovery':
                    row.update(parsed=bool(parsed.out_links), stored=bool(store.conn.execute(
                        'SELECT 1 FROM document_links WHERE source_document_id=?', (doc,)).fetchone()),
                        admitted=None, chunk_present=None, lineage_preserved=None, subject_correct=None)
                row['first_loss'] = next((k for k in ['source_present', 'parsed', 'stored', 'admitted',
                                                      'chunk_present', 'lineage_preserved', 'subject_correct']
                                          if row[k] is False), None)
                matrix.append(row)
            counts = dict(segments=store.conn.execute('SELECT count(*) FROM segments').fetchone()[0],
                          chunks=store.conn.execute('SELECT count(*) FROM chunks').fetchone()[0],
                          tokens=store.conn.execute('SELECT coalesce(sum(token_count),0) FROM chunks').fetchone()[0])
    return matrix, dict(wall_seconds=perf_counter()-start, chunk_seconds=chunk_seconds, **counts)


def run(variants=("A", "B"), repetitions=3, split=None):
    selected = [x for x in fixtures() if split is None or x['split'] == split]
    result = dict(synthetic=True, tokenizer='cl100k_base', chunk_size=600, overlap=60,
                  segment_budget=100000, retrieval='exact source-linked SQLite evidence lookup; no semantic retrieval',
                  input_sha256=digest(json.dumps(selected, sort_keys=True).encode()), matrix=[], costs={})
    # Block all socket creation, including accidental tokenizer cache downloads.
    with patch.object(socket, 'socket', side_effect=RuntimeError('DS006 prohibits network')):
        from crawler.core.segmentation.chunker import _get_encoding
        _get_encoding('cl100k_base')
        samples = {v: [] for v in variants}
        for rep in range(repetitions):
            for v in (variants if rep % 2 == 0 else tuple(reversed(variants))):
                matrix, cost = measure(v, selected)
                if rep == 0:
                    result['matrix'].extend(matrix)
                samples[v].append(cost)
        for v, costs in samples.items():
            result['costs'][v] = dict(samples=costs, median_wall_seconds=statistics.median(
                c['wall_seconds'] for c in costs))
    result['summary'] = {}
    for v in variants:
        result['summary'][v] = {}
        for part in ('development', 'held_out'):
            rows = [r for r in result['matrix'] if r['variant']==v and r['split']==part and r['kind']=='evidence']
            positive = [r for r in rows if r['relevant']]
            result['summary'][v][part] = dict(expected=len(positive),
                chunk_present=sum(bool(r['chunk_present']) for r in positive),
                correctly_attributed=sum(bool(r['subject_correct']) for r in positive),
                wrong_subject=sum(r['wrong_subject'] for r in positive),
                unresolved_subject=sum(r['subject_unresolved'] for r in positive),
                distractors_retained=sum(bool(r['chunk_present']) for r in rows if not r['relevant']))
    return result


def write(result, output):
    output.mkdir(parents=True, exist_ok=False)
    (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (output/'loss_matrix.csv').open('w') as f:
        rows=result['matrix']; writer=csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v,(list,dict)) else v
                             for k,v in row.items()})
    tracked = ['crawler/core/parsers/html_parser.py','crawler/core/storage.py','crawler/core/engine.py',
               'crawler/scripts/chunk_documents.py','crawler/core/segmentation/chunker.py',
               'research/experiments/ds006/fixtures.py','research/experiments/ds006/continuity.py']
    import importlib.metadata
    manifest=dict(python=platform.python_version(), sources={p:digest((ROOT/p).read_bytes()) for p in tracked},
                  packages={p:importlib.metadata.version(p) for p in ['beautifulsoup4','lxml','tiktoken','requests']},
                  tokenizer_files={p.name:digest(p.read_bytes()) for p in Path(os.environ['TIKTOKEN_CACHE_DIR']).glob('*') if p.is_file()})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--baseline-only',action='store_true'); ap.add_argument('--split', choices=['development','held_out'])
    args=ap.parse_args(); result=run(('A',) if args.baseline_only else ('A','B'),split=args.split)
    write(result,args.output); print(json.dumps(result['summary'],indent=2)); print(result['costs'])
