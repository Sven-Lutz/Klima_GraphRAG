"""Build a validated semantic+lexical PDF-V2 retrieval snapshot, preserving originals.

Run from the Klima_GraphRAG repository root:
  python bootstrap_pdfv2_hybrid.py                 # read-only plan
  python bootstrap_pdfv2_hybrid.py --build --batch 32

The script reuses verified old MiniLM embeddings by exact SHA-256 content hash,
embeds the remaining chunks in resumable batches, exports a new manifested
snapshot, then builds a matching FTS5 index. It neither edits existing source
snapshots nor changes the answer generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

SOURCE = Path('crawler/data/db/RECOVERY_RAG_PDF_V2.sqlite')
OLD = Path('crawler/data/db/FINAL_V3_RAG_SNAPSHOT_20261012.sqlite')
WORK = Path('crawler/data/db/RECOVERY_RAG_PDF_V2_HYBRID_WORK.sqlite')
FROZEN = Path('crawler/data/db/RECOVERY_RAG_PDF_V2_HYBRID_SNAPSHOT.sqlite')
LEXICAL = Path('crawler/data/db/RECOVERY_RAG_PDF_V2_HYBRID_LEXICAL.sqlite')
SCRIPT_VERSION = 'pdfv2-hybrid-recovery-v4-2026-11-05'


def now():
    return datetime.now(timezone.utc).isoformat()


def ro(path):
    # The build database may need WAL/shm recovery after an interrupted process.
    # Only WORK may be opened read-write; input snapshots stay read-only.
    path = Path(path)
    mode = 'rw' if path.resolve() == WORK.resolve() else 'ro'
    return sqlite3.connect(path.resolve().as_uri() + '?mode=' + mode, uri=True)


def sha(s):
    return hashlib.sha256(str(s).encode('utf-8')).hexdigest()


def count(db, sql):
    return db.execute(sql).fetchone()[0]


def check_inputs():
    if not Path('crawler/core/retrieval.py').is_file():
        raise RuntimeError('Run this from the Klima_GraphRAG repository root')
    for path in (SOURCE, OLD):
        if not path.is_file():
            raise FileNotFoundError(path)
    with closing(ro(OLD)) as db:
        db.row_factory = sqlite3.Row
        artifact = db.execute('SELECT * FROM embedding_artifacts WHERE status=?', ('completed',)).fetchall()
        if len(artifact) != 1:
            raise RuntimeError('Expected exactly one completed old artifact')
        meta = dict(artifact[0])
        if meta['embedded_count'] != meta['planned_count'] or int(meta['vector_dimension']) != 384:
            raise RuntimeError('Old embedding artifact is incomplete or has unexpected dimensions')
        if count(db, 'SELECT COUNT(*) FROM chunk_embeddings') != int(meta['embedded_count']):
            raise RuntimeError('Old vector count disagrees with artifact metadata')
        if meta['model'] != 'paraphrase-multilingual-MiniLM-L12-v2' or \
                meta['preprocessing_id'] != 'canonical-chunks-v1:max-seq-length=256':
            raise RuntimeError('Unexpected old embedding model/preprocessing identity')
    return meta


def corpus_rows(path):
    from crawler.core.retrieval import chunk_corpus_rows, corpus_fingerprint
    with closing(ro(path)) as db:
        rows = chunk_corpus_rows(db)
    return rows, corpus_fingerprint(rows)


def matching_old_vectors(current_hashes, old_meta):
    """Return only old vectors that have a valid source/content/dimension binding.

    Some texts appear in multiple chunks; one embedding suffices per identical text.
    """
    mapping = {}
    sample_texts = {}
    with closing(ro(OLD)) as db:
        cur = db.execute('''SELECT c.text,c.chunk_hash,e.source_content_hash,
                                  e.vector_dimension,e.embedding
                            FROM chunk_embeddings e JOIN chunks c ON c.chunk_id=e.chunk_id
                            WHERE e.artifact_id=?''', (old_meta['artifact_id'],))
        for txt, stored_hash, src_hash, dimension, blob in cur:
            if src_hash not in current_hashes:
                continue
            if sha(txt) != src_hash or stored_hash != src_hash:
                raise RuntimeError('Hash/content disagreement in old vector source')
            if int(dimension) != 384 or len(blob) != 384 * 4:
                raise RuntimeError('Old vector has incorrect dimension or byte length')
            if src_hash not in mapping:
                mapping[src_hash] = bytes(blob)
                if len(sample_texts) < 12:
                    sample_texts[src_hash] = txt
    return mapping, sample_texts


def verify_model_against_old_vectors(model, mapping, sample_texts):
    """Guard against silently mixing an unversioned, changed model with old vectors."""
    import numpy as np
    keys = list(sample_texts)
    if len(keys) < 4:
        raise RuntimeError('Insufficient independent old-vector samples to verify model')
    fresh = model.encode([sample_texts[k] for k in keys],
                         batch_size=8, normalize_embeddings=True,
                         convert_to_numpy=True).astype(np.float32)
    if fresh.shape != (len(keys), 384):
        raise RuntimeError('New model returned wrong vector dimensions')
    similarities = []
    for k, new_vec in zip(keys, fresh):
        old_vec = np.frombuffer(mapping[k], dtype='<f4')
        if not np.isfinite(old_vec).all() or not np.isfinite(new_vec).all():
            raise RuntimeError('Non-finite embedding in model consistency test')
        similarity = float(np.dot(old_vec, new_vec) /
                           (np.linalg.norm(old_vec) * np.linalg.norm(new_vec)))
        similarities.append(similarity)
    worst = min(similarities)
    print(f'MODEL CONSISTENCY: {len(keys)} samples; worst cosine={worst:.7f}')
    if worst < 0.9999:
        raise RuntimeError('Old vectors do not match current model weights; refusing reuse')


def _validate_or_repair_temporary_copy(tmp, src_fp):
    """Accept a fully copied database even when an old script omitted its marker.

    Never trust a partial copy solely because SQLite can open it: verify its
    integrity and the complete canonical chunk corpus against the frozen input.
    """
    with closing(ro(tmp)) as chk:
        result = chk.execute('PRAGMA quick_check').fetchone()
        if not result or result[0] != 'ok':
            raise RuntimeError(f'SQLite quick_check failed: {result}')
        _rows, fingerprint = corpus_rows(tmp)
        if fingerprint != src_fp:
            raise RuntimeError('Temporary copy chunk corpus differs from original')
        tables = {row[0] for row in chk.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        marker = (chk.execute("SELECT value FROM hybrid_build_meta WHERE "
                              "key='original_corpus_fingerprint'").fetchone()
                  if 'hybrid_build_meta' in tables else None)
        if marker is not None and marker[0] != src_fp:
            raise RuntimeError('Temporary copy has an inconsistent origin marker')
    if marker is None:
        # The earlier version may have finished backup before recording metadata.
        # Only repair the COPY after a complete integrity/fingerprint match.
        with closing(sqlite3.connect(tmp)) as dst:
            dst.execute('CREATE TABLE IF NOT EXISTS hybrid_build_meta '
                        '(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            dst.execute('INSERT INTO hybrid_build_meta(key,value) VALUES (?,?)',
                        ('original_corpus_fingerprint', src_fp))
            dst.commit()
        print('Recovered copy was missing marker; verified and restored it',flush=True)


def _fresh_copy(tmp, src_fp):
    free = shutil.disk_usage(WORK.parent).free
    # Conservative floor for a second snapshot, FTS and new vectors.
    required = SOURCE.stat().st_size * 2 + 1_800_000_000
    if free < required:
        raise RuntimeError('Insufficient disk space for safe copy/snapshot/index; '
                           f'free={free:,}; estimated minimum={required:,}. '
                           'Keep the source database and ask before deleting files.')
    print('Creating NEW working database; original inputs remain read-only',flush=True)
    # Explicitly close BOTH connections before renaming. The SQLite connection
    # context manager alone does not close handles on Windows.
    with closing(ro(SOURCE)) as src, closing(sqlite3.connect(tmp)) as dst:
        src.backup(dst)
        dst.execute('CREATE TABLE IF NOT EXISTS hybrid_build_meta '
                    '(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        dst.execute('INSERT OR REPLACE INTO hybrid_build_meta VALUES(?,?)',
                    ('original_corpus_fingerprint', src_fp))
        dst.commit()
    _validate_or_repair_temporary_copy(tmp, src_fp)
    tmp.replace(WORK)


def ensure_working_copy(src_fp):
    if not WORK.exists():
        tmp = WORK.with_suffix('.sqlite.partial')
        if tmp.exists():
            print(f'Found leftover temporary database: {tmp}', flush=True)
            try:
                _validate_or_repair_temporary_copy(tmp, src_fp)
            except (sqlite3.Error, RuntimeError, OSError) as exc:
                # Preserve the questionable file; never silently delete data.
                # Move it out of the working filename, then make a verified copy.
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                quarantine = tmp.with_name(tmp.name + f'.unverified-{stamp}')
                if quarantine.exists():
                    raise RuntimeError(f'Quarantine name already exists: {quarantine}')
                try:
                    tmp.replace(quarantine)
                except OSError as err:
                    raise RuntimeError(
                        f'Cannot rename old .partial copy (another program may hold it): {err}'
                    ) from err
                print(f'Old .partial could not be verified: {exc}',flush=True)
                print(f'Preserved unverified file as: {quarantine}',flush=True)
                _fresh_copy(tmp, src_fp)
            else:
                try:
                    tmp.replace(WORK)
                except OSError as exc:
                    raise RuntimeError(
                        f'Validated .partial but Windows prevented rename: {exc}. '
                        'Close database viewers and other Python processes.'
                    ) from exc
                print('Recovered verified temporary database',flush=True)
        else:
            _fresh_copy(tmp, src_fp)
    rows, fp = corpus_rows(WORK)
    if fp != src_fp:
        raise RuntimeError('Working database corpus differs from source; refusing to continue')
    with closing(ro(WORK)) as db:
        meta = db.execute('SELECT value FROM hybrid_build_meta WHERE '
                          "key='original_corpus_fingerprint'").fetchone()
        if meta is None or meta[0] != src_fp:
            raise RuntimeError('Working database has no valid source fingerprint')
    return rows, fp


def build_artifact(rows, fingerprint, old_meta, vectors, samples, batch_size):
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from crawler.core.database import resolve_runtime_database
    from crawler.core.retrieval import (EmbeddingIdentity, embedding_artifact_id,
                                        ensure_embedding_schema, vector_to_blob)
    if batch_size < 1 or batch_size > 256:
        raise RuntimeError('Use a batch size from 1 to 256')
    identity = EmbeddingIdentity(
        provider=old_meta['provider'], model=old_meta['model'],
        model_revision=old_meta['model_revision'],
        vector_dimension=int(old_meta['vector_dimension']),
        preprocessing_id=old_meta['preprocessing_id'])
    source_db_id = resolve_runtime_database(str(WORK)).database_id
    artifact = embedding_artifact_id(identity, source_database_id=source_db_id,
                                     corpus_identity=fingerprint)
    print(f'NEW ARTIFACT: {artifact}')
    model = SentenceTransformer(identity.model, device='cpu')
    model.max_seq_length = 256
    if int(model.get_sentence_embedding_dimension()) != identity.vector_dimension:
        raise RuntimeError('Unexpected embedding dimension')
    verify_model_against_old_vectors(model, vectors, samples)
    with closing(sqlite3.connect(WORK)) as db:
        ensure_embedding_schema(db)
        db.commit()
        meta = db.execute('SELECT status,planned_count FROM embedding_artifacts WHERE artifact_id=?',
                          (artifact,)).fetchone()
        if meta is None:
            db.execute('''INSERT INTO embedding_artifacts
                (artifact_id,schema_version,provider,model,model_revision,vector_dimension,
                 preprocessing_id,source_database_id,corpus_fingerprint,planned_count,
                 embedded_count,status,created_at)
                VALUES (?,1,?,?,?,?,?,?,?,?,0,'running',?)''',
                (artifact,identity.provider,identity.model,identity.model_revision,
                 identity.vector_dimension,identity.preprocessing_id,source_db_id,
                 fingerprint,len(rows),now()))
            db.commit()
        elif int(meta[1]) != len(rows):
            raise RuntimeError('Existing artifact has incompatible corpus size')
        # Existing records are checked on every resume. A run is only 'completed'
        # when all canonical chunk IDs, hashes and vector lengths match.
        existing = {}
        for chunk_id, content_hash, dimension, vector_bytes in db.execute(
            'SELECT chunk_id,source_content_hash,vector_dimension,length(embedding) '
            'FROM chunk_embeddings WHERE artifact_id=?', (artifact,)):
            if dimension != 384 or vector_bytes != 1536:
                raise RuntimeError('Incomplete/invalid persisted vector record')
            existing[str(chunk_id)] = str(content_hash)
        row_hashes = {chunk_id: h for chunk_id, h, _ in rows}
        if any(chunk_id not in row_hashes or row_hashes[chunk_id] != h
               for chunk_id,h in existing.items()):
            raise RuntimeError('Resumed artifact has inconsistent source hashes')
        if len(existing) == len(rows):
            print('All vectors present; performing completion check')
        elif meta is not None and meta[0] == 'completed':
            raise RuntimeError('Marked completed but missing vectors; refusing to proceed')
        db.execute("UPDATE embedding_artifacts SET status='running',embedded_count=? WHERE artifact_id=?",
                   (len(existing),artifact))
        db.commit()
        # Persist reused vectors in deterministic batches; never edit original V3.
        reuse_batch=[]
        reused=0
        for chunk_id,h,_txt in rows:
            if chunk_id in existing or h not in vectors:
                continue
            reuse_batch.append((artifact,chunk_id,h,384,vectors[h],now()))
            if len(reuse_batch) >= 1000:
                db.executemany('INSERT INTO chunk_embeddings VALUES (?,?,?,?,?,?)',reuse_batch)
                db.execute('UPDATE embedding_artifacts SET embedded_count=embedded_count+? WHERE artifact_id=?',
                           (len(reuse_batch),artifact))
                db.commit()
                reused += len(reuse_batch)
                reuse_batch.clear()
        if reuse_batch:
            db.executemany('INSERT INTO chunk_embeddings VALUES (?,?,?,?,?,?)',reuse_batch)
            db.execute('UPDATE embedding_artifacts SET embedded_count=embedded_count+? WHERE artifact_id=?',
                       (len(reuse_batch),artifact))
            db.commit(); reused += len(reuse_batch)
        print(f'REUSED THIS RUN: {reused:,}',flush=True)
        already = set(existing)
        # Embed each distinct new text once, then link the result to all matching
        # canonical chunk IDs. A batch is atomic, so resuming never loses a group.
        groups = {}
        for chunk_id,h,txt in rows:
            if chunk_id in already or h in vectors:
                continue
            if h not in groups:
                groups[h] = [txt, []]
            groups[h][1].append(chunk_id)
        keys = list(groups)
        pending_count = sum(len(v[1]) for v in groups.values())
        print(f'TO EMBED THIS RUN: {pending_count:,} chunks / {len(keys):,} unique texts',
              flush=True)
        finished=0
        for start in range(0,len(keys),batch_size):
            batch_keys=keys[start:start+batch_size]
            a=model.encode([groups[k][0] for k in batch_keys],batch_size=batch_size,
                           normalize_embeddings=True,convert_to_numpy=True).astype(np.float32)
            if a.shape != (len(batch_keys),384) or not np.all(np.isfinite(a)) or \
                    np.any(np.linalg.norm(a,axis=1)==0):
                raise RuntimeError('Generated embeddings failed numeric validation')
            stamp=now()
            payload=[]
            for i,h in enumerate(batch_keys):
                blob=vector_to_blob(a[i])
                for chunk_id in groups[h][1]:
                    payload.append((artifact,chunk_id,h,384,blob,stamp))
            db.executemany('INSERT INTO chunk_embeddings VALUES (?,?,?,?,?,?)',payload)
            db.execute('UPDATE embedding_artifacts SET embedded_count=embedded_count+? WHERE artifact_id=?',
                       (len(payload),artifact))
            db.commit()
            finished+=len(payload)
            if (start // batch_size) % 50 == 0 or start+len(batch_keys)==len(keys):
                print(f'EMBEDDED NEW: {finished:,}/{pending_count:,}',flush=True)
        # Recompute authoritative membership and lengths; do not trust count only.
        actual=list(db.execute('SELECT chunk_id,source_content_hash,vector_dimension,length(embedding) '
                               'FROM chunk_embeddings WHERE artifact_id=? ORDER BY chunk_id',(artifact,)))
        expect=[(chunk_id,h,384,1536) for chunk_id,h,_ in rows]
        if actual != expect:
            raise RuntimeError('Final artifact membership/hash/dimension verification failed')
        db.execute("UPDATE embedding_artifacts SET embedded_count=?,status='completed',completed_at=? "
                   'WHERE artifact_id=?',(len(rows),now(),artifact))
        db.commit()
    print(f'VALIDATED EMBEDDING ARTIFACT: {len(rows):,} vectors')
    return artifact


def export_and_index(expected_fingerprint, artifact):
    from crawler.core.snapshots import export_database_snapshot, validate_snapshot_manifest
    from crawler.core.retrieval import corpus_fingerprint, chunk_corpus_rows, _select_embedding_artifact
    from crawler.core.retrieval import EmbeddingIdentity
    if FROZEN.exists() and not FROZEN.with_suffix(FROZEN.suffix+'.manifest.json').exists():
        raise RuntimeError('Frozen output exists without default manifest; inspect manually')
    if not FROZEN.exists():
        print('Exporting NEW frozen snapshot and manifest',flush=True)
        binding=export_database_snapshot(WORK,FROZEN)
    else:
        print('Using existing frozen snapshot for validation',flush=True)
        binding=validate_snapshot_manifest(FROZEN.with_suffix(FROZEN.suffix+'.manifest.json'))
    with closing(ro(binding.database.path)) as frozen_db:
        frozen_fingerprint = corpus_fingerprint(chunk_corpus_rows(frozen_db))
    if frozen_fingerprint != expected_fingerprint:
        raise RuntimeError('Frozen snapshot does not match expected source chunks')
    with closing(ro(binding.database.path)) as db:
        row=db.execute('SELECT provider,model,model_revision,vector_dimension,preprocessing_id '
                       'FROM embedding_artifacts WHERE artifact_id=?',(artifact,)).fetchone()
        if row is None:
            raise RuntimeError('Frozen snapshot lacks generated embedding artifact')
        identity=EmbeddingIdentity(*row)
        selected,_rows=_select_embedding_artifact(db,binding,identity)
        if selected!=artifact:
            raise RuntimeError('Manifest/artifact identity mismatch')
    manifest_id=str(binding.manifest.get('manifest_id'))
    if LEXICAL.exists():
        with closing(ro(LEXICAL)) as db:
            lex_meta=dict(db.execute('SELECT key,value FROM metadata').fetchall())
            if lex_meta.get('source_snapshot_id') != manifest_id or \
               count(db,'SELECT COUNT(*) FROM chunks_fts') != len(_rows):
                raise RuntimeError('Existing lexical index mismatches new snapshot')
    else:
        partial=LEXICAL.with_suffix('.sqlite.partial')
        if partial.exists():
            raise RuntimeError(f'Leftover partial lexical index: {partial}')
        print('Building fresh snapshot-bound FTS5 index',flush=True)
        try:
            with closing(sqlite3.connect(partial)) as target, closing(ro(binding.database.path)) as source:
                target.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                target.executemany('INSERT INTO metadata(key,value) VALUES(?,?)',
                                   [('index_type','sqlite-fts5-unicode61'),
                                    ('source_snapshot_id',manifest_id)])
                target.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5("
                               "chunk_id UNINDEXED,municipality_id UNINDEXED,text,"
                               "tokenize='unicode61 remove_diacritics 2')")
                cur=source.execute('SELECT chunk_id,municipality_id,text FROM chunks ORDER BY chunk_id')
                inserted=0
                while True:
                    batch=cur.fetchmany(2000)
                    if not batch:break
                    target.executemany('INSERT INTO chunks_fts(chunk_id,municipality_id,text) '
                                       'VALUES (?,?,?)',batch)
                    inserted+=len(batch)
                if inserted!=len(_rows):
                    raise RuntimeError('Lexical row count differs from canonical corpus')
                target.commit()
            partial.replace(LEXICAL)
        except Exception:
            # An interrupted build may leave a temporary FTS file; no original
            # corpus/index is overwritten.
            raise
    print('\nSUCCESS: complete canonical semantic+lexical retrieval inputs')
    print('SNAPSHOT:',binding.database.path)
    print('MANIFEST:',binding.manifest_path)
    print('LEXICAL:',LEXICAL.resolve())
    print('ARTIFACT:',artifact)
    print('STOP HERE: do not rebuild embeddings; proceed to a retrieval smoke test.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true',help='Create a new resumable working copy and indexes')
    parser.add_argument('--batch',type=int,default=32,help='CPU embedding batch size')
    args=parser.parse_args()
    print('SCRIPT VERSION:', SCRIPT_VERSION, flush=True)
    old_meta=check_inputs()
    rows,src_fp=corpus_rows(SOURCE)
    hashes={h for _,h,_ in rows}
    print('SOURCE CHUNKS:',len(rows))
    print('OLD MODEL:',old_meta['model'])
    print('PRESERVED INPUTS:',SOURCE,OLD)
    if not args.build:
        print('PLAN ONLY: use --build to create a separate full hybrid snapshot; no file changed')
        return
    lock=WORK.with_suffix('.build.lock')
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        with os.fdopen(fd,'w') as f:f.write(now())
    except FileExistsError:
        raise RuntimeError(f'Build lock exists: {lock}. Check for active process before removing it.')
    try:
        work_rows,fp=ensure_working_copy(src_fp)
        vectors,samples=matching_old_vectors(hashes,old_meta)
        print('OLD DISTINCT REUSABLE HASHES:',len(vectors))
        artifact=build_artifact(work_rows,fp,old_meta,vectors,samples,args.batch)
        export_and_index(fp,artifact)
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        raise
