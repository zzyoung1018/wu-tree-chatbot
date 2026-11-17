"""DashScope text embeddings. Credentials and raw upstream errors never enter audits."""
import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict

import httpx

QUERY_INSTRUCTION = 'Given a question about ancient trees in Wuzhong, retrieve relevant passages from the book describing the trees, their history, stories, field visits, or conservation interviews.'
API_PATH = '/services/embeddings/text-embedding/text-embedding'


class EmbeddingError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Embedding request failed: ' + code)


def native_base_url(value):
    value = value.rstrip('/')
    if value.endswith(API_PATH):
        value = value[:-len(API_PATH)]
    if value.endswith('/compatible-mode/v1'):
        value = value[:-len('/compatible-mode/v1')] + '/api/v1'
    if value and not value.endswith('/api/v1'):
        value += '/api/v1'
    return value


def encoder_spec(settings):
    # API hosts identify a region/workspace. A changed endpoint requires explicit
    # rebuilding, so vectors from different deployments cannot silently mix.
    return {'version': 1, 'provider': 'dashscope', 'model': settings.embedding_model,
            'dim': settings.embedding_dimensions, 'text_type': 'document',
            'input_format': 'context_header+newline+text',
            'endpoint_sha256': hashlib.sha256(native_base_url(settings.embedding_base_url).encode()).hexdigest()}


def normalized(values, dim):
    if not isinstance(values, (list, tuple)) or len(values) != dim:
        raise ValueError('wrong embedding dimensions')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('non-finite embedding')
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError('zero embedding')
    return [v / norm for v in values]


class QwenEmbedding:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(settings.embedding_timeout, connect=10), follow_redirects=False)
        self.semaphore = asyncio.Semaphore(4)
        self.cache = OrderedDict()

    @property
    def configured(self):
        return bool(self.settings.embedding_base_url and self.settings.embedding_api_key.get_secret_value())

    async def close(self):
        self.cache.clear()
        await self.client.aclose()

    async def embed(self, texts, text_type='document'):
        if not self.configured:
            raise EmbeddingError('unconfigured')
        if text_type not in ('query', 'document') or not 1 <= len(texts) <= 20 or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('Expected 1–20 non-empty texts and query/document role')
        params = {'text_type': text_type, 'dimension': self.settings.embedding_dimensions, 'output_type': 'dense'}
        if text_type == 'query':
            params['instruct'] = QUERY_INSTRUCTION
        payload = {'model': self.settings.embedding_model, 'input': {'texts': texts}, 'parameters': params}
        started = time.monotonic()
        async with self.semaphore:
            for attempt in range(2):
                try:
                    response = await self.client.post(native_base_url(self.settings.embedding_base_url) + API_PATH,
                        headers={'Authorization': 'Bearer ' + self.settings.embedding_api_key.get_secret_value()}, json=payload)
                except httpx.TimeoutException:
                    raise EmbeddingError('timeout') from None
                except httpx.RequestError:
                    raise EmbeddingError('network') from None
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                if response.status_code in (401, 403):
                    raise EmbeddingError('credentials')
                if response.status_code != 200:
                    raise EmbeddingError('rate_limit' if response.status_code == 429 else 'upstream')
                try:
                    data = response.json()
                    rows = data['output']['embeddings']
                    indexed = {}
                    for row in rows:
                        index = row.get('text_index', row.get('index'))
                        if type(index) is not int or index in indexed or not 0 <= index < len(texts):
                            raise ValueError('invalid input index')
                        indexed[index] = normalized(row['embedding'], self.settings.embedding_dimensions)
                    if len(indexed) != len(texts):
                        raise ValueError('incomplete batch')
                    if data.get('model', self.settings.embedding_model) != self.settings.embedding_model:
                        raise ValueError('different model')
                    # Allowlist numeric usage only; never persist arbitrary provider fields.
                    usage = {k: v for k, v in data.get('usage', {}).items()
                             if k in ('total_tokens', 'prompt_tokens') and type(v) is int and v >= 0}
                    return [indexed[i] for i in range(len(texts))], {
                        'model': self.settings.embedding_model, 'dim': self.settings.embedding_dimensions,
                        'text_type': text_type, 'texts': len(texts), 'usage': usage,
                        'duration_ms': round((time.monotonic() - started) * 1000), 'cached': False}
                except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                    raise EmbeddingError('format') from None

    async def query(self, text):
        # Exact question cache, in memory only, bounded and valid for five minutes.
        digest = hashlib.sha256(json.dumps([encoder_spec(self.settings), QUERY_INSTRUCTION, text], sort_keys=True).encode()).hexdigest()
        cached = self.cache.get(digest)
        if cached and time.monotonic() - cached[0] < 300:
            self.cache.move_to_end(digest)
            return cached[1], {'model': self.settings.embedding_model, 'dim': self.settings.embedding_dimensions,
                              'text_type': 'query', 'texts': 1, 'usage': {}, 'cached': True, 'duration_ms': 0}
        vectors, audit = await self.embed([text], 'query')
        self.cache[digest] = (time.monotonic(), vectors[0])
        self.cache.move_to_end(digest)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return vectors[0], audit
