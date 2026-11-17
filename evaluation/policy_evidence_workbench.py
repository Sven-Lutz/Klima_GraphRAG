"""Evidence-first, review-gated policy QA for the existing Klima_GraphRAG snapshot.

Run from the repository root:
  python -m evaluation.policy_evidence_workbench collect --case MPB-002
  python -m evaluation.policy_evidence_workbench collect --all
  python -m evaluation.policy_evidence_workbench adjudicate

New, standalone module: no edits to crawler, benchmarks, SQLite or older runs.
The machine verifies source provenance; human review verifies municipal decision
semantics and policy-specific applicability. Nothing uses the benchmark gold label
as evidence or as a feature in answer prediction.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from contextlib import closing
import hashlib
from html import escape as html_escape
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = ROOT / 'evaluation/multi_policy_benchmark_v1_20261015.csv'
DEFAULT_SNAPSHOT = ROOT / 'crawler/data/db/RECOVERY_RAG_PDF_V2.sqlite'
DEFAULT_MANIFEST = ROOT / 'crawler/data/db/RECOVERY_RAG_PDF_V2.manifest.json'
DEFAULT_INDEX = ROOT / 'crawler/data/db/RECOVERY_LEXICAL_PDF_V2.sqlite'
DEFAULT_OUT = ROOT / 'evaluation/policy_evidence_review_v1'
VERSION = 'klima-reviewed-policy-evidence-1'

# Broad, repeatable source-discovery searches, NOT verdict rules. The baseline
# question is also searched. Exact terms are chosen to capture grant rules,
# official council actions, closures and extensions—not simply PV mentions.
FAMILY_SEARCHES = {
    'balkonkraftwerk_subsidy': (
        'Balkonkraftwerk Stecker-Solar Mini-Solaranlagen Zuschuss Förderrichtlinie',
        'Mini-Solaranlagen Beschluss Förderung eingestellt Verlängerung',
        'Stecker-Solar kommunales Förderprogramm Antragsfrist',
    ),
    'rooftop_pv_subsidy': (
        'Photovoltaikanlagen Dächer Fassaden kommunale Förderrichtlinie Zuschuss',
        'Solarförderprogramm Photovoltaikanlagen Beschluss Laufzeit',
        'Dach-PV Fassaden Photovoltaik Förderung eingestellt verlängert',
    ),
    'heat_pump_heating_subsidy': (
        'Wärmepumpen Zuschuss kommunale Förderung Förderrichtlinie',
        'Heizungsförderung Wärmepumpe Beschluss eingestellt Verlängerung',
        'Wärmepumpe kommunales Förderprogramm Antrag',
    ),
    'building_energy_renovation': (
        'energetische Gebäudesanierung Gebäudehülle Dämmung Fenstertausch Zuschuss',
        'Sanierung Wohngebäude kommunale Förderrichtlinie Beschluss',
        'Gebäudehülle Sanierungsförderung verlängert eingestellt',
    ),
    'ev_charging_emobility': (
        'Wallbox Ladeinfrastruktur private Ladestation kommunaler Zuschuss',
        'Elektromobilität Ladeinfrastruktur Förderprogramm Beschluss',
        'Wallbox Förderung eingestellt verlängert Antragsfrist',
    ),
}

OFFICIAL_DOMAINS = {
    '09161000': ('ingolstadt.de',),
    '09162000': ('muenchen.de', 'muenchen.ris-portal.de'),
    '09362000': ('regensburg.de',),
    '09462000': ('bayreuth.de',),
    '09562000': ('erlangen.de',),
    '09563000': ('fuerth.de',),
    '09564000': ('nuernberg.de',),
    '09663000': ('wuerzburg.de',),
    '09761000': ('augsburg.de',),
}
TOPIC_CUES = {
    'balkonkraftwerk_subsidy': ('balkon', 'stecker', 'mini-solar', 'minisolar'),
    'rooftop_pv_subsidy': ('photovoltaik', 'dach', 'fassad', 'solarförderprogramm'),
    'heat_pump_heating_subsidy': ('wärmepumpe', 'waermepumpe', 'heizungsförder'),
    'building_energy_renovation': ('sanier', 'dämm', 'daemm', 'gebäudehüll', 'fenstertausch'),
    'ev_charging_emobility': ('wallbox', 'ladeinfrastruktur', 'ladestation', 'elektromobil'),
}
DECISION_CUES = (
    'förderrichtlinie', 'beschluss', 'beschlossen', 'gefördert', 'bewilligt',
    'abgelehnt', 'ausgelaufen', 'gültig bis', 'nicht mehr', 'befristet',
    'haushaltskonsolidierung', 'verlänger', 'eingestellt', 'außer kraft',
    'in kraft', 'förderprogramm', 'antragsfrist',
)
# Proposed/agenda language must always be checked by a reviewer, never
# interpreted as an enacted grant decision automatically.
PROPOSAL_CUES = ('antrag der', 'vorschlag', 'tagesordnungspunkt', 'vorberatend', 'beantragt')
EVENTS = {
    'mention', 'agenda', 'proposal', 'rejected_proposal', 'discussion',
    'approved_active', 'extended', 'terminated', 'explicitly_not_offered',
}


class AuditError(RuntimeError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _iso(raw: Any, name: str, required: bool = False) -> date | None:
    if raw in ('', None):
        if required:
            raise AuditError(f'{name} is required')
        return None
    if not isinstance(raw, str):
        raise AuditError(f'{name} must be YYYY-MM-DD')
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise AuditError(f'{name} must be YYYY-MM-DD: {raw!r}') from exc


def _exact_quote(quote: str, text: str) -> bool:
    return ' '.join(quote.split()).casefold() in ' '.join(text.split()).casefold()


def _official_url(url: str, municipality_id: str) -> bool:
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    if p.scheme not in {'https', 'http'} or not p.hostname or p.username or p.password:
        return False
    host = p.hostname.casefold()
    return any(host == domain or host.endswith('.' + domain) for domain in OFFICIAL_DOMAINS.get(municipality_id, ()))


def _looks_damaged(text: str) -> bool:
    words = re.findall(r'[^\W_]+', text, flags=re.UNICODE)
    if len(words) < 9 or len(text.strip()) < 65:
        return True
    if len(text) < 250 and re.search(r'(?<=[a-zäöüß])\.(?=[a-zäöüß])', text):
        return True
    return False


def _signals(text: str, family: str, url: str, municipality_id: str) -> dict[str, Any]:
    normalized = text.casefold()
    topic = [cue for cue in TOPIC_CUES.get(family, ()) if cue in normalized]
    decision = [cue for cue in DECISION_CUES if cue in normalized]
    proposal = [cue for cue in PROPOSAL_CUES if cue in normalized]
    official = _official_url(url, municipality_id)
    damaged = _looks_damaged(text)
    # A sorting aid, never evidence of an enacted or currently active policy.
    score = (6 if official else 0) + 3 * min(3, len(topic)) + 2 * min(3, len(decision))
    score += 2 if re.search(r'\b(?:202[3-9]|203\d)\b', normalized) else 0
    score -= 7 if damaged else 0
    score -= 2 if proposal else 0
    if family == 'rooftop_pv_subsidy' and any(x in normalized for x in ('balkonkraftwerk', 'mini-solaranlage', 'stecker-solar')):
        if not any(x in normalized for x in ('dach', 'fassad')):
            score -= 6  # Avoid confusing balcony-PV subsidies with rooftop-PV grants.
    return {'municipal_domain_hint': official, 'topic_cues': topic, 'decision_cues': decision,
            'proposal_or_agenda_cues': proposal, 'damaged_excerpt': damaged, 'ranking_hint': score}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    required = {'benchmark_id', 'municipality_id', 'municipality', 'question', 'policy_id', 'human_label'}
    if not rows or not required <= set(rows[0]):
        raise AuditError(f'Missing benchmark columns: {sorted(required - set(rows[0] if rows else []))}')
    ids = [r['benchmark_id'] for r in rows]
    if len(set(ids)) != len(ids):
        raise AuditError('Duplicate benchmark IDs')
    return rows


def search_plans(row: dict[str, str], policy_family: str) -> list[str]:
    city = row['municipality']
    questions = [row['question']]
    for q in FAMILY_SEARCHES.get(policy_family, ()):
        questions.append(f'{city} {q}')
    return questions


def _review_template() -> dict[str, Any]:
    return {
        'status': 'unreviewed', 'reviewer': '', 'reviewed_on': '',
        'authority': 'unknown', 'issuer_ags': '',
        'policy_matches': False, 'official_status_confirmed': False, 'event': 'mention',
        'published_at': '', 'effective_from': '', 'effective_until': '',
        'current_status_checked_through': '', 'quote': '', 'notes': '',
    }


def _read_source(con: sqlite3.Connection, chunk_id: str, municipality_id: str) -> dict[str, str]:
    # Schema corresponds to canonical chunk retrieval; read-only and parameterized.
    row = con.execute('''SELECT c.document_id,c.text,c.chunk_hash,d.url_canonical
        FROM chunks AS c JOIN documents_raw AS d ON d.document_id=c.document_id
        WHERE c.chunk_id=?''', (chunk_id,)).fetchone()
    if not row:
        raise AuditError(f'Chunk missing from snapshot: {chunk_id}')
    doc_id, text, stored_hash, url = row
    linked = con.execute('''SELECT 1 FROM municipality_documents
        WHERE document_id=? AND municipality_id=? LIMIT 1''', (doc_id, municipality_id)).fetchone()
    if linked is None:
        raise AuditError(f'Chunk not linked to municipality: {chunk_id} / {municipality_id}')
    if not isinstance(text, str) or not text.strip() or not isinstance(url, str):
        raise AuditError(f'Invalid text/URL for chunk {chunk_id}')
    h = _sha(text)
    if not stored_hash or stored_hash != h:
        raise AuditError(f'Chunk content hash mismatch/missing for {chunk_id}')
    return {'chunk_id': chunk_id, 'document_id': str(doc_id), 'text': text,
            'text_sha256': h, 'source_url': url}


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise AuditError(f'Snapshot missing: {path}')
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    return con


def _check_snapshot(snapshot: Path, manifest: Path, lexical: Path | None = None) -> str:
    # Imported lazily; unit tests for pure decision logic need no project install.
    from crawler.core.snapshots import validate_snapshot_manifest
    binding = validate_snapshot_manifest(manifest)
    if snapshot.resolve() != binding.database.path:
        raise AuditError('Snapshot and manifest refer to different paths')
    if lexical is not None:
        if not lexical.is_file():
            raise AuditError(f'Missing lexical index: {lexical}')
        with sqlite3.connect(lexical.resolve().as_uri() + '?mode=ro', uri=True) as con:
            metadata = dict(con.execute('SELECT key,value FROM metadata'))
        if str(metadata.get('source_snapshot_id')) != str(binding.manifest.get('manifest_id')):
            raise AuditError('Lexical index manifest binding mismatch')
    return str(binding.database.snapshot_id)


def collect_case(row: dict[str, str], *, retriever: Any, con: sqlite3.Connection,
                 manifest: Path, snapshot_id: str, as_of: str, top_k: int = 12,
                 max_candidates: int = 16) -> dict[str, Any]:
    from crawler.core.question_intent import extract_question_intent
    from crawler.core.retrieval import RetrievalMode, RetrievalRequest
    intent = extract_question_intent(row['question'])
    if intent.municipality_id != row['municipality_id'] or not intent.policy_family:
        raise AuditError(f'Unresolved policy scope for {row["benchmark_id"]}: {intent}')
    grouped: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    plans = search_plans(row, intent.policy_family)
    for n, query in enumerate(plans):
        try:
            result = retriever.retrieve(RetrievalRequest(
                query=query, manifest_path=str(manifest), mode=RetrievalMode.LEXICAL,
                top_k=top_k, municipality_ids=(row['municipality_id'],)))
        except Exception as exc:
            # Do not silently treat a failed search as evidence of absence.
            warnings.append(f'Query {n + 1} failed: {type(exc).__name__}: {str(exc)[:250]}')
            continue
        if str(result.database_snapshot_id) != snapshot_id:
            raise AuditError('Cross-snapshot retrieval result blocked')
        for item in result.items:
            if item.source_type != 'chunk' or not item.chunk_id:
                continue
            source = _read_source(con, item.chunk_id, row['municipality_id'])
            if (source['source_url'] != item.document.source_url or
                source['document_id'] != item.document.document_id or
                source['text'] != item.evidence_text or
                item.evidence_id == ''):
                raise AuditError(f'Canonical retrieval differs from snapshot: {item.chunk_id}')
            source_id = source['chunk_id']
            sig = _signals(source['text'], intent.policy_family,
                           source['source_url'], row['municipality_id'])
            if source_id not in grouped:
                grouped[source_id] = dict(source, evidence_id=item.evidence_id,
                    source_snapshot_id=snapshot_id, retrieval_rank=int(item.rank),
                    matched_queries=[n + 1], signals=sig, review=_review_template())
            else:
                entry = grouped[source_id]
                entry['retrieval_rank'] = min(entry['retrieval_rank'], int(item.rank))
                if n + 1 not in entry['matched_queries']:
                    entry['matched_queries'].append(n + 1)
    candidates = list(grouped.values())
    candidates.sort(key=lambda r: (
        -r['signals']['ranking_hint'], -len(r['matched_queries']),
        r['retrieval_rank'], r['chunk_id']))
    selected: list[dict[str, Any]] = []
    per_doc: dict[str, int] = {}
    for candidate in candidates:
        doc_id = candidate['document_id']
        if per_doc.get(doc_id, 0) >= 3:
            continue
        selected.append(candidate)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(selected) >= max_candidates:
            break
    return {
        'format': VERSION, 'snapshot_id': snapshot_id,
        'as_of': as_of, 'benchmark_id': row['benchmark_id'],
        'municipality_id': row['municipality_id'], 'municipality': row['municipality'],
        'policy_family': intent.policy_family, 'benchmark_policy_id': row['policy_id'],
        'question': row['question'],
        # Gold label intentionally absent: prevents leakage into review/decision.
        'retrieval_plan_count': len(plans), 'retrieved_unique_chunks': len(grouped),
        'retrieval_warnings': warnings, 'candidates': selected,
        'notice': 'Candidates are unverified. Inspect primary municipal source and decision context; do not mark proposals or council agendas as enacted policy.',
    }


@dataclass(frozen=True)
class VerifiedEvent:
    evidence_id: str
    url: str
    quote: str
    event: str
    published_at: date
    effective_from: date | None
    effective_until: date | None
    current_checked: date | None


def _verify_review(packet: dict[str, Any], candidate: dict[str, Any],
                   con: sqlite3.Connection, as_of: date) -> VerifiedEvent | None:
    review = candidate['review']
    if review['status'] == 'unreviewed' or review['status'] == 'rejected':
        return None
    if review['status'] != 'verified':
        raise AuditError('Invalid review status')
    if not review['reviewer'].strip() or not _iso(review['reviewed_on'], 'reviewed_on', True):
        raise AuditError('Verified review requires reviewer and review date')
    if review['authority'] != 'municipality' or review['issuer_ags'] != packet['municipality_id']:
        return None  # utility, federal, neighboring city, etc. cannot prove municipal action
    if review['policy_matches'] is not True:
        return None
    if review.get('official_status_confirmed') is not True:
        return None  # Independent source/decision-context check required
    if review['event'] not in EVENTS:
        raise AuditError(f'Invalid event: {review["event"]!r}')
    pub = _iso(review['published_at'], 'published_at', True)
    start = _iso(review['effective_from'], 'effective_from')
    until = _iso(review['effective_until'], 'effective_until')
    checked = _iso(review['current_status_checked_through'], 'current_status_checked_through')
    if start and until and start > until:
        raise AuditError('effective_from after effective_until')
    if pub > as_of:
        raise AuditError('Future-dated evidence cannot decide a historical/current question')
    if checked and checked > _iso(review['reviewed_on'], 'reviewed_on', True):
        raise AuditError('Current status check cannot be after its review date')
    source = _read_source(con, candidate['chunk_id'], packet['municipality_id'])
    for field in ('chunk_id', 'document_id', 'text_sha256', 'source_url', 'text'):
        if source[field] != candidate[field]:
            raise AuditError(f'Snapshot provenance changed: {candidate["chunk_id"]} / {field}')
    if not isinstance(review['quote'], str) or len(review['quote'].strip()) < 25:
        raise AuditError('Verified review requires a source quote >=25 characters')
    if not _exact_quote(review['quote'], source['text']):
        raise AuditError('Reviewed quote is absent from immutable chunk text')
    return VerifiedEvent(candidate['evidence_id'], source['source_url'], review['quote'],
                         review['event'], pub, start, until, checked)


def decide_verified(events: list[VerifiedEvent], as_of: date) -> dict[str, Any]:
    """Conservative as-of resolver; never infers No from search absence/expiry alone.

    A start/extension is YES only while explicitly valid or independently current-
    checked. A termination/no-offer statement requires recent evidence or a
    documented current-status recheck. Proposals and agenda text prove neither.
    Later definitive municipal actions supersede earlier ones when dates differ.
    """
    eligible: list[tuple[date, str, VerifiedEvent]] = []
    excluded: dict[str, str] = {}
    for e in events:
        if e.event not in {'approved_active', 'extended', 'terminated', 'explicitly_not_offered'}:
            excluded[e.evidence_id] = 'Not an enacted/current policy action'
            continue
        start = e.effective_from or e.published_at
        if start > as_of:
            excluded[e.evidence_id] = 'Action not effective on as-of date'
            continue
        if e.event in {'approved_active', 'extended'}:
            if e.effective_until and e.effective_until < as_of:
                excluded[e.evidence_id] = 'Historical program expired; does not prove current No'
                continue
            if not e.effective_until and (e.current_checked is None or e.current_checked < as_of):
                excluded[e.evidence_id] = 'Active program without validity end needs current status verification'
                continue
            eligible.append((start, 'Yes', e))
        else:
            if e.effective_until and e.effective_until < as_of:
                excluded[e.evidence_id] = 'Negative statement limited to an expired period'
                continue
            if (as_of - e.published_at > timedelta(days=365)
                    and (e.current_checked is None or e.current_checked < as_of)):
                excluded[e.evidence_id] = 'Old negative statement; current policy may differ'
                continue
            eligible.append((start, 'No', e))
    if not eligible:
        return {'label': 'Unknown', 'reason': 'No verified current enacted municipal decision.',
                'citations': [], 'excluded': excluded}
    newest = max(entry[0] for entry in eligible)
    latest = [(label, e) for stamp, label, e in eligible if stamp == newest]
    labels = {label for label, _ in latest}
    if len(labels) > 1:
        return {'label': 'Conflict', 'reason': 'Conflicting equally recent verified municipal actions.',
                'citations': [{'evidence_id': e.evidence_id, 'url': e.url, 'quote': e.quote} for _, e in latest],
                'excluded': excluded}
    label = next(iter(labels))
    latest_events = [e for _, e in latest]
    return {'label': label,
            'reason': 'Most recent verified, currently applicable municipal policy action.' if label == 'Yes'
            else 'Most recent verified explicit municipal termination/non-offering statement.',
            'citations': [{'evidence_id': e.evidence_id, 'url': e.url, 'quote': e.quote} for e in latest_events],
            'excluded': excluded}


def adjudicate_case(packet: dict[str, Any], con: sqlite3.Connection,
                    expected_snapshot_id: str) -> dict[str, Any]:
    if packet.get('format') != VERSION or packet.get('snapshot_id') != expected_snapshot_id:
        raise AuditError('Review packet version/snapshot mismatch')
    if not re.fullmatch(r'[0-9]{8}', str(packet.get('municipality_id', ''))):
        raise AuditError('Invalid municipality AGS in review packet')
    as_of = _iso(packet['as_of'], 'as_of', True)
    ids: set[str] = set()
    verified_events: list[VerifiedEvent] = []
    review_counts = {'verified': 0, 'rejected': 0, 'unreviewed': 0}
    for candidate in packet['candidates']:
        evid = candidate['evidence_id']
        if candidate.get('source_snapshot_id') != expected_snapshot_id:
            raise AuditError('Candidate belongs to another snapshot')
        if evid in ids:
            raise AuditError('Duplicate evidence ID in review packet')
        ids.add(evid)
        # Recheck all source bindings, including unreviewed records; reject edits.
        source = _read_source(con, candidate['chunk_id'], packet['municipality_id'])
        for field in ('chunk_id', 'document_id', 'text_sha256', 'source_url', 'text'):
            if source[field] != candidate.get(field):
                raise AuditError(f'Edited/unbound source content: {evid} / {field}')
        status = candidate['review']['status']
        if status not in review_counts:
            raise AuditError('Unknown review status')
        review_counts[status] += 1
        item = _verify_review(packet, candidate, con, as_of)
        if item:
            verified_events.append(item)
    decision = decide_verified(verified_events, as_of)
    decision.update({'benchmark_id': packet['benchmark_id'], 'question': packet['question'],
                     'municipality_id': packet['municipality_id'], 'policy_family': packet['policy_family'],
                     'as_of': packet['as_of'], 'retrieved_count': packet['retrieved_unique_chunks'],
                     'selected_candidates': len(packet['candidates']),
                     'verified_usable_claims': len(verified_events), 'review_counts': review_counts,
                     'retrieval_warnings': packet['retrieval_warnings']})
    return decision


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, path)


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        return
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    os.replace(tmp, path)



def write_review_overview(output: Path) -> None:
    """Static, read-only HTML inspection page; never marks evidence as verified."""
    blocks = []
    for path in sorted((output / 'cases').glob('*.json')):
        packet = json.loads(path.read_text(encoding='utf-8'))
        candidates = packet.get('candidates', [])
        blocks.append(f'<section><h2>{html_escape(packet["benchmark_id"])} — '
                      f'{html_escape(packet["municipality"])}</h2>'
                      f'<p><strong>{html_escape(packet["question"])}</strong></p>'
                      f'<p>{html_escape(packet["policy_family"])} · '
                      f'{len(candidates)} candidates · '
                      f'{len(packet.get("retrieval_warnings", []))} retrieval warnings</p>')
        for c in candidates:
            sig = c['signals']
            cues = ', '.join(sig['decision_cues'] + sig['topic_cues'])
            url = html_escape(c['source_url'], quote=True)
            description = html_escape(' '.join(c['text'].split())[:900])
            review_status = html_escape(str(c['review']['status']))
            flags = ('official-domain hint' if sig['municipal_domain_hint'] else 'unverified host')
            if sig['damaged_excerpt']:
                flags += ' · possibly damaged text'
            if sig['proposal_or_agenda_cues']:
                flags += ' · possible proposal/agenda'
            blocks.append('<article>'
                          f'<p><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></p>'
                          f'<small>{html_escape(flags)} · review: {review_status} · '
                          f'cues: {html_escape(cues)} · chunk: {html_escape(c["chunk_id"])}</small>'
                          f'<pre>{description}</pre></article>')
        blocks.append('</section>')
    css = 'body{font:15px/1.5 system-ui,Arial,sans-serif;margin:28px auto;max-width:1020px;background:#f8fafc;color:#162233;padding:0 20px}h1,h2{color:#17345b}section{border:1px solid #d9e2ed;background:white;padding:20px;margin:18px 0;border-radius:10px}article{border-top:1px solid #e2e8f0;padding:12px 0}pre{white-space:pre-wrap;word-break:break-word;background:#f2f5fa;padding:12px;border-radius:6px;font:13px/1.5 system-ui}small{color:#475569}a{color:#1d4b8f}header{padding:12px 0}'
    doc = ('<!doctype html><html lang="en"><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>Klima GraphRAG evidence review</title><style>' + css + '</style>'
           '<header><h1>Evidence review — source candidates</h1>'
           '<p><strong>Read-only:</strong> a high-scoring excerpt is NOT proof of an enacted or current policy. '
           'Inspect linked official source and complete decision context before editing the JSON review fields.</p></header>'
           + ''.join(blocks) + '</html>')
    (output / 'review_overview.html').write_text(doc, encoding='utf-8')


def collect(args: argparse.Namespace) -> None:
    from crawler.core.retrieval import CanonicalRetriever
    rows = _rows(args.benchmark)
    selected = rows if args.all else [row for row in rows if row['benchmark_id'] == args.case]
    if not selected:
        raise AuditError(f'Unknown benchmark case: {args.case}')
    snapshot_id = _check_snapshot(args.snapshot, args.manifest, args.lexical_db)
    retriever = CanonicalRetriever(lexical_database=args.lexical_db)
    args.output.mkdir(parents=True, exist_ok=True)
    with closing(_open_db(args.snapshot)) as con:
        for i, row in enumerate(selected, 1):
            target = args.output / 'cases' / (row['benchmark_id'] + '.json')
            if target.exists() and not args.overwrite:
                print(f'[{i}/{len(selected)}] {row["benchmark_id"]}: SKIP existing packet (review preserved)')
                continue
            packet = collect_case(row, retriever=retriever, con=con,
                manifest=args.manifest, snapshot_id=snapshot_id, as_of=args.as_of,
                top_k=args.top_k, max_candidates=args.max_candidates)
            _write_json(target, packet)
            print(f'[{i}/{len(selected)}] {row["benchmark_id"]}: '
                  f'{packet["retrieved_unique_chunks"]} unique / '
                  f'{len(packet["candidates"])} selected / '
                  f'{len(packet["retrieval_warnings"])} query errors', flush=True)
    write_review_overview(args.output)
    print('REVIEW PACKETS:', args.output / 'cases')
    print('READ-ONLY BROWSER VIEW:', args.output / 'review_overview.html')
    print('No automatic official-policy verdicts were inferred from retrieval.')


def adjudicate(args: argparse.Namespace) -> None:
    benchmark = _rows(args.benchmark)
    row_map = {row['benchmark_id']: row for row in benchmark}
    from crawler.core.question_intent import extract_question_intent
    snapshot_id = _check_snapshot(args.snapshot, args.manifest)
    targets = (sorted((args.output / 'cases').glob('*.json')) if args.all
               else [args.output / 'cases' / (args.case + '.json')])
    if not targets:
        raise AuditError('No review packets exist. Run collect first.')
    summary: list[dict[str, Any]] = []
    with closing(_open_db(args.snapshot)) as con:
        for target in targets:
            if not target.exists():
                raise AuditError(f'Review packet missing: {target}')
            packet = json.loads(target.read_text(encoding='utf-8'))
            case_id = packet.get('benchmark_id')
            if case_id not in row_map:
                raise AuditError(f'Unknown benchmark case: {case_id}')
            official_row = row_map[case_id]
            if (packet['question'] != official_row['question'] or
                packet['municipality_id'] != official_row['municipality_id'] or
                packet['policy_family'] != extract_question_intent(official_row['question']).policy_family):
                raise AuditError(f'Benchmark identity mismatch: {case_id}')
            result = adjudicate_case(packet, con, snapshot_id)
            _write_json(args.output / 'decisions' / (case_id + '.json'), result)
            label = result['label']
            summary.append({
                'benchmark_id': case_id, 'municipality': official_row['municipality'],
                'policy_family': result['policy_family'], 'as_of': result['as_of'],
                'human_label': official_row['human_label'], 'predicted_label': label,
                'label_match': label == official_row['human_label'],
                'retrieved_unique_chunks': result['retrieved_count'],
                'review_candidates': result['selected_candidates'],
                'verified_claims': result['verified_usable_claims'],
                'citation_count': len(result['citations']),
                'reason': result['reason'],
                'citation_urls': ' | '.join(c['url'] for c in result['citations']),
            })
            print(f'{case_id}: {label}; reviewed={result["review_counts"]["verified"]}; '
                  f'gold={official_row["human_label"]}', flush=True)
    _write_csv(args.output / 'policy_evidence_decisions_summary.csv', summary)
    matches = sum(r['label_match'] for r in summary)
    n = len(summary)
    coverage = sum(r['predicted_label'] in {'Yes', 'No'} for r in summary)
    print(f'LABEL MATCHES: {matches}/{n}; DEFINITIVE ANSWERS: {coverage}/{n}')
    print('An unreviewed packet correctly predicts Unknown. These are not autonomous model accuracy measurements.')
    print('SUMMARY:', args.output / 'policy_evidence_decisions_summary.csv')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('collect', 'adjudicate'):
        p = sub.add_parser(name)
        selected = p.add_mutually_exclusive_group(required=True)
        selected.add_argument('--case', help='Benchmark case, e.g., MPB-002')
        selected.add_argument('--all', action='store_true', help='All 45 benchmark cases')
        p.add_argument('--benchmark', type=Path, default=DEFAULT_BENCHMARK)
        p.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
        p.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
        p.add_argument('--output', type=Path, default=DEFAULT_OUT)
        if name == 'collect':
            p.add_argument('--lexical-db', type=Path, default=DEFAULT_INDEX)
            p.add_argument('--top-k', type=int, default=12)
            p.add_argument('--max-candidates', type=int, default=16)
            p.add_argument('--as-of', default='2026-11-04')
            p.add_argument('--overwrite', action='store_true', help='Replace packet (including its human reviews)')
    args = parser.parse_args(argv)
    if args.cmd == 'collect':
        _iso(args.as_of, 'as_of', True)
        if args.top_k < 1 or args.max_candidates < 1:
            parser.error('top-k and max-candidates must be positive')
        collect(args)
    else:
        adjudicate(args)


if __name__ == '__main__':
    main()
