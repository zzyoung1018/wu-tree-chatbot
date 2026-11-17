import hashlib
import importlib.util
import sys
from pathlib import Path
import pytest
from app.knowledge import Knowledge
from app.retrieval import VectorRetrieval, input_hash

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from retrieval_eval import decide, metrics
from eval_common import Budget


def test_three_arm_inputs_preserve_source_and_v4_order(settings):
    knowledge = Knowledge(settings.knowledge_dir)
    chunks = knowledge.chunks(18)
    hit = [{'chunk_id': chunks[0]['chunk_id']}]
    retrieval = VectorRetrieval(knowledge, settings)
    meta = knowledge.chunk_enrichment[hit[0]['chunk_id']]
    original = chunks[0]['context_header'] + '\n' + chunks[0]['text']
    header = ' · '.join([*meta['themes'], meta['hook_zh'], meta['hook_en'], *meta['synthetic_queries_zh'], *meta['synthetic_queries_en']]).strip(' ·')
    before = input_hash(chunks[0])
    assert retrieval.rerank_documents(hit, 'off') == [original]
    assert retrieval.rerank_documents(hit, 'full') == [header + '\n' + original]
    lean = retrieval.rerank_documents(hit, 'lean')[0]
    assert meta['hook_zh'] in lean and meta['hook_en'] not in lean
    assert lean.endswith(original)
    assert input_hash(knowledge.chunks(18)[0]) == before
    with pytest.raises(ValueError):
        retrieval.rerank_documents(hit, 'typo')


def observation(name, group, off, lean, full):
    return {'id': name, 'set': group, 'arms': {arm: {'in_context': hit, 'hit_at_1': hit}
            for arm, hit in zip(('off', 'lean', 'full'), (off, lean, full))}}


def test_arm_gate_ignores_development_and_protects_chinese():
    assert decide([observation('dev', 'dev_zh', False, True, True)])['status'] == 'pending'
    rows = [observation(str(i), 'holdout_en', False, True, True) for i in range(3)]
    assert decide(rows)['status'] == 'pending'
    rows.extend([observation('zh0', 'holdout_zh', True, True, True),
                 observation('theme0', 'holdout_theme', True, True, True)])
    assert decide(rows)['selected_arm'] == 'lean'
    rows.append(observation('zh', 'holdout_zh', True, False, True))
    assert decide(rows)['selected_arm'] == 'full'
    rows.append(observation('zh2', 'holdout_zh', True, True, False))
    assert decide(rows)['selected_arm'] == 'off'


@pytest.mark.asyncio
async def test_budget_reserves_before_request_and_keeps_failure_charge(tmp_path):
    budget = Budget('test', tmp_path / 'usage.json')
    called = []
    async def fail():
        called.append(True)
        raise ValueError('test')
    with pytest.raises(RuntimeError):
        await budget.call('model', 6_000_001, fail)
    assert not called
    with pytest.raises(ValueError):
        await budget.call('model', 100, fail)
    import json
    ledger = json.loads(budget.path.read_text())
    assert ledger['totals']['model'] == 100
    assert ledger['calls'][0]['status'] == 'failed_usage_unknown'


@pytest.mark.asyncio
async def test_budget_counts_internal_model_retries_without_double_counting(settings, tmp_path):
    import httpx
    import json
    from app.provider import DeepSeek
    from pydantic import SecretStr
    cfg = settings.model_copy(update={'deepseek_api_key': SecretStr('fixture-only')})
    provider = DeepSeek(cfg)
    await provider.client.aclose()
    attempts = []
    def response(request):
        attempts.append(True)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
            'content':'invalid json' if len(attempts)==1 else '{}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':len(attempts),'total_tokens':10+len(attempts)}})
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    budget = Budget('retry-test', tmp_path/'usage.json')
    budget.wrap(provider=provider)
    try:
        await provider.json('system','user')
    finally:
        await provider.close()
    ledger = json.loads(budget.path.read_text())
    assert len(ledger['calls'])==2 and ledger['totals']['model']==23
