"""Controlled targeted field IE: frozen rules, local schema model, hybrid checks.

All accuracy measures are conditional on provisional source-first agent labels.
Evidence-string validity is a structural check, not semantic entailment.
"""

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import time
import types
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from research.climate_policy import store

FIELDS = ('actor', 'role', 'status', 'reference_time', 'amount', 'unit', 'amount_kind')
RESERVE = 15 * 1024**3
PROMPT_VERSION = 'targeted-policy-field-ie-v1'
SYSTEM = '''Extrahiere den durch Fokus bezeichneten Datensatz ausschließlich aus Text und Überschrift.
Mehrere Akteure und Beträge nicht vermischen. actor ist der im Fokus verlangte Akteur, sonst null.
actor und reference_time möglichst wörtlich, Betrag als Dezimalstring ohne Tausendertrennzeichen.
Eine Kostenschätzung ist kein Zuschuss, eine Förderzusage kein Zahlungseingang.
Eine abgeschlossene Studie bedeutet keine umgesetzte Infrastruktur.
Keine Angaben aus dem Namen der Website oder aus Weltwissen ergänzen.
Jedes nicht-null Feld braucht in evidence einen kurzen EXAKTEN Textausschnitt als Beleg.
Wenn nicht ausdrücklich belegt: null. Nutze nur zulässige role/status/amount_kind Werte.
Antworte nur im JSON-Schema. /no_think'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def empty():
    return {**dict.fromkeys(FIELDS), 'evidence': {}}


def normalize(field, value):
    if value is None:
        return None
    value = unicodedata.normalize('NFKC', str(value)).strip()
    if field == 'amount':
        try:
            return str(Decimal(value).normalize())
        except InvalidOperation:
            return value
    return re.sub(r'\s+', ' ', value).casefold().replace('–', '-').replace('—', '-')


def rule_extraction(case, extractor):
    unit = {'unit_id': case['unit_id'], 'text': case['text'],
            'heading_context': case.get('heading'), 'page_ref': case.get('page_ref'),
            'acquisition_ags': case['source_frame_ags'][0],
            'raw_hashes': [case['source_sha256']], 'source_url': case['source_url']}
    claims = extractor(unit)
    output = empty()

    def assign(field, value, quote):
        output[field] = value
        output['evidence'][field] = quote

    actors = [c for c in claims if c['field'] in ('ACTOR', 'FUNDING_ACTOR')]
    if actors:
        c = actors[0]
        assign('actor', c['value'], c['quote'])
        if c['field'] == 'FUNDING_ACTOR':
            assign('role', 'FUNDER', c['quote'])
    money = [c for c in claims if c['field'] in (
        'PROJECT_COST', 'REPORTED_GRANT', 'FUNDING_CAP', 'FUNDING_RATE')]
    focus = case['focus'].casefold()
    wanted = ('FUNDING_RATE' if 'förderquote' in focus else
              'PROJECT_COST' if 'kosten' in focus and 'nicht gesamtkosten' not in focus else
              'FUNDING_CAP' if re.search('höchst|maximal', focus) else 'REPORTED_GRANT')
    money = sorted(money, key=lambda c: c['field'] != wanted)
    if money:
        c = money[0]
        assign('amount', c['value'], c['quote'])
        assign('unit', c['unit'], c['quote'])
        assign('amount_kind', {'PROJECT_COST': 'PLANNED_COST', 'REPORTED_GRANT': 'REPORTED_GRANT',
                              'FUNDING_CAP': 'GRANT_CAP', 'FUNDING_RATE': 'FUNDING_RATE'}[c['field']],
               c['quote'])
    dates = [c for c in claims if c['field'] == 'DATE']
    if dates:
        c = dates[-1] if 'ende' in focus else dates[0]
        assign('reference_time', c['value'], c['quote'])
    statuses = [c for c in claims if c['field'] == 'MEASURE_STATUS']
    if statuses:
        c = statuses[0]
        assign('status', 'PLANNED' if c['value'] == 'ANNOUNCED' else c['value'], c['quote'])
    return output


def schema(cases):
    properties = {}
    for field in FIELDS:
        properties[field] = {'type': ['string', 'null']}
        if field in ('role', 'status', 'unit', 'amount_kind'):
            values = {c['provisional_expected'].get(field) for c in cases}
            properties[field]['enum'] = [None, *sorted(x for x in values if x)]
    properties['evidence'] = {'type': 'object', 'properties': {
        field: {'type': 'string'} for field in FIELDS}, 'additionalProperties': False}
    return {'type': 'object', 'properties': properties,
            'required': [*FIELDS, 'evidence'], 'additionalProperties': False}


def valid_schema(value, definition):
    if not isinstance(value, dict) or set(value) != set(FIELDS) | {'evidence'}:
        return False
    if not isinstance(value['evidence'], dict):
        return False
    for field in FIELDS:
        if value[field] is not None and not isinstance(value[field], str):
            return False
        enum = definition['properties'][field].get('enum')
        if enum is not None and value[field] not in enum:
            return False
    return set(value['evidence']).issubset(FIELDS) and all(
        isinstance(v, str) for v in value['evidence'].values())


def numeric_support(value, quote):
    values = set()
    for hit in re.finditer(r'\d+(?:[.,]\d+)*', quote):
        s = hit.group()
        if ',' in s:
            s = s.replace('.', '').replace(',', '.')
        elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
            s = s.replace('.', '')
        try:
            number = Decimal(s)
        except InvalidOperation:
            # Dates such as 01.11.2024 are not decimal amounts. Keep the
            # model's wrong amount for scoring, but never crash its validator.
            continue
        tail = quote[hit.end():hit.end() + 20].casefold()
        if re.match(r'\s*(?:million|mio\.)', tail):
            number *= 1000000
        values.add(str(number.normalize()))
    return normalize('amount', value) in values


def structurally_supported(field, output, text):
    value = output.get(field)
    if value is None:
        return True
    quote = output.get('evidence', {}).get(field)
    if not quote or quote not in text:
        return False
    if field in ('actor', 'reference_time'):
        return normalize(field, value) in normalize(field, quote)
    if field == 'amount':
        return numeric_support(value, quote)
    if field == 'unit':
        return bool(re.search(r'%|prozent', quote, re.I)) if value == 'percent' else bool(
            re.search(r'€|euro', quote, re.I))
    # Category values are not literal source strings. Only their quote existence
    # is certified here; semantic class correctness is measured separately.
    return True


def hybrid(model, rules, case):
    output = empty()
    rejected = []
    context = (case.get('heading') or '') + '\n' + case['text']
    for field in FIELDS:
        model_ok = model[field] is not None and structurally_supported(field, model, context)
        rule_ok = rules[field] is not None and structurally_supported(field, rules, context)
        if model[field] is not None and not model_ok:
            rejected.append(field)
        if model_ok:
            if rule_ok and normalize(field, model[field]) != normalize(field, rules[field]):
                # Conflicting proposals abstain, rather than trust either model or regex.
                rejected.append(field + ':conflict_abstention')
                continue
            output[field] = model[field]
            output['evidence'][field] = model['evidence'][field]
        elif rule_ok:
            output[field] = rules[field]
            output['evidence'][field] = rules['evidence'][field]
    return output, rejected


def summary(rows):
    totals = Counter()
    by_field = defaultdict(Counter)
    for row in rows:
        for field in FIELDS:
            expected = normalize(field, row['reference'][field])
            actual = normalize(field, row['output'][field])
            counts = by_field[field]
            counts['slots'] += 1
            counts['exact'] += expected == actual
            if expected is None:
                counts['unknown_slots'] += 1
                counts['correct_unknown'] += actual is None
            if expected is not None:
                counts['positive_slots'] += 1
                counts['tp'] += actual == expected
                counts['fn'] += actual != expected
            if actual is not None:
                counts['predicted_positive'] += 1
                counts['fp'] += actual != expected
    for field, counts in by_field.items():
        totals.update(counts)

    def metrics(counts):
        p = counts['tp'] / (counts['tp'] + counts['fp']) if counts['tp'] + counts['fp'] else None
        r = counts['tp'] / (counts['tp'] + counts['fn']) if counts['tp'] + counts['fn'] else None
        return {**counts, 'precision': p, 'recall': r,
                'f1': 2 * p * r / (p + r) if p is not None and r is not None and p + r else 0.0}
    return {'micro': metrics(totals), 'fields': {f: metrics(c) for f, c in by_field.items()}}


def run(db, sample, output, endpoint, model, response_cache=None):
    sample, output = Path(sample), Path(output)
    if output.exists():
        raise ValueError('Use a fresh result directory; completed runs are immutable')
    frozen = json.loads((sample / 'FREEZE.json').read_text())
    if sha(sample / 'cases.jsonl') != frozen['cases_sha256'] or sha(db) != frozen['base_database_sha256']:
        raise ValueError('Frozen source or sample changed')
    if shutil.disk_usage(output.parent).free < RESERVE + 32 * 1024**2:
        raise RuntimeError('Storage reserve plus IE output allowance unavailable')
    cases = [json.loads(line) for line in (sample / 'cases.jsonl').read_text().splitlines()]
    definition = schema(cases)
    parameters = {'temperature': 0, 'seed': 42, 'num_ctx': 2048,
                  'num_predict': 384, 'num_thread': 1}
    output.mkdir(parents=True)
    method = {'prompt_version': PROMPT_VERSION, 'system_prompt': SYSTEM,
              'model': model, 'schema': definition, 'parameters': parameters,
              'max_calls': len(cases), 'max_wall_seconds': 1200, 'base_db_sha256': sha(db),
              'sample_sha256': frozen['cases_sha256'], 'implementation_sha256': sha(__file__),
              'rules_sha256': sha(store.__file__), 'reference_status': 'PROVISIONAL_AGENT_REVIEW',
              'budget': 'one local model response per case; hybrid reuses same response',
              'metrics': 'per-field TP/FP/FN, unknown specificity, structural quote validity; no human gold'}
    tags = requests.get(endpoint.rstrip('/') + '/api/tags', timeout=5)
    tags.raise_for_status()
    model_record = next((r for r in tags.json()['models'] if r['name'] == model), None)
    if model_record is None:
        raise ValueError('Explicit local model unavailable; do not download or fall back')
    method['served_model_artifact'] = model_record
    (output / 'METHOD_FREEZE.json').write_text(json.dumps(method, ensure_ascii=False, indent=2))
    (output / 'implementation.py').write_bytes(Path(__file__).read_bytes())
    cached_responses = {}
    if response_cache:
        for line in Path(response_cache).read_text().splitlines():
            record = json.loads(line)
            cached_responses[record['case_id']] = record
        (output / 'CACHE_LINEAGE.json').write_text(json.dumps({
            'path': str(Path(response_cache).resolve()), 'sha256': sha(response_cache),
            'policy': 'Reuse only exact request matches; labels, prompts and schemas unchanged',
            'amendment': 'Validator now ignores invalid dotted date tokens as decimal amounts',
        }, indent=2))
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        names = {ags: json.loads(record)['name'] for ags, record in conn.execute(
            'SELECT ags,record FROM municipalities')}
    extractor = types.FunctionType(store.extract.__code__,
                                   {**store.__dict__, 'MUNICIPALITIES': names}, 'extract')
    results, started = [], time.monotonic()

    def event(status, case_id=None, **extra):
        with (output / 'runs.jsonl').open('a') as stream:
            stream.write(json.dumps({'status': status, 'at': datetime.now(timezone.utc).isoformat(),
                'case_id': case_id, 'provider': 'local_ollama', 'model': model,
                'prompt_version': PROMPT_VERSION, 'parameters': parameters,
                'snapshot': frozen['base_database_sha256'], **extra}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    event('pending', input_ids=[c['unit_id'] for c in cases])
    for case in cases:
        if time.monotonic() - started > 1200 or shutil.disk_usage(output).free < RESERVE + 16 * 1024**2:
            event('failed', case['case_id'], error_class='RESOURCE_STOP')
            raise RuntimeError('Bounded IE resource stop; preserve partial run')
        context = (case.get('heading') or '') + '\n' + case['text']
        question = 'Fokus: ' + case['focus'] + '\nQuelltext:\n' + context
        payload = {'model': model, 'messages': [{'role': 'system', 'content': SYSTEM},
                   {'role': 'user', 'content': question}], 'format': definition,
                   'stream': False, 'think': False, 'keep_alive': '2m', 'options': parameters}
        rule_start = time.perf_counter()
        rules = rule_extraction(case, extractor)
        rule_seconds = time.perf_counter() - rule_start
        event('running', case['case_id'], input_ids=[case['unit_id']])
        call_start = time.perf_counter()
        response = None
        error = None
        prediction = empty()
        schema_ok = False
        from_cache = False
        try:
            cached = cached_responses.get(case['case_id'])
            if cached and cached['request'] == payload:
                response = cached['response']
                from_cache = True
            else:
                reply = requests.post(endpoint.rstrip('/') + '/api/chat', json=payload, timeout=(5, 90))
                reply.raise_for_status()
                response = reply.json()
            # Persist provider output before any local parsing or fusion.
            (output / (case['case_id'] + '.provider.json')).write_text(json.dumps({
                'request': payload, 'response': response, 'from_cache': from_cache},
                ensure_ascii=False))
            if response.get('model') != model or not response.get('done'):
                raise ValueError('Serving model/status mismatch')
            raw = json.loads(response['message']['content'])
            schema_ok = valid_schema(raw, definition)
            if not schema_ok:
                raise ValueError('Schema violation')
            prediction = raw
        except (requests.RequestException, ValueError, KeyError) as exc:
            error = type(exc).__name__
        model_seconds = time.perf_counter() - call_start
        combined, rejected = hybrid(prediction, rules, case)
        for arm, value in [('rules', rules), ('model', prediction), ('hybrid', combined)]:
            results.append({'case_id': case['case_id'], 'split': case['split'],
                'component': case['component'], 'municipality': case['source_frame_ags'][0],
                'method': arm, 'reference': case['provisional_expected'], 'output': value,
                'structurally_grounded': {f: structurally_supported(f, value, context) for f in FIELDS},
                'source_sha256': case['source_sha256'], 'unit_id': case['unit_id'],
                'status': 'PROVISIONAL_AGENT_REFERENCE_NOT_HUMAN_GOLD'})
        receipt = {'case_id': case['case_id'], 'request': payload, 'response': response,
                   'schema_valid': schema_ok, 'error_class': error, 'hybrid_rejections': rejected,
                   'rule_seconds': rule_seconds, 'model_seconds': model_seconds,
                   'served_from_cache': from_cache,
                   'actual_inference_seconds_this_run': 0 if from_cache else model_seconds}
        with (output / 'provider-responses.jsonl').open('a') as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        event('succeeded' if schema_ok else 'failed', case['case_id'], error_class=error,
              output_ids=[case['case_id'] + ':' + method for method in ('rules', 'model', 'hybrid')])
        print(json.dumps({'case': case['case_id'], 'schema_valid': schema_ok,
                          'seconds': round(model_seconds, 3)}), flush=True)
    (output / 'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results))
    report = {'cases': len(cases), 'components': len({c['component'] for c in cases}),
              'schema': 'targeted-field-ie-comparison-v1', 'status': 'EXPLORATORY_PROVISIONAL',
              'all_cases': {m: summary([r for r in results if r['method'] == m])
                            for m in ('rules', 'model', 'hybrid')},
              'evaluation_components': {m: summary([r for r in results if r['method'] == m
                                                   and r['split'] == 'evaluation'])
                                        for m in ('rules', 'model', 'hybrid')},
              'seconds': time.monotonic() - started, 'independent_human_judgments': 0,
              'semantic_entailment_automatically_certified': False,
              'reference_limit': 'source-exposed provisional annotations; no general architecture superiority'}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    event('succeeded', output_ids=[str(output / 'report.json')])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--sample', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--response-cache')
    args = parser.parse_args()
    run(args.db, args.sample, args.output, args.endpoint, args.model, args.response_cache)
