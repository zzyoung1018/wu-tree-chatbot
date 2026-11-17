"""Publish a confirmed snapshot, reusing unchanged vectors. Stop the API first."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.confirmation import confirm_metadata
from app.retrieval import input_hash


def preserved_vectors(output):
    path, manifest_path = output / 'story.db', output / 'manifest.json'
    if not path.exists() or not manifest_path.exists():
        return {}, {}, {}
    manifest = json.loads(manifest_path.read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['database_sha256']:
        raise ValueError('Existing snapshot hash mismatch; no files replaced')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        meta = db.execute("SELECT value FROM meta WHERE key='embedding_index'").fetchone()
        if not meta:
            return {}, {}, {}
        return json.loads(meta[0]), {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM chunk_vectors')}, manifest.get('embedding', {})


def export_snapshot(source_dir, output):
    source_path = (source_dir / 'story.db').resolve()
    if source_path == (output / 'story.db').resolve():
        raise ValueError('Source and application snapshot must be separate')
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.embedding-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Same two-file publication recovery protocol as build_embeddings.py.
        next_manifest = output / 'manifest.json.next'
        if next_manifest.exists() and (output / 'story.db').exists():
            pending = json.loads(next_manifest.read_text())
            if pending['database_sha256'] == hashlib.sha256((output / 'story.db').read_bytes()).hexdigest():
                os.replace(next_manifest, output / 'manifest.json')
        old_meta, old_vectors, old_embedding = preserved_vectors(output)
        spec = old_meta.get('encoder', {})
        with tempfile.TemporaryDirectory(prefix='.export-', dir=output) as staging:
            staged = Path(staging) / 'story.db'
            with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(staged)) as target:
                    source.backup(target)
                    target.row_factory = sqlite3.Row
                    target.execute('PRAGMA foreign_keys=ON')
                    confirm_metadata(target)
                    target.execute('DELETE FROM chunk_vectors')
                    target.execute("DELETE FROM meta WHERE key='embedding_index'")
                    rows = target.execute("""SELECT c.* FROM chunks c JOIN entries e ON e.unit_id=c.unit_id
                        WHERE c.review_status!='rejected' AND e.review_status!='rejected'""").fetchall()
                    if not rows:
                        raise ValueError('Empty snapshot')
                    reused_inputs = {}
                    for row in rows:
                        digest = input_hash(row)
                        if row['content_hash'] != 'sha256:' + digest[:16]:
                            raise ValueError('Source chunk hash mismatch')
                        old = old_vectors.get(row['chunk_id'])
                        if (not old or spec.get('version') != 1
                            or old_meta.get('inputs', {}).get(row['chunk_id']) != digest
                            or old['content_hash'] != row['content_hash']
                            or old['model'] != spec.get('model') or old['dim'] != spec.get('dim')):
                            continue
                        try:
                            vector = struct.unpack('<' + 'f' * old['dim'], old['vec'])
                            if any(not math.isfinite(v) for v in vector) or abs(sum(v*v for v in vector) - 1) > 1e-4:
                                continue
                        except (TypeError, struct.error):
                            continue
                        target.execute('INSERT INTO chunk_vectors VALUES(?,?,?,?,?)',
                                       tuple(old[k] for k in ('chunk_id', 'model', 'dim', 'content_hash', 'vec')))
                        reused_inputs[row['chunk_id']] = digest
                    if reused_inputs:
                        target.execute('INSERT INTO meta VALUES(?,?)', ('embedding_index', json.dumps(
                            old_meta | {'inputs': reused_inputs}, ensure_ascii=False)))
                    counts = {table: target.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                              for table in ('entries', 'items', 'chunks', 'images')}
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchall():
                        raise ValueError('Invalid snapshot')
                    target.commit()
            shutil.copytree(source_dir / 'images/story', output / 'images', dirs_exist_ok=True)
            hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted((source_dir / 'images/story').glob('*.jpg'))}
            manifest = {'source': '《树碑立传》树的故事', 'source_id': 'SRC-SHBL', 'as_of': '2024-08',
                        'pipeline': 'pilot_stories@3+index_story@1+build_story_chunks@2.1',
                        'database_sha256': hashlib.sha256(staged.read_bytes()).hexdigest(),
                        'counts': counts, 'image_sha256': hashes, 'knowledge_status': 'confirmed'}
            if reused_inputs:
                manifest['embedding'] = old_embedding | {'model': spec['model'], 'dimensions': spec['dim'],
                    'vectors': len(reused_inputs), 'built_at': old_meta.get('built_at')}
            next_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            os.chmod(staged, 0o644)
            os.replace(staged, output / 'story.db')
            os.replace(next_manifest, output / 'manifest.json')
        report = {'counts': counts, 'vectors_reused': len(reused_inputs), 'vectors_pending': len(rows) - len(reused_inputs)}
        print(json.dumps(report, ensure_ascii=False))
        if report['vectors_pending']:
            print('Run scripts/build_embeddings.py to build only missing/changed vectors before starting the API.')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT.parent / 'analysis/pilot')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/knowledge')
    args = parser.parse_args()
    export_snapshot(args.source, args.output)
