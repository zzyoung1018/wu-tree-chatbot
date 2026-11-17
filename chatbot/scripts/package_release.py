"""Package an allowlisted deployment bundle. Secrets and conversation state are excluded."""
import hashlib
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / 'release'
destination.mkdir(exist_ok=True)
archive = destination / 'wu-tree-chatbot.tar.gz'
include = ['backend', 'frontend', 'data/knowledge', 'deploy', 'scripts', 'README.md', 'compose.yaml', 'pytest.ini', '.env.example', '.env.server.example', '.dockerignore', '.gitignore']
skip = {'node_modules', '__pycache__', '.pytest_cache', 'dist', '.venv', 'state', 'release', 'test-results'}
files = []
for name in include:
    source = root / name
    files.extend(p for p in (source.rglob('*') if source.is_dir() else [source]) if p.is_file()
                 and not skip.intersection(p.relative_to(root).parts) and p.suffix not in {'.pyc', '.tsbuildinfo', '.log', '.lock', '.next'}
                 and not any(part in {'.DS_Store', 'Thumbs.db'} or part.startswith('._') for part in p.relative_to(root).parts)
                 and not p.name.startswith('.embedding-'))
# Compare against actual local secret without ever logging its value.
from dotenv import dotenv_values
secrets = [value for name, value in dotenv_values(root / '.env').items()
           if value and (name.endswith('_API_KEY') or name.endswith('_PASSWORD'))]
for path in files:
    if path.name.startswith('.env') and path.name not in {'.env.example', '.env.server.example'}:
        raise SystemExit('Unexpected environment file in bundle')
    if any(secret.encode() in path.read_bytes() for secret in secrets):
        raise SystemExit('Secret detected in ' + str(path.relative_to(root)))
with tarfile.open(archive, 'w:gz') as bundle:
    for path in sorted(files):
        bundle.add(path, arcname='wu-tree-chatbot/' + str(path.relative_to(root)), recursive=False)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(destination / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')
print(f'Packaged {len(files)} files, {archive.stat().st_size:,} bytes; no API key or conversation state included.')
