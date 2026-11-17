import hashlib
import json
import math
import shutil
import sqlite3

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.embeddings import EmbeddingError, QwenEmbedding, QUERY_INSTRUCTION, native_base_url
from app.indexing import build_index
from app.knowledge import Knowledge
from app.retrieval import VectorRetrieval, VectorIndex
from app.provider import ModelError
from app.reranking import QwenReranker, RerankError, RERANK_INSTRUCTION


@pytest.fixture
def embedding_settings(settings, tmp_path, monkeypatch):
    fixture_knowledge = lambda directory: Knowledge(directory, allow_fallback=True)
    monkeypatch.setattr("app.indexing.Knowledge", fixture_knowledge)
    monkeypatch.setattr("app.main.Knowledge", fixture_knowledge)
    target = tmp_path / 'knowledge'
    target.mkdir()
    for name in ('story.db', 'manifest.json'):
        shutil.copyfile(settings.knowledge_dir / name, target / name)
    # No keyword index exists in this fixture: every build/search must work
    # without FTS5, including all error and restart paths.
    with sqlite3.connect(target / 'story.db') as db:
        db.execute('DROP TABLE chunks_fts')
    manifest = json.loads((target / 'manifest.json').read_text())
    manifest['database_sha256'] = hashlib.sha256((target / 'story.db').read_bytes()).hexdigest()
    (target / 'manifest.json').write_text(json.dumps(manifest))
    settings.knowledge_dir = target
    settings.embedding_api_key = SecretStr('test-embedding-secret')
    settings.embedding_base_url = 'https://embedding.example/api/v1'
    settings.embedding_dimensions = 256
    return settings


class FakeEmbedding:
    configured = True

    def __init__(self, settings):
        self.settings, self.calls = settings, []
        self.failure = False

    async def embed(self, texts, text_type='document'):
        self.calls.append((texts, text_type))
        if self.failure:
            raise EmbeddingError('credentials')
        return [[1.0] + [0.0] * (self.settings.embedding_dimensions - 1) for _ in texts], {'usage': {'total_tokens': 1}}

    async def query(self, text):
        vectors, audit = await self.embed([text], 'query')
        return vectors[0], audit

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_native_protocol_reorders_batches_and_separates_query_role(embedding_settings):
    cfg = embedding_settings
    seen = []

    def response(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path.endswith('/api/v1/services/embeddings/text-embedding/text-embedding')
        assert request.headers['Authorization'] == 'Bearer test-embedding-secret'
        rows = [{'index': i, 'embedding': [i + 1.0, 1.0] + [0.0] * 254} for i in reversed(range(len(body['input']['texts'])))]
        return httpx.Response(200, json={'output': {'embeddings': rows}, 'usage': {'total_tokens': 9, 'untrusted': 'secret'}})

    client = QwenEmbedding(cfg)
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        vectors, audit = await client.embed(['甲', '乙'])
        assert vectors[0][0] == pytest.approx(1 / math.sqrt(2))
        assert vectors[1][0] == pytest.approx(2 / math.sqrt(5))
        assert seen[0]['parameters'] == {'dimension': 256, 'output_type': 'dense', 'text_type': 'document'}
        assert audit['usage'] == {'total_tokens': 9}
        await client.query('问题')
        _, cached = await client.query('问题')
        assert len(seen) == 2 and cached['cached']
        assert seen[1]['parameters']['instruct'] == QUERY_INSTRUCTION
        assert seen[1]['parameters']['text_type'] == 'query'
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'dimension', 'nan', 'zero', 'model', 'credentials', 'redirect'])
async def test_bad_embeddings_rejected_without_leaking_response(embedding_settings, bad):
    client = QwenEmbedding(embedding_settings)
    await client.client.aclose()
    data = {'output': {'embeddings': [{'text_index': 0, 'embedding': [1.0] + [0.0] * 255}]}}
    status = 200
    if bad == 'missing':
        data['output']['embeddings'] = []
    elif bad == 'duplicate':
        data['output']['embeddings'] *= 2
    elif bad == 'dimension':
        data['output']['embeddings'][0]['embedding'] = [1.0]
    elif bad == 'nan':
        data['output']['embeddings'][0]['embedding'][0] = 'NaN'
    elif bad == 'zero':
        data['output']['embeddings'][0]['embedding'][0] = 0.0
    elif bad == 'model':
        data['model'] = 'a-different-model'
    elif bad == 'credentials':
        status, data = 401, {'message': 'test-embedding-secret'}
    elif bad == 'redirect':
        status = 302
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=data)))
    try:
        with pytest.raises(EmbeddingError) as err:
            await client.embed(['test'])
        assert 'secret' not in str(err.value)
    finally:
        await client.close()


