"""Check released arithmetic/provenance, not independent scientific validity."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads((ROOT / path).read_text())


def main():
    for study in read('docs/research/ARTIFACTS.json')['studies']:
        payload = (ROOT / study['public_path']).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == study['public_sha256']
        assert study['independent_human_validation'] is False
    ie = read('docs/research/artifacts/ie-1.7b-summary.json')
    assert ie['cases'] == ie['components'] == 8
    assert ie['independent_human_judgments'] == 0
    for arm in ie['arms'].values():
        assert arm['slots'] == 56
        assert arm['positive_slots'] + arm['unknown_slots'] == arm['slots']
        assert arm['tp'] + arm['fn'] == arm['positive_slots']
        expected = 2 * arm['tp'] / (2 * arm['tp'] + arm['fp'] + arm['fn'])
        assert abs(arm['f1'] - expected) < 1e-12
    report = read('docs/research/artifacts/retrieval03-report.json')
    assert report['tasks'] == 38 and report['sources'] == 35
    assert report['evaluation_dependency_components'] == 18
    assert report['graph_sql_all_tasks_same_relations'] is True
    assert report['confidence_intervals'] is None
    arms = report['summary_evaluation_provisional_positive']
    for arm in arms.values():
        assert arm['tasks'] == 28 and arm['known_evidence'] == 46
        assert arm['known_relations'] == 66
        assert 0 <= arm['complete_known_bundles'] <= arm['tasks']
        assert arm['captured_known_evidence'] <= arm['known_evidence']
        assert arm['represented_known_relations'] <= arm['known_relations']
    assert arms['sql_relations'] == arms['graph_traversal']
    synthetic = read('docs/research/artifacts/ds006-current-result.json')
    assert synthetic['synthetic'] is True
    assert synthetic['summary']['B']['held_out']['chunk_present'] == 4
    print('PASS: released aggregate arithmetic, denominators and provenance hashes; '
          'no independent annotation or original-study replay inferred.')


if __name__ == '__main__':
    main()
