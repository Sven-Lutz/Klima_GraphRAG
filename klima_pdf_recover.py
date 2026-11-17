#!/usr/bin/env python3
"""Safely re-extract locally stored municipal PDFs into a COPY of a crawl SQLite database.

Does not download files, use OCR, or overwrite the source database. Newly extracted
page text is inserted as new segments, leaving existing evidence and IDs intact.
After running, use the project's existing chunk_documents/snapshot/index commands.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def find_raw(path_value: str, source: Path) -> Path | None:
    value = (path_value or '').strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    project = Path.cwd()
    raw_root = source.parent.parent / 'raw'
    options = [candidate, project / candidate, project / 'crawler' / candidate,
               project / 'crawler' / 'data' / candidate, raw_root / candidate,
               raw_root / candidate.name, project / 'crawler' / 'data' / 'raw' / candidate]
    for p in options:
        if p.is_file():
            return p.resolve()
    return None


def extract_pages(path: Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError('PyMuPDF missing: python -m pip install pymupdf') from exc
    data = path.read_bytes()
    if not data.lstrip().startswith(b'%PDF'):
        return []
    pages = []
    with fitz.open(stream=data, filetype='pdf') as pdf:
        for page in pdf:
            # Sort into reading order rather than combining PDF character streams.
            text = page.get_text('text', sort=True).replace('\x00', '').strip()
            tokens = [w for w in text.split() if any(ch.isalpha() for ch in w)]
            if len(tokens) >= 15 and len(text) >= 100:
                pages.append(text)
            else:
                pages.append('')
    return pages


def cols_of(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f'PRAGMA table_info({table})').fetchall()


def insert_segments(conn: sqlite3.Connection, doc_id: str, muni_id: str,
                    url: str, pages: list[str]) -> int:
    cols = cols_of(conn, 'segments')
    if not cols:
        raise RuntimeError('No segments table present')
    names = [x[1] for x in cols]
    if not {'segment_id', 'document_id', 'text'} <= set(names):
        raise RuntimeError('Unrecognized segments schema; requires segment_id, document_id, text')
    conn.row_factory = sqlite3.Row
    template_row = conn.execute('SELECT * FROM segments WHERE document_id=? LIMIT 1', (doc_id,)).fetchone()
    template = dict(template_row) if template_row else {}
    conn.row_factory = None
    # Never invent a required field that we cannot obtain from an existing segment.
    for col in cols:
        _cid, key, _type, not_null, default, is_pk = col
        if not_null and default is None and not is_pk and key not in template \
                and key not in {'segment_id', 'document_id', 'text', 'municipality_id',
                                'heading_context', 'page_ref', 'impact_score', 'is_negative',
                                'created_at'}:
            raise RuntimeError(f'Cannot safely populate required segments field: {key}')
    numeric_seq_cols = {'segment_index', 'segment_idx', 'seg_index', 'ordinal',
                        'index_in_document', 'position', 'order_index', 'segment_no'}
    max_by_col = {}
    for n in numeric_seq_cols & set(names):
        max_by_col[n] = conn.execute(f'SELECT COALESCE(MAX({n}),0) FROM segments WHERE document_id=?',(doc_id,)).fetchone()[0] or 0
    stmt = f"INSERT OR IGNORE INTO segments ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})"
    added = 0
    for page_num, text in enumerate(pages, 1):
        if not text:
            continue
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        seg_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'klima-reextract:{doc_id}:{page_num}:{digest}'))
        existing = conn.execute('SELECT 1 FROM segments WHERE segment_id=?', (seg_id,)).fetchone()
        if existing:
            continue
        row = dict(template)
        row.update(segment_id=seg_id, document_id=doc_id, text=text)
        for key in names:
            k = key.casefold()
            if key != 'segment_id' and next((c[5] for c in cols if c[1] == key), 0) and key not in {'document_id'}:
                row[key] = None  # let an integer surrogate primary key autogenerate
            elif key == 'municipality_id': row[key] = muni_id
            elif key == 'heading_context': row[key] = f'PDF page {page_num} (layout-sorted extraction)'
            elif key == 'page_ref': row[key] = str(page_num)
            elif key == 'impact_score': row[key] = max(30, int(row.get(key) or 0))
            elif key == 'is_negative': row[key] = 0
            elif key == 'created_at': row[key] = datetime.now(timezone.utc).isoformat()
            elif key in max_by_col: row[key] = int(max_by_col[key]) + page_num + 100
            elif k in {'segment_hash', 'text_hash', 'content_hash'}: row[key] = digest
        before = conn.total_changes
        conn.execute(stmt, [row.get(k) for k in names])
        if conn.total_changes > before:
            added += 1
        else:
            raise RuntimeError(f'Page {page_num} was not inserted (unique constraint?)')
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--municipality', required=True)
    ap.add_argument('--max-pdfs', type=int, default=25)
    ap.add_argument('--url-contains', action='append', default=[],
                    help='Repeat to select target PDFs by URL substring; recommended')
    ap.add_argument('--include-all-pdfs', action='store_true')
    args = ap.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not source.is_file():
        ap.error(f'Source database missing: {source}')
    if source == output or output.exists():
        ap.error('Output must be a NEW path different from source (no overwriting)')
    if args.max_pdfs < 1:
        ap.error('--max-pdfs must be >= 1')
    if not args.url_contains and not args.include_all_pdfs:
        ap.error('Specify --url-contains for targeted recovery or --include-all-pdfs')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as read:
        columns = {r[1] for r in cols_of(read, 'documents_raw')}
        required = {'document_id','municipality_id','url_final','raw_path'}
        if not required <= columns:
            ap.error(f'documents_raw missing columns: {required - columns}')
        conditions = ['municipality_id=?']
        params = [args.municipality]
        if not args.include_all_pdfs:
            conditions.append('(' + ' OR '.join('url_final LIKE ?' for _ in args.url_contains) + ')')
            params.extend('%' + t + '%' for t in args.url_contains)
        where = ' AND '.join(conditions)
        sql = f'SELECT document_id, municipality_id, url_final, raw_path, content_type, raw_ext FROM documents_raw WHERE {where} ORDER BY url_final LIMIT ?'
        rows = read.execute(sql, (*params, args.max_pdfs)).fetchall()
        candidates = []
        for doc_id, muni, url, raw_path, ctype, ext in rows:
            parts = urlsplit(url or '')
            looks_pdf = ('pdf' in str(ctype).lower() or 'pdf' in str(ext).lower()
                         or parts.path.lower().endswith(('.pdf', '/getfile.php'))
                         or ('loaddocument.phtml' in parts.path.lower() and 'pdf' in parts.query.lower()))
            if looks_pdf:
                candidates.append((doc_id, muni, url, raw_path))
    print('Selected PDF document records:', len(candidates), flush=True)
    if not candidates:
        print('No matching PDF documents. No database copy created.')
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as read, sqlite3.connect(output) as write:
        read.backup(write)
    recovered = 0
    added = 0
    missing = 0
    failed = 0
    with sqlite3.connect(output) as write:
        write.execute('PRAGMA busy_timeout=30000')
        for doc_id, muni, url, raw_path in candidates:
            p = find_raw(raw_path, source)
            if p is None:
                missing += 1
                print('MISSING RAW:', url, '| raw_path=', raw_path, flush=True)
                continue
            try:
                pages = extract_pages(p)
                if not any(pages):
                    print('NO TEXT:', url)
                    continue
                with write:
                    n = insert_segments(write, doc_id, muni, url, pages)
                if n:
                    recovered += 1
                    added += n
                print(f'RECOVERED: pages_added={n}/{len(pages)} | {url}', flush=True)
            except Exception as exc:
                failed += 1
                print('ERROR:', url, '|', type(exc).__name__, str(exc)[:200], flush=True)
    print('COPY:', output)
    print(f'RESULT: recovered_documents={recovered} inserted_segments={added} missing_raw={missing} errors={failed}')
    if not added:
        print('WARNING: No new segments. Do not rebuild the snapshot from this copy.')
        return 2
    print('NEXT: run crawler.scripts.chunk_documents on COPY, then snapshot and rebuild FTS.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