def test_env_dimensions_and_endpoint_normalization(tmp_path):
    env = tmp_path / '.env'
    env.write_text('EMBEDDING_DIMENSIONS=1024\nEMBEDDING_BASE_URL=https://workspace.example/compatible-mode/v1\n')
    cfg = Settings(_env_file=env)
    assert cfg.embedding_dimensions == 1024
    assert native_base_url(cfg.embedding_base_url) == 'https://workspace.example/api/v1'


@pytest.mark.asyncio
async def test_index_build_inputs_reuse_and_failed_rebuild_preserves_snapshot(embedding_settings):
    cfg = embedding_settings
    before = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    with before.db() as db:
        original = db.execute('SELECT chunk_id,chunk FROM chunks ORDER BY chunk_id').fetchall()
        expected = [r[0] + '\n' + r[1] for r in db.execute('SELECT context_header,text FROM chunks ORDER BY chunk_id')]
    provider = FakeEmbedding(cfg)
    report = await build_index(cfg, provider)
    assert report['generated'] == 138 and report['reused'] == 0
    assert [t for texts, role in provider.calls for t in texts] == expected
    assert all(role == 'document' and len(texts) <= 20 for texts, role in provider.calls)
    ready = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    with ready.db() as db:
        assert [tuple(r) for r in original] == [tuple(r) for r in db.execute('SELECT chunk_id,chunk FROM chunks ORDER BY chunk_id')]
    assert len(VectorIndex(ready, cfg).vectors) == 138
    assert (await build_index(cfg, provider))['generated'] == 0 and len(provider.calls) == 7
    digest = hashlib.sha256(ready.path.read_bytes()).hexdigest()
    cfg.embedding_dimensions = 512
    assert VectorIndex(ready, cfg).reason == 'encoder_mismatch'
    provider.failure = True
    with pytest.raises(EmbeddingError):
        await build_index(cfg, provider)
    assert hashlib.sha256(ready.path.read_bytes()).hexdigest() == digest


