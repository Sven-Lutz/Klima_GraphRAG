"""Read-only, provenance-verified adapter from klima_evidence_v4 JSON to canonical retrieval.

The V4 JSON is an untrusted *shortlist*, not a trusted replacement for source text.
Each returned indexed chunk is reloaded from the existing PDF-V2 SQLite snapshot.
This module does not write to the source DB or reconstruct graph assertions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from crawler.core.provenance import evidence_id_for
from crawler.core.retrieval import (
    DocumentSource, RetrievedEvidence, RetrievalResult, RetrievalTrace,
)


def _norm(s: str) -> str:
    return ' '.join(s.split()).casefold()


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _city_host(url: str, name: str) -> bool:
    city = name.casefold().translate(str.maketrans({'ä':'ae','ö':'oe','ü':'ue','ß':'ss'})).replace(' ', '-')
    hostname = (urlsplit(url).hostname or '').lower()
    return bool(city and (hostname == city + '.de' or hostname.endswith('.' + city + '.de')))


class V4EvidenceRetriever:
    """Plug into CanonicalAnswerGenerator as its retriever for one saved V4 run."""

    def __init__(self, json_path: Path, root: Path, *, municipality_name: str):
        self.root = root.resolve()
        self.json_path = json_path.resolve()
        if not _within_root(self.json_path, self.root):
            raise ValueError('Evidence JSON must be inside the project directory')
        raw = self.json_path.read_bytes()
        self.payload = json.loads(raw)
        if not isinstance(self.payload, dict):
            raise ValueError('V4 evidence JSON must be an object')
        self.snapshot_id = 'verified-v4-shortlist-sha256:' + hashlib.sha256(raw).hexdigest()
        self.question = self.payload.get('query')
        self.municipality_id = self.payload.get('municipality_id')
        if (not isinstance(self.question, str) or not isinstance(self.municipality_id, str)
                or not re.fullmatch(r'\d{8}', self.municipality_id)):
            raise ValueError('V4 evidence missing original query or municipality ID')
        if not isinstance(self.payload.get('results'), list):
            raise ValueError('V4 evidence is missing results')
        self.municipality_name = municipality_name
        source = Path(self.payload.get('source_database', ''))
        if not str(source) or str(source) == '.':
            raise ValueError('V4 evidence missing source_database')
        self.source_db = (self.root / source).resolve()
        if not _within_root(self.source_db, self.root) or not self.source_db.is_file():
            raise ValueError('Original V4 source database cannot be located under project root')

    def _source_chunk(self, db: sqlite3.Connection, row: dict, *, rank: int, is_date=False):
        chunk_id = row.get('chunk_id')
        doc_id = row.get('document_id')
        url = row.get('url')
        if not all(isinstance(value, str) and value for value in (chunk_id, doc_id, url)):
            raise ValueError('V4 shortlist contains an invalid chunk, document, or URL')
        verified = db.execute(
            'SELECT c.chunk_id, c.segment_id, c.document_id, c.text, '
            'c.heading_context, c.chunk_hash, d.url_canonical '
            'FROM chunks c JOIN documents_raw d ON d.document_id=c.document_id '
            'WHERE c.chunk_id=?', (chunk_id,),
        ).fetchone()
        if verified is None:
            raise ValueError(f'Chunk no longer exists in source DB: {chunk_id}')
        cid, sid, did, text, heading, stored_hash, stored_url = verified
        if not sid or did != doc_id or stored_url != url:
            raise ValueError(f'Chunk provenance mismatch: {chunk_id}')
        # JSON may hold a shortened excerpt, but it must appear in the original.
        supplied = row.get('text', '')
        if supplied and (not isinstance(supplied, str) or _norm(supplied) not in _norm(text)):
            raise ValueError(f'Saved excerpt not found in original chunk: {chunk_id}')
        actual_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if stored_hash and stored_hash != actual_hash:
            raise ValueError(f'Source DB stored hash disagrees with chunk text: {chunk_id}')
        municipal_rows = db.execute(
            'SELECT municipality_id FROM municipality_documents WHERE document_id=?', (did,)
        ).fetchall()
        municipalities = {str(x[0]) for x in municipal_rows}
        raw_row = db.execute(
            'SELECT municipality_id FROM documents_raw WHERE document_id=?', (did,)
        ).fetchone()
        if raw_row and raw_row[0] is not None:
            municipalities.add(str(raw_row[0]))
        if self.municipality_id not in municipalities:
            raise ValueError(f'Chunk has wrong municipality: {chunk_id}')
        # Named source provenance must be an HTTP(S) URL; utilities are valid
        # evidence but not proof of a city-issued grant.
        if urlsplit(stored_url).scheme not in ('https', 'http') or not urlsplit(stored_url).netloc:
            raise ValueError(f'Invalid source URL for chunk {chunk_id}')
        score = float(row.get('final_rank_score', row.get('retrieval_score', 0)))
        return RetrievedEvidence(
            database_snapshot_id=self.snapshot_id,
            evidence_id=evidence_id_for('chunk', cid, actual_hash),
            source_type='chunk', source_id=cid, segment_id=sid, chunk_id=cid,
            document=DocumentSource(did, stored_url, heading, tuple(sorted(municipalities))),
            evidence_text=text, extraction_run_ids=(), graph_artifact_ids=(), assertions=(),
            retrieval_traces=(RetrievalTrace('v4_document_date' if is_date else 'v4_verified_shortlist', rank, score),),
            retrieval_score=score, rank=rank,
        )

    def _find_date_chunk(self, db: sqlite3.Connection, doc_id: str, url: str, seen_ids: set[str]):
        """Find an explicitly enacted rule date IN the same official policy document."""
        if not _city_host(url, self.municipality_name):
            return None
        if not re.search(r'(richtlinie|satzung|foerder|f%C3%B6rder)', url, re.I):
            return None
        # Only original SQLite rows qualify. Never trust an unverified date in JSON.
        rows = db.execute(
            "SELECT c.chunk_id, c.document_id, c.text, d.url_canonical "
            "FROM chunks c JOIN documents_raw d ON d.document_id=c.document_id "
            "WHERE c.document_id=? AND (lower(c.text) LIKE '%tritt%' "
            "OR lower(c.text) LIKE '%gilt ab%') LIMIT 1200", (doc_id,),
        ).fetchall()
        from crawler.core.answer_generation import _policy_document_rule_effective_date
        candidates = []
        distinct_dates = set()
        for cid, did, text, actual_url in rows:
            if did != doc_id or actual_url != url:
                continue
            date_value = _policy_document_rule_effective_date(text)
            if date_value:
                distinct_dates.add(date_value)
                if cid not in seen_ids:
                    candidates.append(cid)
        if len(distinct_dates) != 1 or not candidates:
            return None
        target = candidates[0]
        return {'chunk_id': target, 'document_id': doc_id, 'url': url, 'text': ''}

    def retrieve(self, request):
        if request.query != self.question or request.municipality_ids != (self.municipality_id,):
            raise ValueError('Query or municipality differs from saved V4 retrieval')
        uri = self.source_db.as_uri() + '?mode=ro'
        db = sqlite3.connect(uri, uri=True)
        try:
            selected = []
            seen_ids = set()
            source_docs = []
            skipped_nonindexed = 0
            for row in self.payload['results']:
                if not isinstance(row, dict):
                    raise ValueError('Invalid V4 result row')
                if row.get('municipality_id') != self.municipality_id:
                    raise ValueError('V4 result municipality mismatch')
                if row.get('source') not in ('indexed_chunk', 'indexed_document_context'):
                    # Repaired PDF fragments can only enter the canonical model
                    # after a separately authenticated source-to-page binding.
                    skipped_nonindexed += 1
                    continue
                cid = row.get('chunk_id')
                if cid in seen_ids:
                    continue
                item = self._source_chunk(db, row, rank=len(selected) + 1)
                selected.append(item)
                seen_ids.add(cid)
                if row.get('source_topic_match') and not row.get('source_off_topic'):
                    source_docs.append((item.document.document_id, item.document.source_url))
            # Add at most one validated date anchor per SAME municipal guideline.
            anchors = []
            anchor_docs = set()
            for doc_id, url in source_docs:
                if doc_id in anchor_docs:
                    continue
                anchor_docs.add(doc_id)
                possible = self._find_date_chunk(db, doc_id, url, seen_ids)
                if possible:
                    anchor = self._source_chunk(db, possible, rank=1, is_date=True)
                    anchors.append(anchor)
                    seen_ids.add(possible['chunk_id'])
            # Put the date evidence near its guideline so that evidence budgets
            # do not silently discard the date anchor.
            joined = []
            anchor_by_doc = {a.document.document_id: a for a in anchors}
            for item in selected:
                joined.append(item)
                if item.document.document_id in anchor_by_doc:
                    joined.append(anchor_by_doc.pop(item.document.document_id))
            joined = joined[:request.top_k]
            from dataclasses import replace
            final = tuple(replace(item, rank=i, retrieval_traces=(
                RetrievalTrace(item.retrieval_traces[0].method, i, item.retrieval_score),
            )) for i, item in enumerate(joined, 1))
            return RetrievalResult(
                query=request.query,
                mode=request.mode.value,
                database_snapshot_id=self.snapshot_id,
                projection_id=request.projection_id,
                items=final,
                embedding_artifact_id=None,
                warnings=tuple(
                    ['V4 shortlisted, source-DB-verified chunks; no live corpus search']
                    + ([f'{skipped_nonindexed} PDF recovery fragments omitted: not bound to canonical chunk IDs']
                       if skipped_nonindexed else [])
                ),
            )
        finally:
            db.close()
