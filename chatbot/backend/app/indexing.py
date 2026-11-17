"""Offline, resumable embedding build for the application snapshot only."""
import hashlib
import json
import os
import sqlite3
import struct
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .embeddings import encoder_spec, normalized
from .knowledge import Knowledge
from .retrieval import input_hash, VectorIndex


async def build_index(settings, embeddings, progress=lambda value: None):
    directory = settings.knowledge_dir
    manifest_path = directory / 'manifest.json'
    pending_manifest = directory / 'manifest.json.next'
    # Recover a publication interrupted between the two atomic file renames.
    if pending_manifest.exists():
        pending = json.loads(pending_manifest.read_text())
        if hashlib.sha256((directory / 'story.db').read_bytes()).hexdigest() == pending['database_sha256']:
            os.replace(pending_manifest, manifest_path)
    knowledge = Knowledge(directory)
    spec = encoder_spec(settings)
    with knowledge.db() as db:
        rows = db.execute("""SELECT c.* FROM chunks c JOIN entries e ON e.unit_id=c.unit_id
            WHERE c.review_status!='rejected' AND e.review_status!='rejected' ORDER BY c.chunk_id""").fetchall()
        previous = db.execute("SELECT value FROM meta WHERE key='embedding_index'").fetchone()
        old_meta = json.loads(previous[0]) if previous else {}
        old_vectors = {r['chunk_id']: r for r in db.execute('SELECT * FROM chunk_vectors')}
    inputs = {r['chunk_id']: input_hash(r) for r in rows}
    if any(r['content_hash'] != 'sha256:' + inputs[r['chunk_id']][:16] for r in rows):
        raise ValueError('Chunk content hash does not match source text; rebuild the knowledge snapshot first')
    reusable, pending_rows = {}, []
    for row in rows:
        old = old_vectors.get(row['chunk_id'])
        if (old_meta.get('encoder') == spec and old_meta.get('inputs', {}).get(row['chunk_id']) == inputs[row['chunk_id']]
            and old and old['content_hash'] == row['content_hash'] and old['model'] == spec['model'] and old['dim'] == spec['dim']):
            try:
                vector = struct.unpack('<' + 'f' * spec['dim'], old['vec'])
                normalized(vector, spec['dim'])
                if abs(sum(v * v for v in vector) - 1) > 1e-4:
                    raise ValueError('invalid norm')
                reusable[row['chunk_id']] = old['vec']
                continue
            except (ValueError, TypeError, struct.error):
                pass
        pending_rows.append(row)
    calls = []
    report = {'model': spec['model'], 'dimensions': spec['dim'], 'chunks': len(rows),
              'reused': len(reusable), 'generated': len(pending_rows), 'calls': calls}
    if not pending_rows and old_meta.get('inputs') == inputs:
        progress(report)
        return report
    # Checkpoint successful batches outside the published knowledge DB. A failed
    # request cannot damage the working index, and retries reuse completed work.
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    cache_path = settings.state_dir / 'embedding-build.sqlite'
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    with closing(sqlite3.connect(cache_path)) as cache:
        cache.execute('CREATE TABLE IF NOT EXISTS cache (encoder TEXT, input_hash TEXT, vec BLOB, PRIMARY KEY(encoder,input_hash))')
        still_pending = []
        for row in pending_rows:
            found = cache.execute('SELECT vec FROM cache WHERE encoder=? AND input_hash=?', (fingerprint, inputs[row['chunk_id']])).fetchone()
            try:
                if not found:
                    raise ValueError('missing')
                vector = struct.unpack('<' + 'f' * spec['dim'], found[0])
                normalized(vector, spec['dim'])
                if abs(sum(v * v for v in vector) - 1) > 1e-4:
                    raise ValueError('invalid norm')
                reusable[row['chunk_id']] = found[0]
            except (ValueError, TypeError, struct.error):
                still_pending.append(row)
        report['reused'], report['generated'] = len(reusable), len(still_pending)
        for start in range(0, len(still_pending), 20):
            batch = still_pending[start:start + 20]
            vectors, usage = await embeddings.embed([r['context_header'] + '\n' + r['text'] for r in batch], 'document')
            if len(vectors) != len(batch):
                raise ValueError('Incomplete embedding batch')
            for row, vector in zip(batch, vectors):
                blob = struct.pack('<' + 'f' * spec['dim'], *normalized(vector, spec['dim']))
                reusable[row['chunk_id']] = blob
                cache.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (fingerprint, inputs[row['chunk_id']], blob))
            cache.commit()
            calls.append(usage)
            progress({'indexed': len(reusable), 'total': len(rows), 'batch_tokens': usage.get('usage', {}).get('total_tokens')})
    meta = {'encoder': spec, 'inputs': inputs, 'built_at': datetime.now(timezone.utc).isoformat()}
    fd, name = tempfile.mkstemp(prefix='.embedding-', suffix='.db', dir=directory)
    os.close(fd)
    staged = Path(name)
    try:
        with closing(sqlite3.connect(staged)) as target, knowledge.db() as source:
            source.backup(target)
            target.execute('PRAGMA foreign_keys=ON')
            target.execute('DELETE FROM chunk_vectors')
            target.executemany('INSERT INTO chunk_vectors(chunk_id,model,dim,content_hash,vec) VALUES(?,?,?,?,?)',
                [(r['chunk_id'], spec['model'], spec['dim'], r['content_hash'], reusable[r['chunk_id']]) for r in rows])
            target.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('embedding_index', json.dumps(meta, ensure_ascii=False)))
            target.commit()
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Vector database validation failed')
        manifest = knowledge.manifest | {'database_sha256': hashlib.sha256(staged.read_bytes()).hexdigest(),
            'embedding': {'model': spec['model'], 'dimensions': spec['dim'], 'vectors': len(rows), 'built_at': meta['built_at']}}
        pending_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        os.chmod(staged, 0o644)
        os.replace(staged, knowledge.path)
        os.replace(pending_manifest, manifest_path)
        if VectorIndex(Knowledge(directory), settings).reason:
            raise ValueError('Published vector index did not validate')
    finally:
        staged.unlink(missing_ok=True)
    return report