@pytest.mark.asyncio
async def test_stale_vector_excluded_without_contacting_model(embedding_settings):
    cfg = embedding_settings
    provider = FakeEmbedding(cfg)
    await build_index(cfg, provider)
    calls = len(provider.calls)
    with sqlite3.connect(cfg.knowledge_dir / 'story.db') as db:
        db.execute("UPDATE chunk_vectors SET content_hash='invalid' WHERE chunk_id='shbl:story-002:body-2'")
    manifest_path = cfg.knowledge_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['database_sha256'] = hashlib.sha256((cfg.knowledge_dir / 'story.db').read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    retrieval = VectorRetrieval(Knowledge(cfg.knowledge_dir, allow_fallback=True), cfg, provider)
    assert retrieval.index.reason == 'stale_or_invalid_index' and not retrieval.index.vectors
    with pytest.raises(ModelError) as err:
        await retrieval.search('三官殿龙柏')
    assert err.value.code == 'vector_unavailable'
    assert err.value.audit['retrieval']['error_code'] == 'stale_or_invalid_index'
    assert len(provider.calls) == calls


@pytest.mark.asyncio
async def test_pure_vector_path_and_failure_never_uses_keyword_or_entity_fallback(embedding_settings):
    cfg = embedding_settings
    provider = FakeEmbedding(cfg)
    await build_index(cfg, provider)
    retrieval = VectorRetrieval(Knowledge(cfg.knowledge_dir, allow_fallback=True), cfg, provider)
    (context, sources, cards), audit = await retrieval.search('三官殿龙柏的树龄', [2])
    assert audit['mode'] == 'vector' and audit['embedding'] and audit['hits']
    assert all('vector_rank' in h for h in audit['hits'])
    assert len(cards) == 1 and len(sources) == 2 and len(context) <= 6000
    assert all(s['text'] in context for s in sources)
    provider.failure = True
    with pytest.raises(ModelError) as err:
        await retrieval.search('三官殿龙柏', [2])
    assert err.value.code == 'vector_unavailable'
    assert err.value.audit['retrieval']['error_code'] == 'embedding_credentials'


class FakeReranker:
    configured = True

    def __init__(self, failure=''):
        self.calls, self.failure = [], failure

    async def rank(self, query, documents):
        self.calls.append((query, documents))
        if self.failure:
            raise RerankError(self.failure)
        return [{'index': i, 'relevance_score': (i + 1) / len(documents)}
                for i in reversed(range(len(documents)))], {'model': 'test', 'usage': {}}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_rerank_http_request_and_only_index_score_accepted(embedding_settings):
    client = QwenReranker(embedding_settings)
    await client.client.aclose()

    def respond(request):
        body = json.loads(request.content)
        assert request.url.path.endswith('/api/v1/services/rerank/text-rerank/text-rerank')
        assert body['input'] == {'query': '问题', 'documents': ['甲', '乙']}
        assert body['parameters'] == {'top_n': 2, 'instruct': RERANK_INSTRUCTION}
        return httpx.Response(200, json={'output': {'results': [
            {'index': 0, 'relevance_score': 0.2, 'chunk_id': 'untrusted-id'},
            {'index': 1, 'relevance_score': 0.9, 'document': {'text': 'untrusted replacement'}}]},
            'usage': {'total_tokens': 12, 'message': 'sensitive'}})

    client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        ordered, audit = await client.rank('问题', ['甲', '乙'])
        assert ordered == [{'index': 1, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.2}]
        assert audit['usage'] == {'total_tokens': 12}
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['duplicate', 'missing', 'out_of_range', 'boolean_index', 'score', 'null', 'credentials', 'redirect'])
async def test_invalid_rerank_rejected_without_upstream_leak(embedding_settings, bad):
    client = QwenReranker(embedding_settings)
    await client.client.aclose()
    rows = [{'index': 0, 'relevance_score': 0.7}, {'index': 1, 'relevance_score': 0.2}]
    status = 200
    if bad == 'duplicate':
        rows[1]['index'] = 0
    elif bad == 'missing':
        rows.pop()
    elif bad == 'out_of_range':
        rows[1]['index'] = 2
    elif bad == 'boolean_index':
        rows[0]['index'] = False
    elif bad == 'score':
        rows[1]['relevance_score'] = 1.1
    elif bad == 'null':
        rows = None
    elif bad == 'credentials':
        status = 401
    elif bad == 'redirect':
        status = 302
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status,
        json={'output': {'results': rows}, 'message': 'test-embedding-secret'})))
    try:
        with pytest.raises(RerankError) as err:
            await client.rank('问题', ['甲', '乙'])
        assert 'secret' not in str(err.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rerank_preserves_chunk_identity_and_original_citation_order(embedding_settings):
    cfg = embedding_settings
    embedding, reranker = FakeEmbedding(cfg), FakeReranker()
    await build_index(cfg, embedding)
    knowledge = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    retrieval = VectorRetrieval(knowledge, cfg, embedding, reranker)
    (context, sources, _), audit = await retrieval.search('三官殿龙柏', [2])
    assert audit['mode'] == 'vector' and audit['ranking'] == 'rerank'
    assert audit['hits'][0]['chunk_id'] == 'shbl:story-002:body-2'
    assert audit['hits'][0]['vector_rank'] == 2 and audit['hits'][0]['rerank_rank'] == 1
    assert sources[1]['chunk_id'] == 'shbl:story-002:body-2' and sources[1]['id'] == 'S2'
    assert all(s['text'] in context for s in sources)
    assert reranker.calls[0][1] == [c['context_header'] + '\n' + c['text'] for c in knowledge.chunks(2)]
    cfg.rerank_enabled = False
    (_, _, _), audit = await retrieval.search('三官殿龙柏', [2])
    assert audit['ranking'] == 'cosine' and audit['rerank']['status'] == 'disabled'
    assert len(reranker.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'credentials', 'format'])
