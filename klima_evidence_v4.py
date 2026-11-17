"""Klima GraphRAG V4: focused, read-only, document-aware evidence retrieval.

Builds on klima_evidence_v3.py and the existing full PDF-V2 corpus/FTS index.
No new embeddings, corpus rewriting, SSL bypass, or policy-status assertions.

Changes vs V3:
 - More focused ranking: topic-specific guidelines outrank unrelated policies.
 - Re-rank still limited to a small, diverse shortlist (Ollama BGE-M3 on demand).
 - Verified TLS download attempts (system, certifi, optional Windows truststore).
 - Existing browser-downloaded PDFs in --pdf-dir override network downloads.
 - Dates reported ONLY for matching evidence documents, with a separate
   strict effective/expiry candidate list. Never equate effective with active.
 - Saves JSON with document/source provenance and quality flags.

Examples (from project root):
  python klima_evidence_v4.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --lexical-only --repair-pdfs --pdf-dir "%USERPROFILE%\\Downloads"
  python klima_evidence_v4.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --repair-pdfs --pdf-dir "%USERPROFILE%\\Downloads" --rerank 12 --top-k 6 --save-json evaluation\\regensburg_pv_evidence_v4.json

If HTTPS certificate verification fails, do NOT disable TLS verification.
Download the relevant PDF using your normal browser into --pdf-dir, or install
truststore (`python -m pip install truststore`) to try Windows' certificate store.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import ssl
import sys
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import numpy as np
import klima_evidence_v3 as v3
import klima_fast_retrieval_v2 as v2

# Topic groups are generic across municipalities; no benchmark IDs or city names.
# Matched ONLY against source-title/URL material to avoid, for example,
# e-mobility documents winning merely because they mention solar charging.
CATEGORY_PATTERNS = {
    'photovoltaics': (r'photovolta', r'(?<![a-z])pv(?![a-z])', r'solarstrom', r'solaranlag', r'solarmodul'),
    'balcony_pv': (r'balkonkraft', r'steckersolar', r'balcony[ -]?solar'),
    'heat_pump': (r'w[aä]rmepump', r'waermepump', r'heizungstausch'),
    'district_heat': (r'fernw[aä]rm', r'nahw[aä]rm', r'w[aä]rmenetz', r'waermenetz'),
    'energy_renovation': (r'geb[aä]udeh[uü]ll', r'gebaeudehuell', r'w[aä]rmed[aä]mm', r'd[aä]mm', r'sanierung', r'\bsanier\w*', r'energieeffizienz'),
    'electric_mobility': (r'elektromobil', r'elektrofahr', r'wallbox', r'ladeinfrastruktur', r'ladestation'),
    'wind': (r'windkraft', r'windenergie', r'windanlage'),
}

DATE_RULES = (
    ('effective', re.compile(r'(?i)\b(?:tritt|treten)\s+(?:am\s+)?(?:\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*[a-zäöüß]+\s+\d{4})\s+in\s+kraft\b')),
    ('effective', re.compile(r'(?i)\b(?:gilt|gültig|gueltig|in\s+kraft)\s+ab\s*:?[ \t]*(?:\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*[a-zäöüß]+\s+\d{4})\b')),
    ('expires', re.compile(r'(?i)\b(?:außer\s+kraft\s+am|ausser\s+kraft\s+am|gültig\s+bis|gueltig\s+bis|befristet\s+bis|läuft\s+aus\s+am|laeuft\s+aus\s+am)\s*:?[ \t]*(?:\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*[a-zäöüß]+\s+\d{4})\b')),
)


def categories(text: str) -> set[str]:
    txt = unquote(text or '').casefold()
    found = {category for category, patterns in CATEGORY_PATTERNS.items()
             if any(re.search(p, txt, re.I) for p in patterns)}
    # Balcony PV is a specific category; a query about balcony PV should not be
    # interpreted as a request for every rooftop-PV scheme.
    return found


def query_categories(query: str) -> set[str]:
    categories_found = categories(query)
    # Preserve V2's English/variant trigger mapping.
    t = ' ' + query.casefold() + ' '
    for group, fragments in v2.TRIGGERS.items():
        if group in CATEGORY_PATTERNS and any(token in t for token in fragments):
            categories_found.add(group)
    if 'balcony_pv' in categories_found:
        categories_found.discard('photovoltaics')
    return categories_found


def title_categories(url: str, heading: str) -> set[str]:
    parts = urlsplit(url)
    # Use basename / specific URL path plus document heading, not passage body.
    return categories(unquote(parts.path) + ' ' + heading)


def _official_municipal_host(item: dict) -> bool:
    """Prefer the same municipality's official host, never just its AGS tag.

    This is a *retrieval preference*, not proof of authorship, programme status,
    validity, or eligibility. The resolver still independently verifies these.
    """
    from crawler.core.question_intent import _CANONICAL_NAMES
    name = _CANONICAL_NAMES.get(str(item.get('municipality_id') or ''))
    if not name:
        return False
    slug = name.casefold().translate(str.maketrans({
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'
    })).replace(' ', '-')
    host = (urlsplit(str(item.get('url') or '')).hostname or '').casefold()
    return bool(slug and (host == slug + '.de' or host.endswith('.' + slug + '.de')))


def _concrete_municipal_service_passage(text: str, want: set[str]) -> bool:
    """Retrieval *candidate* signal for an actual offered support service.

    Require topic match and an offer/action, not merely an overview mentioning
    'Beratungsangebote'. This does not validate provider, date, eligibility,
    availability, or whether a municipal subsidy exists.
    """
    if not want:
        return False
    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ])', str(text or '')):
        if not categories(sentence).intersection(want):
            continue
        # Historical announcements, plans and cancelled services are not
        # positive current-service retrieval cues.
        if re.search(
            r'\b(?:geplant|beabsichtigt|vorgesehen|eingestellt|'
            r'abgeschafft|früher|ehemalig\w*|soll\s+erst|'
            r'will\s+be\s+launched)\b', sentence, re.I
        ):
            continue
        advice = re.search(
            r'\b(?:\w{0,24}beratung\w*|berat\w*|einstiegsberatung\w*|'
            r'beratungsgespr\w*|consultation\w*|advice|advisory|'
            r'assistance|support)\b', sentence, re.I
        )
        action = re.search(
            r'\b(?:berät|beraten|bietet|bieten|unterstützt|unterstützen|'
            r'hilft|helfen|erhalten|erhält|bekommen|'
            r'in\s+anspruch\s+nehmen|vereinbaren|buchen|'
            r'provides?|offers?|receive|book)\b', sentence, re.I
        )
        # 'können' alone can be speculative, but combined with a concrete
        # consultation and 'in Anspruch nehmen' it is an actionable offer.
        if advice and action:
            return True
    return False


def _practical_service_query(query: str) -> bool:
    """Only reserve a service slot for queries asking for practical support.

    Do not displace monetary grant evidence in grant/subsidy questions.
    This only affects retrieval and never changes policy classifications.
    """
    q = str(query or '').casefold()
    if re.search(r'\b(?:zuschuss\w*|finanziell\w*|subsid\w*|grant\w*|'
                 r'förderung\w*|foerderung\w*|fördert|foerdert|'
                 r'funding|cash\s+grant)\b', q):
        return False
    return bool(re.search(
        r'unterstütz\w*|unterstuetz\w*|berat\w*|advice|'
        r'consultation|assistance|practical\s+support', q
    ))


def focused_metrics(item: dict, want: set[str], intent: list[str]) -> None:
    """Promote documents matching the actual subject; do not turn scores into truth."""
    title = unquote(urlsplit(item['url']).path) + ' ' + (item.get('heading') or '')
    title_topics = title_categories(item['url'], item.get('heading') or '')
    passage_topics = categories(item.get('text', ''))
    doc_is_specialized = bool(re.search(r'(?i)(richtlinie|förderprogramm|foerderprogramm|förderung|foerderung|/foerderungen/|/förderungen/)', title))
    off_topic = bool(want and title_topics and title_topics.isdisjoint(want) and doc_is_specialized and not passage_topics.intersection(want))
    exact_topic = bool(want and title_topics.intersection(want))
    passage_topic = bool(want and passage_topics.intersection(want))
    # A generic landing page can carry valid evidence even if its title is general.
    score = float(item['retrieval_score'])
    if exact_topic:
        score += 9
        if re.search(r'(?i)richtlinie|förderprogramm|foerderprogramm', title):
            score += 3
    elif off_topic:
        score -= 24
    elif passage_topic:
        score += 2
    if want and not (exact_topic or passage_topic):
        score -= 6
    # Authority-aware *candidate selection*, not a policy claim. The original
    # corpus includes regional reports and coverage of unrelated cities under
    # the target AGS; prefer official city evidence before costly BGE reranking.
    official_city = _official_municipal_host(item)
    request_query = str(item.get('request_query') or '')
    practical_query = (_practical_service_query(request_query) if request_query else True)
    concrete_service = bool(official_city and passage_topic and practical_query and
                            _concrete_municipal_service_passage(item.get('text', ''), want))
    if official_city and (exact_topic or passage_topic):
        score += 10
    # Promote real consultation offers over generic advice-page introductions.
    # Finance-focused queries still prioritize grants and binding programmes.
    financial_intent = any(re.search(r'förd|foerd|zuschuss|subsid|grant|fund|finanz',
                                     str(t), re.I) for t in intent)
    # A user's exact question outranks broad query-expansion keywords.
    if request_query:
        financial_intent = not practical_query
    if concrete_service and not financial_intent:
        score += 12
    item['official_municipal_source'] = official_city
    item['concrete_service_candidate'] = concrete_service
    item['requested_topics'] = sorted(want)
    item['source_title_topics'] = sorted(title_topics)
    item['source_off_topic'] = off_topic
    item['source_topic_match'] = exact_topic
    item['retrieval_score'] = round(score, 4)


def select_pdf_documents(docs: list[dict], rows: list[dict], want: set[str], limit: int) -> list[dict]:
    keyed = {(d['document_id'], d['municipality_id']): d for d in docs}
    stats = defaultdict(lambda: -1e12)
    for r in rows:
        key = (r['document_id'], r['municipality_id'])
        if key in keyed:
            stats[key] = max(stats[key], r['retrieval_score'])
    candidates = []
    for key, doc in keyed.items():
        if not v3.pdf_url(doc['url']):
            continue
        # Do not spend PDF download time repairing obviously unrelated guidelines.
        if want and any(r['source_off_topic'] for r in rows
                        if (r['document_id'], r['municipality_id']) == key):
            continue
        candidates.append((stats[key], doc))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]['url']))
    return [doc for _, doc in candidates[:limit]]


def tls_context(mode: str):
    if mode == 'system':
        return ssl.create_default_context()
    if mode == 'certifi':
        try:
            import certifi
        except ImportError as exc:
            raise RuntimeError('certifi unavailable: python -m pip install certifi') from exc
        return ssl.create_default_context(cafile=certifi.where())
    if mode == 'windows':
        try:
            import truststore
        except ImportError as exc:
            raise RuntimeError('Windows TLS backend requires: python -m pip install truststore') from exc
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    raise ValueError('Unknown trust mode')


def cert_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, URLError) and isinstance(exc.reason, ssl.SSLCertVerificationError):
        return True
    return 'CERTIFICATE_VERIFY_FAILED' in str(exc)


def fetch_verified_pdf(url: str, cache: Path, local_dir: Path | None, tls_mode: str = 'auto', max_bytes: int = 30_000_000) -> tuple[Path, str]:
    """Use existing local PDF or secure HTTPS, never certificate verification bypass."""
    if not v3.pdf_url(url):
        raise ValueError('Only public HTTPS PDFs can be fetched')
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]
    stored = cache / (key + '.pdf')
    if v3.safe_pdf(stored):
        return stored, 'verified_cache_file'
    basename = unquote(urlsplit(url).path.split('/')[-1])
    if local_dir:
        downloaded = local_dir / basename
        if downloaded.is_file() and v3.safe_pdf(downloaded):
            if downloaded.stat().st_size > max_bytes:
                raise ValueError('Local PDF exceeds size limit')
            return downloaded, 'user_local_pdf_source_not_independently_verified'
    modes = ['system', 'certifi', 'windows'] if tls_mode == 'auto' else [tls_mode]
    last_exc = None
    cache.mkdir(parents=True, exist_ok=True)
    for mode in modes:
        try:
            ctx = tls_context(mode)
        except RuntimeError as exc:
            last_exc = exc
            continue
        temp = cache / (key + '.partial')
        try:
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; AcademicPolicyResearch/1.0)',
                                        'Accept': 'application/pdf'})
            with urlopen(req, timeout=35, context=ctx) as resp, temp.open('wb') as fh:
                final = urlsplit(resp.geturl())
                if final.scheme != 'https':
                    raise ValueError('PDF redirected away from HTTPS')
                count = 0
                while True:
                    buf = resp.read(65536)
                    if not buf:
                        break
                    count += len(buf)
                    if count > max_bytes:
                        raise ValueError('PDF exceeds size limit')
                    fh.write(buf)
            if not v3.safe_pdf(temp):
                raise ValueError('Download was not a PDF')
            temp.replace(stored)
            return stored, 'https_' + mode
        except Exception as exc:
            last_exc = exc
            if not cert_error(exc):
                raise
            print(f'  TLS verification failed with {mode} trust; trying another verified trust source', flush=True)
        finally:
            temp.unlink(missing_ok=True)
    raise RuntimeError('Secure PDF download unavailable. Download the source using your web browser ' 
                       'to the --pdf-dir folder, retaining its original filename. '
                       f'Last TLS issue: {last_exc}')


def repair_focused(docs: list[dict], topic: list[str], intent: list[str], want: set[str], *,
                   cache: Path, local_dir: Path | None, max_pdfs: int, tls_mode: str) -> list[dict]:
    out = []
    for doc in docs:
        print('PDF REPAIR:', doc['url'], flush=True)
        try:
            file, method = fetch_verified_pdf(doc['url'], cache, local_dir, tls_mode)
            pages = v3.pdf_pages(doc['url'], file, cache)
            pieces = v3.passages_from_pdf(pages, doc, topic, intent)
            for row in pieces:
                focused_metrics(row, want, intent)
                row['pdf_acquisition'] = method
            out.extend(pieces)
            print(f'  METHOD: {method}; PAGES: {len(pages)}; readable evidence passages: {len(pieces)}', flush=True)
        except Exception as exc:
            print(f'  PDF repair unavailable: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    return out


def _preserve_official_service_passage(rows: list[dict], query: str) -> None:
    """Preserve one source-backed practical-service passage in the top eight.

    Prefer a municipal HTML service page over a generic or historic PDF when
    both are already present in the selected shortlist. No URLs, city names,
    dates or policy labels are hard-coded. This is retrieval only.
    """
    if len(rows) <= 8 or not _practical_service_query(query):
        return
    concrete = [r for r in rows if r.get('official_municipal_source')
                and r.get('concrete_service_candidate')]
    if not concrete:
        return
    def web_page(r: dict) -> bool:
        path = unquote(urlsplit(str(r.get('url') or '')).path).casefold()
        return not path.endswith('.pdf') and (
            path.endswith('.html') or path.endswith('.htm') or '/internet/' in path)
    preferred = next((r for r in concrete if web_page(r)), concrete[0])
    if any(r is preferred for r in rows[:8]):
        return
    rows.remove(preferred)
    rows.insert(7, preferred)


def _diversify_final_evidence(rows: list[dict], *, limit: int = 8) -> None:
    """Prevent one document/host monopolizing a small citation shortlist.

    Work only within the already topic-filtered, provenance-carrying V3 shortlist.
    Keep strong same-document passages when needed for dates and eligibility;
    promote alternative providers only if their ranking is near the cutoff.
    Does not use municipality names, benchmark URLs, labels or policy decisions.
    """
    if len(rows) <= limit:
        return

    def score(row: dict) -> float:
        return float(row.get('final_rank_score', row.get('retrieval_score', 0)) or 0)

    cutoff = score(rows[limit - 1])
    floor = cutoff - 0.065  # Do not elevate weak, off-topic evidence for diversity.
    chosen = []
    deferred = []
    doc_counts = defaultdict(int)
    host_counts = defaultdict(int)
    host_limit = max(1, (3 * limit + 3) // 4)  # six of eight at most, if alternatives exist

    for idx, row in enumerate(rows):
        scope = (str(row.get('document_id') or ''), str(row.get('municipality_id') or ''))
        host = (urlsplit(str(row.get('url') or '')).hostname or '').casefold().removeprefix('www.')
        # Two different indexed passages from the same policy can establish both
        # its measure and effective date; do not require every slot to be unique.
        crowded = doc_counts[scope] >= 2 or (host and host_counts[host] >= host_limit)
        admissible = idx < limit or score(row) >= floor
        if len(chosen) < limit and admissible and not crowded:
            chosen.append(row)
            doc_counts[scope] += 1
            if host:
                host_counts[host] += 1
        else:
            deferred.append(row)

    if len(chosen) < limit:
        # When there are too few alternatives, retain the original top-ranked
        # passages rather than padding with low-scoring documents.
        chosen.extend(deferred[:limit - len(chosen)])
    selected = {id(row) for row in chosen}
    rows[:] = chosen + [row for row in rows if id(row) not in selected]


def focused_rerank(query: str, rows: list[dict]):
    if not rows:
        return rows, 'no-evidence'
    try:
        qvec = v3.base.embed_one(query)
        for r in rows:
            doc_text = (r.get('heading') or '') + '\n' + r['text']
            sim = float(np.dot(qvec, v3.base.embed_one(doc_text[:1350])))
            r['bge_similarity'] = round(sim, 6)
        low = min(r['retrieval_score'] for r in rows)
        high = max(r['retrieval_score'] for r in rows)
        gap = max(1., high - low)
        for r in rows:
            lexical = (r['retrieval_score'] - low) / gap
            r['final_rank_score'] = round(.40*r['bge_similarity'] + .42*lexical + .18*r['readability'], 6)
        rows.sort(key=lambda r: (-r['final_rank_score'], -r['retrieval_score'], r['document_id']))
        _diversify_final_evidence(rows)
        _preserve_official_service_passage(rows, query)
        return rows, 'expanded-lexical+topic-specificity+quality+BGE-M3'
    except Exception as exc:
        print(f'WARNING: BGE-M3 unavailable ({type(exc).__name__}: {exc}); falling back to lexical ranking', file=sys.stderr)
        rows.sort(key=lambda r: (-r['retrieval_score'], -r['readability'], r['document_id']))
        _diversify_final_evidence(rows)
        _preserve_official_service_passage(rows, query)
        return rows, 'expanded-lexical+topic-specificity+quality'


def date_evidence(rows: list[dict], relevant_doc_ids: set[tuple[str, str]], limit=12):
    """Collect only strict temporal phrases from the same exact policy documents."""
    found = []
    seen = set()
    ordered = sorted((r for r in rows if (r['document_id'], r['municipality_id']) in relevant_doc_ids),
                     key=lambda r: (r['source_off_topic'], -r['retrieval_score'], r['document_id']))
    for r in ordered:
        for classification, regex in DATE_RULES:
            for match in regex.finditer(r['text']):
                phrase = v3.normalize(match.group(0))
                key = (r['document_id'], classification, phrase.casefold())
                if key in seen:
                    continue
                seen.add(key)
                found.append({'kind': classification, 'text': phrase, 'municipality_id': r['municipality_id'],
                              'document_id': r['document_id'], 'url': r['url'], 'page': r.get('page'),
                              'status_verified': False})
                if len(found) >= limit:
                    return found
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--query', required=True)
    p.add_argument('--municipality-id')
    p.add_argument('--lexical-only', action='store_true')
    p.add_argument('--repair-pdfs', action='store_true')
    p.add_argument('--pdf-dir', type=Path, help='Folder containing PDFs downloaded in a browser (original filenames)')
    p.add_argument('--cache-dir', type=Path, default=v3.CACHE_ROOT)
    p.add_argument('--tls', choices=['auto','system','certifi','windows'], default='auto')
    p.add_argument('--max-pdfs', type=int, default=3)
    p.add_argument('--rerank', type=int, default=12)
    p.add_argument('--top-k', type=int, default=6)
    p.add_argument('--min-quality', type=float, default=.52)
    p.add_argument('--save-json', type=Path)
    a = p.parse_args()
    if not 1 <= a.rerank <= 40 or not 1 <= a.top_k <= a.rerank:
        p.error('--rerank must be 1..40; --top-k must be 1..--rerank')
    if not 1 <= a.max_pdfs <= 10 or not 0 <= a.min_quality <= 1:
        p.error('--max-pdfs must be 1..10 and --min-quality 0..1')
    rows, topic, intent, docs = v3.fetch_candidates(a.query, a.municipality_id)
    wanted = query_categories(a.query)
    for row in rows:
        focused_metrics(row, wanted, intent)
    selected_docs = select_pdf_documents(docs, rows, wanted, a.max_pdfs)
    repaired = repair_focused(selected_docs, topic, intent, wanted, cache=a.cache_dir,
                              local_dir=a.pdf_dir, max_pdfs=a.max_pdfs, tls_mode=a.tls) if a.repair_pdfs else []
    rows.extend(repaired)
    # Hard filter only clearly incompatible *policy-specific* document titles;
    # generic landing pages, background, utility schemes and cross-topic questions stay.
    candidates = [r for r in rows if not r['source_off_topic']]
    removed = len(rows) - len(candidates)
    shortlist, stats = v3.shortlist(candidates, max_count=a.rerank, topk=a.top_k, min_quality=a.min_quality)
    if a.lexical_only:
        ranked = sorted(shortlist, key=lambda r: (-r['retrieval_score'], -r['readability'], r['document_id']))
        method = 'expanded-lexical+topic-specificity+quality'
    else:
        ranked, method = focused_rerank(a.query, shortlist)
    host_cap = max(1, (3 * a.top_k + 3) // 4)
    host_counts = defaultdict(int)
    shown, deferred = [], []
    for item in ranked:
        host = (urlsplit(item["url"]).hostname or "").lower().removeprefix("www.")
        if host_counts[host] < host_cap and len(shown) < a.top_k:
            shown.append(item)
            host_counts[host] += 1
        else:
            deferred.append(item)
    if len(shown) < a.top_k:
        shown.extend(deferred[:a.top_k - len(shown)])
    doc_set = {(r['document_id'], r['municipality_id']) for r in shown if not r['source_off_topic']}
    hints = date_evidence(candidates, doc_set)
    print(f'OFF-TOPIC POLICY DOCUMENTS: excluded {removed} passages')
    print(f'QUALITY FILTER: excluded {stats["quality_filtered"]} / {stats["candidate_count"]} on-topic passages')
    print(f'RERANK SHORTLIST: {len(ranked)} across {stats["docs_covered"]} documents')
    print('METHOD:', method)
    print('EVIDENCE ONLY. Effective dates do not prove programmes are currently funded or open.\n')
    for idx, item in enumerate(shown, 1):
        print(f'[{idx}] Municipality: {item["municipality_id"]} | Document: {item["document_id"]}')
        print('SOURCE:', item['url'] + (f'#page={item["page"]}' if item.get('page') else ''))
        print('ORIGIN:', item['source'], '| Readability heuristic:', item['readability'],
              '| Topic match in source:', item['source_topic_match'])
        if 'bge_similarity' in item:
            print('BGE similarity:', item['bge_similarity'])
        if item.get('heading'):
            print('HEADING:', item['heading'][:160])
        print('TEXT:', v3.normalize(item['text'])[:1050])
        print()
    if hints:
        print('DATE EVIDENCE (matching documents only; no active-status inference):')
        for hint in hints:
            print(' -', hint['kind'], '|', hint['text'][:120], '|', hint['url'][:150])
    if a.save_json:
        payload = {'query': a.query, 'municipality_id': a.municipality_id,
                   'generated_utc': datetime.now(timezone.utc).isoformat(),
                   'method': method, 'requested_topics': sorted(wanted),
                   'source_database': str(v3.base.SOURCE), 'lexical_database': str(v3.base.LEXICAL),
                   'statistics': {**stats, 'off_topic_passages_excluded': removed,
                                  'pdf_reextracted_passages': len(repaired)},
                   'results': shown, 'date_evidence_unverified': hints,
                   'disclaimer': 'Retrieval only. Policy status, applicability, dates and budget availability have not been established.'}
        a.save_json.parent.mkdir(parents=True, exist_ok=True)
        a.save_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print('SAVED EVIDENCE:', a.save_json)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
