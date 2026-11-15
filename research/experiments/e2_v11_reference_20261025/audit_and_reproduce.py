"""Reuse frozen E2 diagnostics with read-only inputs and isolated reports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / 'research/experiments/e2_offline_20261024'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    paths = [ROOT / '.gitignore', ROOT / 'AGENTS.md']
    for folder in ('research/experiments/e2_offline_20261024',
                   'research/experiments/e2_v11_acquisition_20261024'):
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and not p.is_relative_to(HERE))
    paths.extend(ROOT / name for name in (
        'crawler/core/engine.py', 'crawler/core/models.py',
        'crawler/core/parsers/html_parser.py', 'crawler/core/parsers/pdf_parser.py',
        'research/experiments/e1_selector/fixtures/anchors_dev.csv'))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def execute(out, e1_zip):
    if out.exists():
        raise FileExistsError('Use a fresh output directory')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(OLD))
    import run_diagnostics
    before = inventory()
    out.mkdir(parents=True)
    def guard(event, args):
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'):
            raise RuntimeError('Offline audit forbids network access')
        if event == 'open':
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or \
                      (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and not isinstance(path, int) and not Path(path).resolve().is_relative_to(out):
                raise RuntimeError('Read-only input guard: ' + str(path))
        if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.rename'):
            paths = args[:2] if event == 'os.rename' else args[:1]
            for path in paths:
                if not Path(path).resolve().is_relative_to(out):
                    raise RuntimeError('Mutation outside audit directory: ' + str(path))
    sys.addaudithook(guard)
    run_diagnostics.main(out / 'diagnostic_replay', e1_zip)
    after = inventory()
    assert before == after, 'Protected input changed'
    replay = out / 'diagnostic_replay'
    prior = OLD / 'run03'
    comparable = [p.name for p in replay.iterdir() if p.name not in ('input_lock.json', 'output_manifest.json')]
    equal = {name: digest(replay / name) == digest(prior / name) for name in comparable}
    assert all(equal.values()), equal
    result = {'status': 'PASS', 'protected_file_count': len(before),
              'protected_inputs_unchanged': before == after,
              'network_guard': True, 'write_guard': 'new audit output directory only',
              'replayed_comparable_outputs': equal,
              'excluded_from_byte_comparison': {
                  'input_lock.json': 'run_diagnostics records Python version and environmental SQLite sidecars',
                  'output_manifest.json': 'contains the resulting input_lock digest'},
              'inventory_sha256': before, 'driver_sha256': digest(Path(__file__))}
    (out / 'verification.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'protected_files': len(before),
                      'identical_replay_outputs': len(equal)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--e1-zip', required=True, type=Path)
    args = parser.parse_args()
    execute(args.out.resolve(), args.e1_zip.resolve())