async def test_rerank_failure_keeps_vector_order_without_fts(embedding_settings, failure):
    cfg = embedding_settings
    embedding = FakeEmbedding(cfg)
    await build_index(cfg, embedding)
    retrieval = VectorRetrieval(Knowledge(cfg.knowledge_dir, allow_fallback=True), cfg, embedding, FakeReranker(failure))
    (_, sources, _), audit = await retrieval.search('三官殿龙柏', [2])
    assert sources and audit['mode'] == 'vector' and audit['ranking'] == 'cosine'
    assert audit['rerank'] == {'status': 'failed', 'reason': failure, 'enrichment': 'off'}
    assert [h['vector_rank'] for h in audit['hits']] == [1]
    assert all('rerank_rank' not in h for h in audit['hits'])


@pytest.mark.asyncio
async def test_vector_failure_is_persisted_as_error_even_with_known_entry(embedding_settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    class UnderstandOnly:
        async def close(self):
            pass

        async def json(self, *args, **kwargs):
            return {'mode': 'qa', 'search_query': '三官殿龙柏'}, {'model': 'test', 'usage': {}}

    cfg = embedding_settings
    embedding = FakeEmbedding(cfg)
    await build_index(cfg, embedding)
    embedding.failure = True
    with TestClient(create_app(cfg, UnderstandOnly(), embedding, FakeReranker())) as client:
        client.get('/api/session')
        cid = client.post('/api/conversations', json={}).json()['id']
        client.post(f'/api/conversations/{cid}/messages', json={'text': '三官殿龙柏的树龄', 'request_id': 'vector-failure-1'})
        message = client.get(f'/api/conversations/{cid}/messages').json()[-1]
        assert message['status'] == message['result']['status'] == 'error'
        assert message['result']['error_code'] == 'vector_unavailable'
        assert message['result']['audit']['retrieval']['error_code'] == 'embedding_credentials'
        assert not message['result'].get('claims') and not message['result'].get('sources')


def test_legacy_retrieval_modes_cannot_be_enabled():
    for mode in ('hybrid', 'keyword'):
        with pytest.raises(ValueError):
            Settings(_env_file=None, retrieval_mode=mode)


@pytest.mark.asyncio
async def test_wider_vector_recall_does_not_truncate_parent_expansion(embedding_settings):
    cfg = embedding_settings
    embedding, reranker = FakeEmbedding(cfg), FakeReranker()
    await build_index(cfg, embedding)
    retrieval = VectorRetrieval(Knowledge(cfg.knowledge_dir, allow_fallback=True), cfg, embedding, reranker)
    (_, _, _), audit = await retrieval.search('正文中的另一种树', candidate_limit=80)
    assert audit['candidate_count'] == 80 and len(reranker.calls[0][1]) == 80
    assert reranker.calls[0][0] == '正文中的另一种树'
    assert len(audit['hits']) == len(audit['selected_entries']) == 8
    assert {h['chunk_id'] for h in audit['hits']} <= set(audit['context_chunks'])
    assert audit['n_chars'] <= cfg.retrieval_context_chars
    assert not audit['corpus_fully_recalled']






def test_full_current_book_fits_expansion_budget_without_dropping_evidence(settings):
    knowledge = Knowledge(settings.knowledge_dir)
    retrieval = VectorRetrieval(knowledge, settings)
    hits = [dict(chunk_id=c['chunk_id'], unit_id=c['unit_id'])
            for no in knowledge.cards for c in knowledge.chunks(no)]
    parents, dropped = retrieval.expand(hits, budget=settings.retrieval_context_chars)
    context, sources, cards = knowledge.pack(parents)
    assert len(sources) == 138 and len(cards) == 105 and not dropped
    assert 6000 < len(context) <= settings.retrieval_context_chars
    with pytest.raises(ModelError, match='超过'):
        retrieval.expand(hits, budget=6000)




def export_module():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('knowledge_export', Path(__file__).resolve().parents[2] / 'scripts/export_knowledge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
async def test_export_preserves_unchanged_vectors_and_builds_only_changed_input(embedding_settings, tmp_path, changed):
    from pathlib import Path
    cfg = embedding_settings
    embedding = FakeEmbedding(cfg)
    await build_index(cfg, embedding)
    before = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    with before.db() as db:
        old_vectors = {r[0]: r[1] for r in db.execute('SELECT chunk_id,vec FROM chunk_vectors')}
    source = tmp_path / 'export-source'
    source.mkdir()
    (source / 'images/story').mkdir(parents=True)
    shutil.copyfile(before.path, source / 'story.db')
    if changed:
        with sqlite3.connect(source / 'story.db') as db:
            row = db.execute("SELECT chunk FROM chunks WHERE chunk_id='shbl:story-001:body-1'").fetchone()
            chunk = json.loads(row[0])
            chunk['text'] += '更新正文。'
            chunk['content_hash'] = 'sha256:' + hashlib.sha256((chunk['context_header'] + '\n' + chunk['text']).encode()).hexdigest()[:16]
            db.execute('UPDATE chunks SET text=?,content_hash=?,chunk=? WHERE chunk_id=?',
                       (chunk['text'], chunk['content_hash'], json.dumps(chunk), chunk['chunk_id']))
    report = export_module().export_snapshot(source, cfg.knowledge_dir)
    assert report['vectors_reused'] == 138 - int(changed) and report['vectors_pending'] == int(changed)
    after = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    with after.db() as db:
        for cid, vec in db.execute('SELECT chunk_id,vec FROM chunk_vectors'):
            assert old_vectors[cid] == vec
    assert bool(VectorIndex(after, cfg).reason) == changed
    # No checkpoint database is available to mask lost vectors.
    cfg.state_dir = tmp_path / 'fresh-state'
    embedding.calls.clear()
    built = await build_index(cfg, embedding)
    assert built['generated'] == int(changed) and built['reused'] == 138 - int(changed)
    assert len(embedding.calls) == int(changed)
    assert not VectorIndex(Knowledge(cfg.knowledge_dir, allow_fallback=True), cfg).reason


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [1, 6, 8])
async def test_ranked_distinct_parents_are_selected_without_reading_model(embedding_settings, limit):
    cfg = embedding_settings
    embedding, reranker = FakeEmbedding(cfg), FakeReranker()
    await build_index(cfg, embedding)
    knowledge = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    retrieval = VectorRetrieval(knowledge, cfg, embedding, reranker)
    (context, sources, cards), audit = await retrieval.search('古树故事', top_entries=limit)
    expected = list(dict.fromkeys(h['unit_id'] for h in audit['ranked_hits']))[:limit]
    assert [c['unit_id'] for c in cards] == expected
    assert len(cards) == limit and len(reranker.calls) == 1
    assert len(reranker.calls[0][1]) == len(audit['ranked_hits']) == 138
    assert {h['chunk_id'] for h in audit['hits']} <= {s['chunk_id'] for s in sources}
    assert all(s['text'] in context for s in sources)
    assert len(sources) == sum(len(knowledge.chunks(c['entry_no'])) for c in cards)


@pytest.mark.asyncio
@pytest.mark.parametrize('districts,found', [([], True), (['吴江区'], True), (['吴中区'], False)])
async def test_incidental_subject_at_rank_24_has_in_memory_rescue(embedding_settings, districts, found):
    cfg = embedding_settings
    embedding = FakeEmbedding(cfg)
    await build_index(cfg, embedding)
    knowledge = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    retrieval = VectorRetrieval(knowledge, cfg, embedding)
    async def ranking(query, dense, enrichment=None):
        incidental = next(h for h in dense if h['chunk_id'] == 'shbl:story-066:body-1')
        # Many other parents precede the relevant incidental mention.
        shadows = [h for h in dense if h['unit_id'] in {'shbl:story-037', 'shbl:story-012'}]
        ordered = shadows + [h for h in dense if h is not incidental and h not in shadows]
        ordered.insert(23, incidental)
        return [h | {'rerank_rank': i + 1, 'rerank_score': 1 - i / 200}
                for i, h in enumerate(ordered)], {'status': 'applied'}
    retrieval.reorder = ranking
    (_, sources, cards), audit = await retrieval.search('杨树故事', top_entries=6, districts=districts, subject='杨树')
    assert audit['ranked_hits'][23]['chunk_id'] == 'shbl:story-066:body-1'
    assert (66 in [c['entry_no'] for c in cards]) == found
    if not districts:
        assert audit['supplemental_chunks'][0] == 'shbl:story-066:body-1'
        assert 1 <= len(audit['supplemental_chunks']) <= 3 and 7 <= len(cards) <= 9
    assert audit['candidate_count'] == 138 and audit['candidate_entries'] == []


@pytest.mark.asyncio
async def test_district_filter_follows_rerank_and_keeps_cross_district_body(embedding_settings, monkeypatch):
    cfg = embedding_settings
    embedding, reranker = FakeEmbedding(cfg), FakeReranker()
    await build_index(cfg, embedding)
    knowledge = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    chunks = knowledge.chunks
    def cross_district(no):
        return [c | {'text': c['text'] + ('吴中区也有相关树木。' if no == 66 else '')} for c in chunks(no)]
    monkeypatch.setattr(knowledge, 'chunks', cross_district)
    retrieval = VectorRetrieval(knowledge, cfg, embedding, reranker)
    (_, _, cards), audit = await retrieval.search('吴中古树', top_entries=20, districts=['吴中区'])
    assert len(reranker.calls[0][1]) == 138
    assert any(c['entry_no'] == 66 and c['district'] == '吴江区' for c in cards)
    assert audit['district_filtered_count'] > 0
    for c in cards:
        assert c['district'] == '吴中区' or any('吴中' in x['context_header'] + x['text'] for x in knowledge.chunks(c['entry_no']))


@pytest.mark.asyncio
async def test_subject_rescue_is_bounded_to_three_recalled_chunks(embedding_settings, monkeypatch):
    cfg = embedding_settings
    embedding, reranker = FakeEmbedding(cfg), FakeReranker()
    await build_index(cfg, embedding)
    knowledge = Knowledge(cfg.knowledge_dir, allow_fallback=True)
    original = knowledge.chunks
    # With FakeRerank reversed, these four entries are all beyond the first parent.
    monkeypatch.setattr(knowledge, 'chunks', lambda no: [c | {'text': c['text'] + ('稀有测试树' if no in (2, 3, 4, 5) else '')} for c in original(no)])
    retrieval = VectorRetrieval(knowledge, cfg, embedding, reranker)
    (_, _, cards), audit = await retrieval.search('稀有测试树', top_entries=1, subject='稀有测试树')
    assert len(audit['supplemental_chunks']) == 3
    assert len(cards) <= 4
    assert set(audit['supplemental_chunks']) <= {h['chunk_id'] for h in audit['ranked_hits']}
