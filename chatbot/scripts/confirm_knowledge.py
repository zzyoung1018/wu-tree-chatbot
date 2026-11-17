"""Record the user's confirmation in the application snapshot. Stop the API first."""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.confirmation import confirm_metadata

directory = ROOT / 'data/knowledge'
backup = ROOT / 'state/knowledge-before-confirmation'
backup.mkdir(parents=True, exist_ok=True)
manifest_path = directory / 'manifest.json'
manifest = json.loads(manifest_path.read_text())
with sqlite3.connect(directory / 'story.db') as db:
    if not (backup / 'story.db').exists():
        with sqlite3.connect(backup / 'story.db') as target:
            db.backup(target)
        shutil.copyfile(manifest_path, backup / 'manifest.json')
    body_sql = 'SELECT chunk_id,context_header,text,content_hash FROM chunks ORDER BY chunk_id'
    vector_sql = 'SELECT chunk_id,model,dim,content_hash,vec FROM chunk_vectors ORDER BY chunk_id'
    bodies, vectors = db.execute(body_sql).fetchall(), db.execute(vector_sql).fetchall()
    confirm_metadata(db)
    assert db.execute(body_sql).fetchall() == bodies
    assert db.execute(vector_sql).fetchall() == vectors
    db.commit()
manifest.pop('review_mode', None)
manifest['knowledge_status'] = 'confirmed'
manifest['database_sha256'] = hashlib.sha256((directory / 'story.db').read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(f'Confirmed metadata; preserved {len(bodies)} source chunks and {len(vectors)} vectors unchanged.')
