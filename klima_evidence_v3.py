"""Klima GraphRAG: quality-gated, document-aware policy evidence retrieval.

Reads the existing *full* PDF-V2 source and FTS databases without changing them.
Searches a broad lexical pool, inspects source documents (not just the first few
chunks), filters scrambled PDF fragments, and optionally re-extracts a few PDFs
into a separate, provenance-tracked local cache with PyMuPDF.

This is evidence retrieval ONLY: never infers active policies or YES/NO/UNKNOWN.

Dependencies: klima_fast_retrieval.py, klima_fast_retrieval_v2.py, numpy.
For --repair-pdfs: PyMuPDF or pure-Python pypdf (python -m pip install pypdf).

Examples:
 python klima_evidence_v3.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --lexical-only
 python klima_evidence_v3.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --repair-pdfs --lexical-only
 python klima_evidence_v3.py --query "Foerdert Regensburg Photovoltaik?" --municipality-id 09362000 --repair-pdfs --rerank 12 --top-k 6 --save-json pv_evidence.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import numpy as np
import klima_fast_retrieval as base
import klima_fast_retrieval_v2 as v2

CACHE_ROOT = Path('crawler/data/repair_cache/pdfs')
STOPWORDS = set('der die das den dem des ein eine einer einem und oder in im am an auf aus zu zur zum mit fuer für von vom bei als ist sind wird werden es sich sie er wir nach vor durch auch sowie nicht nur bis ab oder kann können einer eines diese dieser diesen diesem the a an and of to from for with is are be may shall will as by'.split())
GOOD_DOCUMENT_WORDS = ('richtlinie', 'foerderrichtlinie', 'förderrichtlinie', 'foerderprogramm', 'förderprogramm', 'satzung', 'bekanntmachung', 'foerderung', 'förderung')
LOW_DOCUMENT_WORDS = ('haushalt-', 'zählerstand', 'zaehlerstand', 'antragsformular', 'foerderantrag', 'förderantrag', 'erklärung', 'erklaerung')
DATE_PATTERNS = [
    re.compile(r'(?i)\b(?:tritt|treten)\s+(?:am\s+)?(?:\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*[a-zäöü]+\s+\d{4})\s+in\s+kraft\b'),
    re.compile(r'(?i)\b(?:gültig\s+ab|gueltig\s+ab|gültig\s+bis|befristet\s+bis|in\s+kraft\s+ab|außer\s+kraft\s+am)\s*:?\s*[^\n.;]{0,90}'),
    re.compile(r'(?i)\b(?:vom|ab|bis|am)\s+\d{1,2}\.\s*\d{1,2}\.\s*20\d{2}\b'),
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def tokens(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text.casefold(), re.UNICODE)


def normalize(text: str) -> str:
    return ' '.join(str(text or '').split())


def readability(text: str) -> tuple[float, list[str]]:
    """Conservative evidence suitability heuristic, not an OCR accuracy metric."""
    raw = str(text or '')
    words = tokens(raw)
    n = len(words)
    flags = []
    if n < 7:
        return 0.0, ['too_short']
    score = min(1.0, n / 32.0)
    chars = [c for c in raw if not c.isspace()]
    if not chars:
        return 0.0, ['empty']
    alpha_frac = sum(c.isalpha() for c in chars) / len(chars)
    if alpha_frac < .48:
        score -= .42; flags.append('low_letter_ratio')
    elif alpha_frac < .60:
        score -= .18; flags.append('mixed_symbols')
    long_words = sum(len(w) > 31 for w in words)
    if long_words >= 2 or long_words / n > .11:
        score -= .28; flags.append('glued_words')
    if raw.count('\ufffd') or raw.count('\x00'):
        score -= .40; flags.append('invalid_characters')
    if re.search(r'[─│┴┬┼]{3,}|[\w)]{20,}\(', raw):
        score -= .33; flags.append('layout_artifacts')
    # A normal natural-language paragraph has common function words.
    if n >= 16 and sum(word in STOPWORDS for word in words) < max(1, int(n * .055)):
        score -= .13; flags.append('few_function_words')
    if n < 16:
        score -= .12; flags.append('short_fragment')
    return round(max(0.0, min(1.0, score)), 4), flags


def relevance(text: str, topic: list[str], intent: list[str]) -> float:
    lowered = normalize(text).casefold()
    topic_hits = sum(term in lowered for term in dict.fromkeys(topic))
    intent_hits = sum(term in lowered for term in dict.fromkeys(intent))
    return min(5, topic_hits) * 1.5 + min(4, intent_hits) * 1.35 + (3 if topic_hits and intent_hits else 0)


def document_bias(url: str, heading: str = '') -> float:
    s = (unquote(urlsplit(url).path) + ' ' + heading).casefold()
    value = 0.0
    if any(k in s for k in GOOD_DOCUMENT_WORDS):
        value += 2.5
    if any(k in s for k in LOW_DOCUMENT_WORDS):
        value -= 1.5
    if 'haushalt-' in s:
        value -= 3
    return value


def add_metrics(row: dict, topic: list[str], intent: list[str]):
    quality, flags = readability(row.get('text', ''))
    row['readability'] = quality
    row['quality_flags'] = flags
    row['topic_intent_score'] = relevance(row.get('heading', '') + ' ' + row.get('text', ''), topic, intent)
    row['document_priority'] = document_bias(row['url'], row.get('heading', ''))
    # A passage from a clear regulation is worth retaining even if it is missing
    # query words; semantic similarity cannot restore unavailable candidates.
    row['retrieval_score'] = round(row['topic_intent_score'] + row['document_priority']
                                   + 3.0 * quality + min(3.0, row.get('fts_weight', 0)), 4)


def validate_scope(src: sqlite3.Connection, doc_id: str, city: str) -> bool:
    matches = {str(row[0]) for row in src.execute(
        'SELECT municipality_id FROM documents_raw WHERE document_id=? AND municipality_id IS NOT NULL', (doc_id,))}
    if src.execute("SELECT 1 FROM sqlite_master WHERE name='municipality_documents' AND type='table'").fetchone():
        matches.update(str(row[0]) for row in src.execute(
            'SELECT municipality_id FROM municipality_documents WHERE document_id=?', (doc_id,)))
    return city in matches


def _topical_advice_service_page(row: dict) -> bool:
    """Retrieval recall signal, NEVER proof that a service is operating.

    Reserve one slot for a readable, subject-relevant public advice/service
    page. Downstream verification still establishes issuer and current status.
    """
    path = unquote(urlsplit(str(row.get('url') or '')).path).casefold()
    return (bool(re.search(r'(?:beratung|beratungs|advice|consult|service|support|angebote)', path))
            and bool(path.endswith(('.html', '.htm')) or '/internet/' in path)
            and float(row.get('topic_intent_score') or 0) >= 1.0
            and float(row.get('readability') or 0) >= 0.52)


def fetch_candidates(question: str, municipality_id: str | None):
    plan, topic, intent = v2.query_plan(question)
    with closing(base.readonly(base.LEXICAL)) as fts, closing(base.readonly(base.SOURCE)) as src:
        n_fts = fts.execute('SELECT COUNT(*) FROM chunks_fts').fetchone()[0]
        n_src = src.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        if not n_src or n_src != n_fts:
            raise RuntimeError(f'FTS and corpus do not match: {n_fts} vs {n_src}')
        all_hits = {}
        for expression, n, channel in plan:
            if municipality_id:
                sql = ('SELECT chunk_id, municipality_id, bm25(chunks_fts) FROM chunks_fts '
                       'WHERE chunks_fts MATCH ? AND municipality_id=? ORDER BY bm25(chunks_fts) LIMIT ?')
                params = (expression, municipality_id, n)
            else:
                sql = ('SELECT chunk_id, municipality_id, bm25(chunks_fts) FROM chunks_fts '
                       'WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?')
                params = (expression, n)
            hits = fts.execute(sql, params).fetchall()
            print(f'SEARCH {channel}: {len(hits)} fetched', flush=True)
            for rank, (cid, city, bm) in enumerate(hits, 1):
                key = (str(cid), str(city))
                hit = all_hits.setdefault(key, {'chunk_id': str(cid), 'municipality_id': str(city),
                                                'channels': {}, 'bm25': float(bm)})
                hit['channels'][channel] = min(rank, hit['channels'].get(channel, rank))
                hit['bm25'] = min(hit['bm25'], float(bm))
        print('UNIQUE FTS HITS:', len(all_hits), flush=True)
        result = []
        docs = {}
        for (cid, city), hit in all_hits.items():
            row = src.execute('SELECT c.text,c.heading_context,c.document_id,d.url_canonical FROM chunks c '
                              'JOIN documents_raw d ON c.document_id=d.document_id WHERE c.chunk_id=?', (cid,)).fetchone()
            if not row:
                continue
            text, heading, doc, url = row
            if not validate_scope(src, str(doc), city):
                continue
            hit.update(text=str(text or ''), heading=str(heading or ''), document_id=str(doc),
                       url=str(url or ''), source='indexed_chunk', page=None,
                       request_query=question,
                       fts_weight=sum(1 / (1 + rank / 40) for rank in hit['channels'].values()))
            add_metrics(hit, topic, intent)
            result.append(hit)
            doc_hit = docs.setdefault((str(doc), city), {'document_id': str(doc), 'url': str(url or ''),
                                                  'municipality_id': city, 'hits': 0,
                                                  'priority': -999})
            doc_hit['hits'] += 1
            doc_hit['priority'] = max(doc_hit['priority'], hit['retrieval_score'])
        # Inspect additional indexed chunks from strong matching documents. This
        # recovers dates/definitions whose text does not repeat the question terms.
        ranked_docs = sorted(docs.values(), key=lambda d: (-d['priority'], -d['hits'], d['document_id']))[:16]
        # Reserve at most one existing document-context slot for a topical
        # advice/service page; no new FTS search, crawl or embedding.
        advice_hits = [r for r in result if _topical_advice_service_page(r)]
        if advice_hits and ranked_docs:
            best_advice = max(advice_hits, key=lambda r: (r['retrieval_score'],
                                                        r['readability'], r['url']))
            advice_doc = docs.get((best_advice['document_id'], best_advice['municipality_id']))
            if advice_doc is not None and all(
                (d['document_id'], d['municipality_id']) !=
                (advice_doc['document_id'], advice_doc['municipality_id'])
                for d in ranked_docs
            ):
                ranked_docs[-1] = advice_doc
        seen = {(r['chunk_id'], r['municipality_id']) for r in result}
        count = 0
        for doc in ranked_docs:
            extra = src.execute('SELECT c.chunk_id,c.text,c.heading_context FROM chunks c '
                                'WHERE c.document_id=? ORDER BY c.chunk_id LIMIT 350', (doc['document_id'],)).fetchall()
            for cid, txt, heading in extra:
                key = (str(cid), doc['municipality_id'])
                if key in seen:
                    continue
                candidate = {'chunk_id': str(cid), 'municipality_id': doc['municipality_id'],
                             'document_id': doc['document_id'], 'url': doc['url'],
                             'text': str(txt or ''), 'heading': str(heading or ''),
                             'channels': {'same-document': 999}, 'fts_weight': 0,
                             'bm25': None, 'page': None, 'source': 'indexed_document_context',
                             'request_query': question}
                add_metrics(candidate, topic, intent)
                # A relevant policy date can be in another chunk
                # of the SAME previously retrieved document.
                date_anchor = any(
                    pattern.search(candidate['text'])
                    for pattern in DATE_PATTERNS[:2]
                )
                candidate['policy_date_anchor'] = date_anchor
                if candidate['readability'] < .52 or (
                    candidate['topic_intent_score'] < 1.0
                    and not date_anchor
                ):
                    continue
                if date_anchor:
                    candidate['retrieval_score'] = round(
                        candidate['retrieval_score'] + 3.0, 4
                    )
                result.append(candidate)
                seen.add(key); count += 1
        print(f'DOCUMENT CONTEXT: added {count} readable passages from {len(ranked_docs)} documents', flush=True)
    return result, topic, intent, ranked_docs


def pdf_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.hostname and parts.hostname not in ('localhost', '127.0.0.1') and (
        '.pdf' in unquote(parts.path).casefold() or unquote(parts.path).casefold().endswith('/pdf'))


def safe_pdf(path: Path) -> bool:
    try:
        with path.open('rb') as f:
            return f.read(5) == b'%PDF-'
    except OSError:
        return False


def fetch_pdf(url: str, cache: Path, local_dir: Path | None, max_bytes=30_000_000) -> Path:
    if not pdf_url(url):
        raise ValueError('Not a public HTTPS PDF source')
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]
    cache_pdf = cache / (key + '.pdf')
    if safe_pdf(cache_pdf):
        return cache_pdf
    name = unquote(urlsplit(url).path.split('/')[-1])
    if local_dir:
        path = local_dir / name
        if path.is_file() and safe_pdf(path):
            if path.stat().st_size > max_bytes:
                raise ValueError('Local PDF exceeds size limit')
            return path
    cache.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; AcademicPolicyResearch/1.0)',
                                'Accept': 'application/pdf'})
    tmp = cache / (key + '.download')
    try:
        with urlopen(req, timeout=35) as resp, tmp.open('wb') as output:
            final_url = resp.geturl()
            if urlsplit(final_url).scheme != 'https':
                raise ValueError('PDF redirected away from HTTPS')
            total = 0
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise ValueError('PDF exceeds 30 MB safety limit')
                output.write(block)
        if not safe_pdf(tmp):
            raise ValueError('Source returned non-PDF content')
        tmp.replace(cache_pdf)
        return cache_pdf
    finally:
        tmp.unlink(missing_ok=True)


def pdf_pages(url: str, file: Path, cache: Path):
    try:
        import fitz
    except (ImportError, OSError):
        fitz = None
    if fitz is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError('PDF extraction needs pypdf: python -m pip install pypdf') from exc
    file_hash = hashlib.sha256(file.read_bytes()).hexdigest()
    target = cache / (hashlib.sha256(url.encode('utf-8')).hexdigest()[:24] + '.pages.json')
    if target.is_file():
        try:
            old = json.loads(target.read_text(encoding='utf-8'))
            if old.get('pdf_sha256') == file_hash and old.get('source_url') == url:
                return old['pages']
        except (ValueError, OSError, KeyError):
            pass
    pages = []
    if fitz is not None:
        with fitz.open(file) as pdf:
            if pdf.needs_pass:
                raise ValueError('Password-protected PDF')
            for i, page in enumerate(pdf, 1):
                unsorted = normalize(page.get_text('text', sort=False))
                sorted_text = normalize(page.get_text('text', sort=True))
                # Different layout modes can differ materially for multi-column PDFs.
                primary = sorted_text if readability(sorted_text)[0] >= readability(unsorted)[0] else unsorted
                pages.append({'page': i, 'text': primary, 'quality': readability(primary)[0]})
    else:
        reader = PdfReader(str(file))
        if reader.is_encrypted:
            raise ValueError('Password-protected PDF')
        for i, page in enumerate(reader.pages, 1):
            plain = normalize(page.extract_text() or '')
            try:
                laid_out = normalize(page.extract_text(extraction_mode='layout') or '')
            except (TypeError, ValueError, AttributeError):
                laid_out = ''
            primary = laid_out if readability(laid_out)[0] >= readability(plain)[0] else plain
            pages.append({'page': i, 'text': primary, 'quality': readability(primary)[0]})
    cache.mkdir(parents=True, exist_ok=True)
    new = {'source_url': url, 'pdf_sha256': file_hash, 'created_utc': stamp(),
           'page_count': len(pages), 'pages': pages}
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(new, ensure_ascii=False), encoding='utf-8')
    temp.replace(target)
    return pages


def passages_from_pdf(pages: list[dict], document: dict, topic: list[str], intent: list[str]) -> list[dict]:
    candidates = []
    for page in pages:
        whole = page.get('text', '')
        if len(tokens(whole)) < 10:
            continue
        # Windows deliberately overlap so clauses spanning segments stay available.
        start = 0; number = 0
        while start < len(whole):
            end = min(len(whole), start + 1200)
            if end < len(whole):
                boundary = whole.rfind(' ', start + 800, end)
                if boundary > start + 800:
                    end = boundary
            fragment = whole[start:end].strip()
            q, flags = readability(fragment)
            if q >= .5:
                row = {'chunk_id': None, 'municipality_id': document['municipality_id'],
                       'document_id': document['document_id'], 'url': document['url'],
                       'text': fragment, 'heading': f'PDF page {page["page"]}',
                       'channels': {'pdf-re-extracted': 1}, 'fts_weight': 0,
                       'bm25': None, 'page': page['page'], 'source': 'pdf_reextracted',
                       'passage_key': f'{document["document_id"]}:p{page["page"]}:w{number}'}
                add_metrics(row, topic, intent)
                candidates.append(row)
            if end == len(whole):
                break
            start = max(start + 1, end - 170)
            number += 1
    # Limit each PDF's supplementary passages to the best varied pages.
    candidates.sort(key=lambda r: (-r['retrieval_score'], r['page'], r['passage_key']))
    output, per_page = [], defaultdict(int)
    for row in candidates:
        if per_page[row['page']] >= 2:
            continue
        output.append(row)
        per_page[row['page']] += 1
        if len(output) >= 24:
            break
    return output


def repair_documents(documents: list[dict], topic: list[str], intent: list[str], *,
                     cache: Path, local_dir: Path | None, maximum: int) -> list[dict]:
    done = []
    pdfs = [d for d in documents if pdf_url(d['url'])]
    # Prefer actual directives to forms, unrelated budgets or meter declarations.
    pdfs.sort(key=lambda d: (-(d['priority'] + document_bias(d['url']) * 2), -d['hits'], d['url']))
    for doc in pdfs[:maximum]:
        print('PDF REPAIR:', doc['url'], flush=True)
        try:
            file = fetch_pdf(doc['url'], cache, local_dir)
            pages = pdf_pages(doc['url'], file, cache)
            pieces = passages_from_pdf(pages, doc, topic, intent)
            print(f'  re-extracted {len(pages)} pages; selected {len(pieces)} readable passages', flush=True)
            done.extend(pieces)
        except Exception as exc:
            print(f'  WARNING: PDF repair unavailable ({type(exc).__name__}: {exc}); preserving indexed evidence',
                  file=sys.stderr, flush=True)
    return done


def shortlist(rows: list[dict], *, max_count: int, topk: int, min_quality: float) -> tuple[list[dict], dict]:
    eligible = [r for r in rows if r['readability'] >= min_quality]
    rejected = len(rows) - len(eligible)
    if not eligible:
        print('WARNING: no readable candidate meets the threshold; showing flagged fallbacks', file=sys.stderr)
        eligible = sorted(rows, key=lambda r: (-r['readability'], -r['retrieval_score']))[:max_count]
    # Prefer official-looking guideline/source documents, readability, and true topic/intent.
    eligible.sort(key=lambda r: (-r['retrieval_score'], -r['readability'], r['url'], r.get('chunk_id') or ''))
    # Focus the 12-16 expensive embeddings on a handful of promising source
    # documents so at least two independent passages can be considered per doc.
    best_docs = {}
    for r in eligible:
        scope = (r['document_id'], r['municipality_id'])
        best_docs[scope] = max(best_docs.get(scope, -1e30), r['retrieval_score'])
    doc_budget = min(max_count, max(3, math.ceil(3 * max_count / 4)))
    ranked_docs = sorted(best_docs, key=lambda k: (-best_docs[k], k))
    doc_hosts = {(r["document_id"], r["municipality_id"]): (urlsplit(r["url"]).hostname or "").lower().removeprefix("www.") for r in eligible}
    host_first = list({doc_hosts[k]: k for k in reversed(ranked_docs)}.values())
    host_first.sort(key=lambda k: (-best_docs[k], k))
    host_first = host_first[:min(4, doc_budget)]
    allowed = set((host_first + [k for k in ranked_docs if k not in host_first])[:doc_budget])
    # Reserve at most one document slot for a concrete, topic-relevant
    # official support service *passage* when practical help was requested.
    # This is candidate selection, never proof of a policy's current status.
    service_rows = [
        r for r in eligible
        if r.get('official_municipal_source')
        and r.get('concrete_service_candidate')
        and not r.get('source_off_topic', False)
    ]
    if service_rows and allowed:
        best_service = max(service_rows, key=lambda r: (
            float(r['retrieval_score']), float(r['readability']), str(r.get('chunk_id', ''))))
        service_scope = (best_service['document_id'], best_service['municipality_id'])
        if service_scope not in allowed:
            weakest_scope = min(allowed, key=lambda k: (best_docs[k], k))
            allowed.remove(weakest_scope)
            allowed.add(service_scope)
    eligible = [r for r in eligible if (r['document_id'], r['municipality_id']) in allowed]
    # Prefer the concrete relevant service passage over generic overview text
    # within already-eligible official documents, without deleting policy dates.
    eligible.sort(key=lambda r: (
        -int(bool(r.get('official_municipal_source') and r.get('concrete_service_candidate'))),
        -float(r['retrieval_score']), -float(r['readability']),
        str(r.get('url', '')), str(r.get('chunk_id', ''))))
    selected, per_doc, per_origin = [], defaultdict(int), defaultdict(int)
    seen = set()
    # First pass offers document diversity; subsequent passes add additional
    # *different* passages, rather than repeating a document's first passage.
    for allowance in (1, 2, 3, 4):
        for r in eligible:
            key = (r['municipality_id'], r['document_id'],
                   r.get('passage_key') or r.get('chunk_id'))
            scope = (r['document_id'], r['municipality_id'])
            if key in seen or per_doc[scope] >= allowance:
                continue
            if per_origin[(r['document_id'], r['municipality_id'], r['source'])] >= 3 and r['source'] != 'pdf_reextracted':
                continue
            selected.append(r)
            seen.add(key)
            per_doc[scope] += 1
            per_origin[(r['document_id'], r['municipality_id'], r['source'])] += 1
            if len(selected) >= max_count:
                return selected, {'candidate_count': len(rows), 'quality_filtered': rejected,
                                  'eligible': len(eligible), 'docs_covered': len(per_doc)}
    return selected, {'candidate_count': len(rows), 'quality_filtered': rejected,
                      'eligible': len(eligible), 'docs_covered': len(per_doc)}


def rerank(question: str, rows: list[dict]) -> tuple[list[dict], str]:
    if not rows:
        return rows, 'no-evidence'
    try:
        qvec = base.embed_one(question)
        for row in rows:
            text = (row.get('heading') or '') + '\n' + row['text']
            v = base.embed_one(text[:1350])
            row['bge_similarity'] = round(float(np.dot(qvec, v)), 6)
        # BGE is a similarity signal, not a policy-validity score. Give lexical
        # relevance and text quality a say so nonsense cannot win on similarity alone.
        max_score = max(r['retrieval_score'] for r in rows) or 1
        for row in rows:
            row['final_rank_score'] = round(.65 * row['bge_similarity'] +
                .20 * (row['retrieval_score'] / max_score) +
                .15 * row['readability'], 6)
        rows.sort(key=lambda r: (-r['final_rank_score'], -r['readability'], r['document_id']))
        return rows, 'expanded-lexical+document-context+quality+BGE-M3'
    except Exception as exc:
        print(f'WARNING: BGE-M3 rerank skipped: {type(exc).__name__}: {exc}', file=sys.stderr)
        return rows, 'expanded-lexical+document-context+quality (BGE unavailable)'


def date_hints(rows: list[dict], limit=8) -> list[dict]:
    seen, hints = set(), []
    for row in rows:
        for pattern in DATE_PATTERNS:
            for m in pattern.finditer(row.get('text', '')):
                value = normalize(m.group(0))
                key = (row['document_id'], value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                hints.append({'text': value, 'document_id': row['document_id'],
                              'url': row['url'], 'page': row.get('page'),
                              'verified_effective_date': False})
                if len(hints) >= limit:
                    return hints
    return hints


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--query', required=True)
    p.add_argument('--municipality-id', help='Optional municipality ID')
    p.add_argument('--lexical-only', action='store_true', help='Skip Ollama reranking')
    p.add_argument('--repair-pdfs', action='store_true', help='Re-extract a few retrieved PDFs; cache separate from source DB')
    p.add_argument('--max-pdfs', type=int, default=3, help='Number of candidate PDFs to repair (default 3)')
    p.add_argument('--pdf-dir', type=Path, help='Optional folder containing previously downloaded PDFs with matching filenames')
    p.add_argument('--cache-dir', type=Path, default=CACHE_ROOT)
    p.add_argument('--rerank', type=int, default=16, help='Max passages sent to BGE-M3 (default 16)')
    p.add_argument('--top-k', type=int, default=8)
    p.add_argument('--min-quality', type=float, default=.52)
    p.add_argument('--save-json', type=Path)
    args = p.parse_args()
    if not 1 <= args.max_pdfs <= 10:
        p.error('--max-pdfs must be 1..10')
    if not 1 <= args.rerank <= 40 or not 1 <= args.top_k <= args.rerank:
        p.error('--rerank must be 1..40 and --top-k must be 1..--rerank')
    if not 0 <= args.min_quality <= 1:
        p.error('--min-quality must be between 0 and 1')
    rows, topic, intent, docs = fetch_candidates(args.query, args.municipality_id)
    if args.repair_pdfs:
        repaired = repair_documents(docs, topic, intent, cache=args.cache_dir,
                                    local_dir=args.pdf_dir, maximum=args.max_pdfs)
        rows.extend(repaired)
    else:
        repaired = []
    selected, stats = shortlist(rows, max_count=args.rerank, topk=args.top_k,
                                 min_quality=args.min_quality)
    print(f'QUALITY FILTER: excluded {stats["quality_filtered"]} / {stats["candidate_count"]} passages', flush=True)
    print(f'RERANK SHORTLIST: {len(selected)} across {stats["docs_covered"]} documents', flush=True)
    if args.lexical_only:
        ranked, method = selected, 'expanded-lexical+document-context+quality'
    else:
        ranked, method = rerank(args.query, selected)
    print('METHOD:', method)
    print('EVIDENCE ONLY: Policy existence, dates, issuer and eligibility still require verified reasoning.\n')
    displayed = ranked[:args.top_k]
    for i, row in enumerate(displayed, 1):
        print(f'[{i}] Municipality: {row["municipality_id"]} | Document: {row["document_id"]}')
        print('SOURCE:', row['url'] + (f'#page={row["page"]}' if row.get('page') else ''))
        print('ORIGIN:', row['source'], '| Readability:', row['readability'],
              '| Evidence relevance:', row['topic_intent_score'])
        if row.get('bge_similarity') is not None:
            print('BGE similarity:', row['bge_similarity'])
        if row.get('heading'):
            print('HEADING:', row['heading'][:160])
        print('TEXT:', normalize(row['text'])[:1050])
        print()
    hints = date_hints(rows)
    if hints:
        print('POSSIBLE DATE LANGUAGE (UNVERIFIED, NOT A POLICY STATUS DECISION):')
        for item in hints[:6]:
            print(' -', item['text'][:120], '|', item['url'][:150])
    if args.save_json:
        payload = {'query': args.query, 'municipality_id': args.municipality_id,
                   'generated_utc': stamp(), 'method': method,
                   'source_database': str(base.SOURCE), 'lexical_database': str(base.LEXICAL),
                   'statistics': {**stats, 'pdf_reextracted_passages': len(repaired)},
                   'results': displayed, 'date_language_unverified': hints,
                   'disclaimer': 'Retrieval evidence only: dates, issuer, scope and current eligibility have not been adjudicated'}
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print('SAVED EVIDENCE:', args.save_json)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
