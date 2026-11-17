"""Real bilingual evaluation; writes only local test output, never user conversations."""
import asyncio
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.knowledge import Knowledge
from app.provider import DeepSeek
from app.embeddings import QwenEmbedding
from app.reranking import QwenReranker
from app.retrieval import VectorRetrieval
from app.service import ChatService

CASES = [
    ('inventory_en', 'How many ginkgo tree/grove items are recorded in Wuzhong District?', 'en', None),
    ('dragon_en', 'How old is the Dragon Juniper at Sanguan Hall? Please show its book photo.', 'en', 2),
    ('mudu_en', 'Where is the Buddhist pine in Mudu?', 'en', 23),
    ('four_en', 'Tell me about the four cypresses Qingqiguguai at Situ Temple. Did Deng Yu really plant them?', 'en', 18),
    ('camellia_en', "Tell me about the Hai'er Lian camellia. Why does it have that name?", 'en', 16),
    ('poplar_en', 'Tell me the story of the poplars in Shuangyang Village.', 'en', 66),
    ('theme_zh', '吴中区有哪些造型奇特或死而复生的古树？', 'zh', None),
    ('tiger_en', 'Are there any ancient trees on Tiger Hill?', 'en', None),
]

async def main():
    cfg = Settings()
    k = Knowledge(cfg.knowledge_dir)
    llm, embedding, reranker = DeepSeek(cfg), QwenEmbedding(cfg), QwenReranker(cfg)
    svc = ChatService(k, llm, cfg, VectorRetrieval(k,cfg,embedding,reranker))
    output, semaphore = [], asyncio.Semaphore(2)
    async def emit(stage): pass
    async def one(case):
        name, question, lang, expected = case
        async with semaphore:
            try:
                result = await svc.answer(question, {}, [], emit, ui_lang='zh')
                checks = {'response_language': result['lang'] == lang,
                          'static_quotes': all(s['quote'] == (k.chunk_enrichment[s['chunk_id']]['text_en'] if lang == 'en' else next(c['text'] for c in k.chunks(s['entry_no']) if c['chunk_id'] == s['chunk_id'])) for s in result['sources']),
                          'followups': bool(result['suggested_questions']) if result['cards'] else True}
                if expected: checks['expected_entry_cited'] = expected in [c['entry_no'] for c in result['cards']]
                if name == 'inventory_en': checks['complete_count'] = result.get('structured', {}).get('item_count') == len(k.structured({'district':'吴中区','species':'银杏'})[0])
                if name == 'poplar_en': checks['incidental_safety'] = result.get('focus') is None and not any(c.get('images') for c in result['claims'])
                if name == 'tiger_en': checks['no_huqiu_district_filter'] = result['audit']['retrieval'].get('plan', {}).get('districts') in ([], ['姑苏区'])
                if name == 'theme_zh': checks['thematic_exploration'] = result.get('route') == 'explore' and 'structured' not in result
                output.append({'name':name,'question':question,'checks':checks,'result':result})
                print(name, result['status'], checks, flush=True)
            except Exception as exc:
                output.append({'name':name,'error_type':type(exc).__name__})
                print(name, type(exc).__name__, flush=True)
            path = ROOT / 'state/improve-v4/live-eval.json'
            path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    try:
        await asyncio.gather(*(one(c) for c in CASES))
    finally:
        await llm.close(); await embedding.close(); await reranker.close()
    if any('checks' not in item or not all(item['checks'].values()) for item in output):
        raise SystemExit('One or more bilingual live checks failed; inspect local results.')

if __name__ == '__main__': asyncio.run(main())
