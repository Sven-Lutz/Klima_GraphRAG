"""Descriptive costs only; fixed local tokenizer, no quality scoring or network."""
import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]


def main(run, out):
    if out.exists():
        raise FileExistsError('Use a fresh output directory')
    cache = ROOT / '.venv/tokenizer-cache'
    vocabulary = cache / '9b5ad71b2ce5302211f9c61530b329a4922fc6a4'
    assert hashlib.sha256(vocabulary.read_bytes()).hexdigest() == \
        '223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(cache)
    def no_network(event, args):
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'):
            raise RuntimeError('Local tokenizer only')
    sys.addaudithook(no_network)
    import tiktoken
    encoding = tiktoken.get_encoding('cl100k_base')
    passages = [json.loads(line) for line in (run / 'selected_passages.jsonl').read_text().splitlines()]
    with (run / 'budget_ledger.csv').open() as file:
        ledger = list(csv.DictReader(file))
    rows = []
    fields = ('unit_id', 'parent_unit_id', 'source_url', 'raw_hashes', 'text', 'heading_context', 'page_ref')
    for row in ledger:
        selected = sorted([p for p in passages if p['task_id'] == row['task_id'] and
                           p['policy'] == row['policy']], key=lambda p: p['rank'])
        context = json.dumps([{k: p[k] for k in fields} for p in selected],
                             ensure_ascii=False, sort_keys=True, separators=(',', ':')) if selected else ''
        text = '\n\n'.join(p['text'] for p in selected)
        rows.append({'task_id': row['task_id'], 'policy': row['policy'],
                     'accounted_chars': int(row['cost_chars']), 'passages': len(selected),
                     'text_tokens': len(encoding.encode(text, disallowed_special=())),
                     'serialized_context_chars': len(context),
                     'serialized_context_tokens': len(encoding.encode(context, disallowed_special=())),
                     'quality_status': 'NOT_ESTIMABLE'})
    summary = []
    for policy in sorted({r['policy'] for r in rows}):
        subset = [r for r in rows if r['policy'] == policy]
        summary.append({'policy': policy, 'tasks': len(subset),
                        'mean_text_tokens': sum(r['text_tokens'] for r in subset) / len(subset),
                        'mean_context_tokens': sum(r['serialized_context_tokens'] for r in subset) / len(subset),
                        'max_context_tokens': max(r['serialized_context_tokens'] for r in subset)})
    out.mkdir(parents=True)
    for name, data in [('per_task.csv', rows), ('summary.csv', summary)]:
        with (out / name).open('x', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=list(data[0]))
            writer.writeheader(); writer.writerows(data)
    manifest = {'status': 'DESCRIPTIVE_COST_ONLY', 'tokenizer': 'cl100k_base',
                'tiktoken_version': importlib.metadata.version('tiktoken'),
                'vocabulary_sha256': hashlib.sha256(vocabulary.read_bytes()).hexdigest(),
                'serialization': {'format': 'compact JSON list, UTF-8, ensure_ascii=False, sort_keys=True',
                                  'fields': fields, 'empty_context': 'empty string'},
                'note': 'No prompt, response, model API or runtime benchmark. Token counts do not change frozen character budgets.',
                'inputs_sha256': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                                  for name in ('selected_passages.jsonl', 'budget_ledger.csv')},
                'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    main(args.run.resolve(), args.out.resolve())
