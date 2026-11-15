"""Build an exhaustive, ranking-free annotation frame. Never produces qrels."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ACQ = ROOT / 'research/experiments/e2_v11_acquisition_20261024'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def csv_write(path, rows):
    with path.open('x', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare(out):
    if out.exists():
        raise FileExistsError('Use a fresh output directory')
    sources = [ACQ / 'export' / name for name in ('catalog.jsonl', 'corpus.jsonl')]
    sources += [ACQ / 'protocol/frozen' / name for name in
                ('tasks_frozen.csv', 'cases_frozen.csv', 'freeze_manifest.json')]
    before = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    catalog = read_jsonl(sources[0])
    units = read_jsonl(sources[1])
    with sources[2].open() as file:
        tasks = list(csv.DictReader(file))
    assert len(catalog) == 57 and len(units) == 894 and len(tasks) == 16
    out.mkdir(parents=True)
    documents = []
    matrix = []
    packet = []
    for index, c in enumerate(sorted(catalog, key=lambda x: (x['ags'], x['url'], x['body_sha256'])), 1):
        raw = ACQ / 'export/raw' / c['body_sha256']
        assert digest(raw) == c['body_sha256']
        members = [u for u in units if u['acquisition_ags'] == c['ags'] and
                   u['source_url'] == c['url'] and c['body_sha256'] in u['raw_hashes']]
        members.sort(key=lambda u: (min(o['segment_order'] for o in u['origins']), u['unit_id']))
        doc_id = f'D{index:03d}'
        documents.append({'document_id': doc_id, **c,
                          'raw_path_from_repository': str(raw.relative_to(ROOT)),
                          'unit_count': len(members), 'review_status': 'NOT_ASSESSED'})
        for u in members:
            packet.append({'document_id': doc_id, **u})
        for task in sorted(tasks, key=lambda t: t['task_id']):
            if task['ags'] == c['ags']:
                matrix.append({'document_id': doc_id, 'task_id': task['task_id'],
                               'ags': c['ags'], 'url': c['url'], 'body_sha256': c['body_sha256'],
                               'review_status': 'NOT_ASSESSED', 'reviewer_id': '',
                               'reviewed_at': '', 'evidence_ids': '', 'reason': ''})
    assert len(packet) == len(units) and len({u['unit_id'] for u in packet}) == len(units)
    assert len(matrix) == 228
    for name, rows in [('documents.jsonl', documents), ('units_source_order.jsonl', packet)]:
        with (out / name).open('x', encoding='utf-8') as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    csv_write(out / 'document_task_matrix.csv', matrix)
    csv_write(out / 'tasks_frozen.csv', tasks)
    protocol = Path(__file__).with_name('REFERENCE_PROTOCOL.md')
    (out / protocol.name).write_bytes(protocol.read_bytes())
    manifest = {'status': 'UNANNOTATED_SOURCE_CENSUS_NOT_GOLD',
                'ranking_inputs_read': False, 'positive_labels': 0,
                'documents': len(documents), 'units': len(packet), 'document_task_pairs': len(matrix),
                'source_sha256': before, 'code_sha256': digest(Path(__file__)),
                'protocol_sha256': digest(protocol),
                'ordering': 'AGS, URL, SHA256, original segment_order',
                'independent_adjudication': 'REQUIRED_NOT_COMPLETED',
                'outputs_sha256': {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    assert before == {str(p.relative_to(ROOT)): digest(p) for p in sources}
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    print(json.dumps(prepare(parser.parse_args().out.resolve()), indent=2))
