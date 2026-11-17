"""Build child embeddings. Stop the API before publishing a changed snapshot."""
import asyncio
import fcntl
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.config import Settings
from app.embeddings import EmbeddingError, QwenEmbedding
from app.indexing import build_index


async def main():
    settings = Settings()
    client = QwenEmbedding(settings)
    try:
        report = await build_index(settings, client, lambda p: print(json.dumps(p, ensure_ascii=False), flush=True))
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        (settings.state_dir / 'embedding-build.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))
    except EmbeddingError as exc:
        print('Embedding build failed: ' + exc.code + '; published knowledge unchanged.', file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await client.close()


if __name__ == '__main__':
    # Do not allow concurrent index writers. No secret is written to this lock.
    with (Settings().knowledge_dir / '.embedding-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main())
