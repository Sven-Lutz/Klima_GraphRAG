"""Read-only Sprint 1 wrapper around existing E2 contracts and diagnostics.

No acquisition, labels, retrieval experiments or mutation of historical inputs.
Output directory must be absent. Stdlib audit plus optional frozen parser replay.
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
ACQ = ROOT / 'research/experiments/e2_v11_acquisition_20261024'
DIAG = ROOT / 'research/experiments/e2_offline_20261024'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def jsonl(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')

def main(out):
    if out.exists():
        raise FileExistsError('Output must be absent: ' + str(out))
    sys.dont_write_bytecode = True
    inputs = sorted(p for p in ACQ.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    before = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    out.mkdir(parents=True)
    def guard(event, args):
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'):
            raise RuntimeError('Offline audit forbids network access')
        if event == 'open':
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and not isinstance(path, int) and not Path(path).resolve().is_relative_to(out):
                raise RuntimeError('Write outside new audit output: ' + str(path))
    sys.addaudithook(guard)
    pc = load_module('sprint01_provenance', ACQ/'protocol/provenance_contract.py')
    # Existing diagnostics supply exact/normalized grouping, near-duplicate and
    # visible-text algorithms. Importing does not invoke its retrieval runner.
    sys.path.insert(0, str(DIAG))
    import run_diagnostics as diag
    ex = ACQ/'export'
    catalog, units, requests = (jsonl(ex/n) for n in ('catalog.jsonl','corpus.jsonl','requests.jsonl'))
    original = json.loads((ex/'manifest.json').read_text())
    checked = []
    for line in (ACQ/'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = (ACQ/name).resolve()
        if not path.is_relative_to(ACQ):
            raise ValueError('Unsafe original checksum path')
        if sha(path) != expected:
            raise ValueError('Original checksum mismatch: ' + name)
        checked.append(name)
    keys = pc.load_catalog(catalog, ex/'raw')
    raw = sorted((ex/'raw').iterdir())
    for path in raw:
        if sha(path) != path.name:
            raise ValueError('Raw body hash mismatch: ' + path.name)
    # Reuse acquisition validator without writing its legacy output file.
    text = (ACQ/'validate.py').read_text()
    old = "(out/'validation.json')"
    if text.count(old) != 1:
        raise ValueError('Unexpected legacy validator report destination')
    text = text.replace(old, 'Path(' + repr(str(out/'acquisition_validation.json')) + ')')
    exec(compile(text, str(ACQ/'validate.py'), 'exec'), {'__file__': str(ACQ/'validate.py'), '__name__': '__audit__'})
    bykey = {(c['ags'], c['url'], c['body_sha256']): c for c in catalog}
    for u in units:
        for h in u['raw_hashes']:
            if (u['acquisition_ags'], u['source_url'], h) not in keys:
                raise ValueError('Unit source linkage failed')
    provenance = []
    for i,c in enumerate(catalog, 1):
        for p in c['institutional_provenance']:
            k = (c['ags'], p['url'], p['body_sha256'])
            if k not in keys or urlsplit(c['url']).hostname != urlsplit(p['url']).hostname:
                raise ValueError('Publisher evidence linkage/host mismatch')
            parser = diag.VisibleText()
            parser.feed((ex/'raw'/p['body_sha256']).read_bytes().decode('utf-8', errors='replace'))
            visible = ' '.join(parser.parts)
            if diag.norm(p['institution_literal']) not in diag.norm(visible) or diag.norm(p['context']) not in diag.norm(visible):
                raise ValueError('Publisher literal/context absent in raw visible text')
        doc_units = [u for u in units if (c['ags'], c['url'], c['body_sha256']) in diag.selector.version_keys(u)]
        exported_imprint = ' '.join(u['text'] for u in units if any((c['ags'],p['url'],p['body_sha256']) in diag.selector.version_keys(u) for p in c['institutional_provenance']))
        provenance.append({'document_id': 'E2-D%03d'%i, 'source_version_id': hashlib.sha256(json.dumps([c['ags'],c['url'],c['body_sha256']], separators=(',',':')).encode()).hexdigest(), 'acquisition_ags':c['ags'], 'source_url':c['url'], 'raw_body_sha256':c['body_sha256'], 'raw_path':str((ex/'raw'/c['body_sha256']).relative_to(ROOT)), 'acquired_at':c['acquisition_time'], 'publisher':c['publishing_institution'], 'authority_basis':c['provenance_status'], 'institutional_self_identification_raw_verified':True, 'publisher_literal_in_parsed_imprint':all(diag.norm(p['institution_literal']) in diag.norm(exported_imprint) for p in c['institutional_provenance']), 'document_date':c['document_date'], 'cutoff_eligibility':c['cutoff_eligibility'], 'document_jurisdiction':c['territorial_scope'], 'affected_municipalities':None, 'claim_jurisdiction':'NOT_ASSESSED', 'source_type':c['source_type'], 'bytes':c['bytes'], 'parsed_units':len(doc_units), 'parsed_characters':sum(len(u['text']) for u in doc_units), 'decision':'INCLUDE_FOR_DIAGNOSTICS_ONLY', 'quality_gate':'NO_INDEPENDENT_CONTENT_ANNOTATION'})
    exact = diag.groups(units, lambda u:u['text'])
    normalized = diag.groups(units, lambda u:diag.norm(u['text']))
    near = diag.near_pairs(units)
    write_json(out/'duplicates.json', {'exact_groups':exact,'normalized_groups':normalized,'near_pairs':near,'near_definition':'existing 5-word shingle Jaccard >=0.90, text length >=100 characters; exact normalized pairs excluded'})
    code = {}
    for name, expected in original['source_code_sha256'].items():
        frozen = ACQ/'implementation_snapshot'/name
        code[name] = {'original_expected_sha256':expected,'snapshot_sha256':sha(frozen),'current_sha256':sha(ROOT/name),'snapshot_matches_original':sha(frozen)==expected,'current_matches_original':sha(ROOT/name)==expected}
    write_json(out/'parser_code_comparison.json', code)
    # Source manifest identifies acquisition bytes; does not assert semantic
    # adequacy, licensing, temporal validity, or independent adjudication.
    manifest = {'schema_version':'klima-corpus-freeze-1','corpus':'E2-v1.1-acquisition-20261024','purpose':'Sprint 1 diagnostic corpus freeze; no quality benchmark acceptance','source_root':str(ACQ.relative_to(ROOT)), 'cutoff_date':original['cutoff_date'],'files':[{'path':p,'sha256':h,'bytes':(ROOT/p).stat().st_size,'classification':'local-original-not-for-redistribution' if '/raw/' in p else 'historical-research-input'} for p,h in before.items()],'documents':provenance,'restore':'Restore exact local original files at the listed repository-relative paths from the project owner backup; verify every SHA-256. Live refetch cannot recreate this snapshot. Raw assets are not bundled/published.','redistribution_status':'NOT_CLEARED; no raw bodies or contact excerpts copied into this output'}
    write_json(out/'corpus_manifest.json',manifest)
    with (out/'corpus_manifest.csv').open('w',newline='') as f:
        fields=list(provenance[0]);w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(provenance)
    quality = {'status':'TECHNICAL_INTEGRITY_PASS_CONTENT_QUALIFICATION_CONDITIONAL','original_checksum_entries_verified':len(checked),'raw_body_files':len(raw),'documents':len(catalog),'text_units':len(units),'request_log_records':len(requests),'http_response_records':sum('status' in x for x in requests),'complete_response_records':sum(bool(x.get('complete')) for x in requests),'response_kind_counts':dict(collections.Counter(x['kind'] for x in requests if 'status' in x)),'documents_by_acquisition_ags':dict(collections.Counter(c['ags'] for c in catalog)),'units_by_acquisition_ags':dict(collections.Counter(u['acquisition_ags'] for u in units)),'source_types':dict(collections.Counter(c['source_type'] for c in catalog)),'unique_catalog_body_hashes':len({c['body_sha256'] for c in catalog}),'unique_catalog_urls':len({c['url'] for c in catalog}),'exact_duplicate_groups':len(exact),'exact_duplicate_excess':sum(len(g)-1 for g in exact),'normalized_duplicate_groups':len(normalized),'normalized_duplicate_excess':sum(len(g)-1 for g in normalized),'near_duplicate_pairs':len(near),'date_confirmed_documents':sum(c['document_date'] is not None for c in catalog),'cutoff_eligible_documents':sum(c['cutoff_eligibility']=='ESTABLISHED' for c in catalog),'claim_jurisdiction_validated_documents':0,'publisher_literal_absent_in_parsed_imprint':sum(not p['publisher_literal_in_parsed_imprint'] for p in provenance),'all_publisher_literals_and_contexts_verified_in_raw':True,'unit_origin_types':dict(collections.Counter(o['segment_type'] for u in units for o in u['origins'])),'failures_reported_by_original_manifest':original['failures'],'independent_gold_annotations':0,'raw_payload_redistribution':'NOT_CLEARED','inclusion_policy':'All 57 source versions and 894 units retained; no posthoc filtering or deduplication','scientific_gate':'M3 quality benchmark blocked until independent annotation, jurisdiction/date review, and source coverage adjudication'}
    after={str(p.relative_to(ROOT)):sha(p) for p in inputs}
    if before != after:
        raise ValueError('Historical input bytes changed')
    quality['protected_input_files']=len(before)
    quality['protected_inputs_unchanged']=True
    write_json(out/'corpus_quality.json',quality)
    write_json(out/'run_manifest.json',{'command':'PYTHONDONTWRITEBYTECODE=1 python3 research/corpus/sprint-01/audit_corpus.py --out '+str(out), 'python':platform.python_version(),'driver_sha256':sha(Path(__file__)),'reused_components':{'provenance_contract.py':sha(ACQ/'protocol/provenance_contract.py'),'validate.py':sha(ACQ/'validate.py'),'run_diagnostics.py':sha(DIAG/'run_diagnostics.py')},'network':'blocked by audit hook','legacy_inputs':'read only; verified before/after','outputs':{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}})
    print(json.dumps(quality,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    main(ap.parse_args().out.resolve())
