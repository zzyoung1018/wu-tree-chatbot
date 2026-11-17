"""Rank only the supplied vector candidates using the same DashScope workspace."""
import asyncio
import math
import time

import httpx

from .embeddings import native_base_url

API_PATH = '/services/rerank/text-rerank/text-rerank'
RERANK_INSTRUCTION = 'Given a question about ancient trees in Wuzhong, rank the passages by whether they provide evidence that answers the question. Preserve distinctions between tree species, locations, legends, historical records, and interviews.'


class RerankError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Rerank request failed: ' + code)


class QwenReranker:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(settings.rerank_timeout, connect=10), follow_redirects=False)
        self.semaphore = asyncio.Semaphore(4)

    @property
    def configured(self):
        return bool(self.settings.embedding_base_url and self.settings.embedding_api_key.get_secret_value())

    async def close(self):
        await self.client.aclose()

    async def rank(self, query, documents):
        if not self.configured:
            raise RerankError('unconfigured')
        if not query.strip() or not 1 <= len(documents) <= 500 or any(not isinstance(d, str) or not d.strip() for d in documents):
            raise ValueError('Expected a query and 1–500 non-empty candidate documents')
        payload = {'model': self.settings.rerank_model, 'input': {'query': query, 'documents': documents},
                   'parameters': {'top_n': len(documents), 'instruct': RERANK_INSTRUCTION}}
        started = time.monotonic()
        async with self.semaphore:
            for attempt in range(2):
                try:
                    response = await self.client.post(native_base_url(self.settings.embedding_base_url) + API_PATH,
                        headers={'Authorization': 'Bearer ' + self.settings.embedding_api_key.get_secret_value()}, json=payload)
                except httpx.TimeoutException:
                    raise RerankError('timeout') from None
                except httpx.RequestError:
                    raise RerankError('network') from None
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                if response.status_code in (401, 403):
                    raise RerankError('credentials')
                if response.status_code != 200:
                    raise RerankError('rate_limit' if response.status_code == 429 else 'upstream')
                try:
                    data = response.json()
                    rows, seen = data['output']['results'], set()
                    ordered = []
                    for row in rows:
                        index, score = row['index'], row['relevance_score']
                        if type(index) is not int or not 0 <= index < len(documents) or index in seen:
                            raise ValueError('invalid document index')
                        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                            raise ValueError('invalid relevance score')
                        seen.add(index)
                        ordered.append({'index': index, 'relevance_score': score})
                    if len(seen) != len(documents):
                        raise ValueError('incomplete rerank results')
                    if data.get('model', self.settings.rerank_model) != self.settings.rerank_model:
                        raise ValueError('different model')
                    usage = {k: v for k, v in data.get('usage', {}).items()
                             if k in ('prompt_tokens', 'total_tokens') and type(v) is int and v >= 0}
                    return sorted(ordered, key=lambda r: (-r['relevance_score'], r['index'])), {
                        'model': self.settings.rerank_model, 'documents': len(documents), 'usage': usage,
                        'duration_ms': round((time.monotonic() - started) * 1000)}
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    raise RerankError('format') from None
