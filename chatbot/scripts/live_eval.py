"""Explicit real DeepSeek evaluation. Outputs claims and metrics, never credentials or full prompts."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.knowledge import Knowledge
from app.provider import DeepSeek, ModelError
from app.service import ChatService
from app.embeddings import QwenEmbedding
from app.retrieval import VectorRetrieval
from app.reranking import QwenReranker

CASES = [
    ('sanguan_age', '三官殿的龙柏有多少岁？', 2, '1500'),
    ('gone_list', '书中哪些树已经不存了？', None, None),
    ('legend', '司徒庙的清奇古怪真是邓禹种、乾隆赐名的吗？', 18, None),
    ('official_code', '吴中203是哪棵树？', 21, '石楠'),
    ('interview', '古树受伤了怎么保护？', 26, None),
    ('opening_hours', '博物馆明天几点开门？', None, None),
    ('unknown_tree', '火星村那棵万年龙血树今年多少岁？', None, None),
    ('location_conflict', '灵源古寺的罗汉松在哪里？', 11, None),
    ('historical_status', '现在天平山的马尾松还在吗？', 1, None),
    ('split_item', '木渎的罗汉松在哪里？', 23, '古松园'),
    ('unsupported_count', '书里树龄超过一千年的树共有多少棵？', None, None),
    ('district_overview', '给我介绍吴中区一些树', None, None),
    ('district_fun', '吴中区有哪些有意思的古树？', None, None),
    ('district_list', '列出书中吴中区的所有树木条目', None, None),
    ('semantic_photo', '外国摄影师把那棵树认成了樟树，后来测绘才确认品种，是哪一处？', 34, '枫杨'),
    ('semantic_mask', '为了躲开花的气味，调查人员提前带了什么？', 21, '口罩'),
    ('semantic_rock', '长在岩壁缝里，盘起来像卧着的龙的那棵是什么？', 21, '石楠'),
    ('semantic_pine', '苏州西边山上的那些松树后来为什么消失了？', 1, '松干蚧'),
    ('unlisted_species_story', '给我介绍一些古杨树以及关于他们的故事', 66, '双杨'),
    ('unlisted_species_wrong_district', '给我介绍吴中区古杨树的故事', None, None),
    ('rich_story', '详细介绍司徒庙的清奇古怪，讲讲它们的形态、传说和历史，并配上书里的图片', 18, '清奇古怪'),
    ('single_site_count', '司徒庙的清奇古怪一共有几棵古柏？', 18, None),
    ('short_district_negative', '介绍吴中古杨树的故事', None, None),
    ('short_district_positive', '介绍吴江古杨树的传说', 66, '双杨'),
    ('hightech_district', '介绍高新区一些古树', None, None),
    ('alias_location', '穹窿山海云院的连理山茶有什么故事？', 29, '海云院'),
    ('code_comparison', '吴中186和吴中187是同一处吗？', 18, '司徒庙'),
]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--concurrency', type=int, choices=range(1, 4), default=1)
    parser.add_argument('--output', type=Path, default=ROOT / 'state/live-eval.json')
    args = parser.parse_args()
    settings = Settings()
    provider = DeepSeek(settings)
    embeddings = QwenEmbedding(settings)
    reranker = QwenReranker(settings)
    knowledge = Knowledge(settings.knowledge_dir)
    service = ChatService(knowledge, provider, settings, VectorRetrieval(knowledge, settings, embeddings, reranker))
    semaphore = asyncio.Semaphore(args.concurrency)
    reports = []

    async def emit(stage):
        pass

    async def run(case):
        name, query, expected_entry, word = case
        reported_name = name
        async with semaphore:
            try:
                result = await service.answer(query, {}, [], emit)
                text = result.get('text','') + '\n' + '\n'.join(c['text'] for c in result.get('claims',[]))
                units = sorted({s['entry_no'] for s in result.get('sources',[])})
                ok = result['status'] == 'answered'
                if expected_entry:
                    ok = ok and expected_entry in units
                if word:
                    ok = ok and word in text
                if name in ('sanguan_age', 'split_item'):
                    ok = ok and bool(result.get('focus')) and result['focus']['item_no'] == 2
                if name == 'gone_list':
                    ok = ok and result.get('structured',{}).get('item_count') == 4 and units == [1,14,29,34]
                elif name == 'opening_hours':
                    ok = result['status'] == 'insufficient' and not result.get('claims')
                elif name == 'unknown_tree':
                    ok = result['status'] in ('insufficient','clarify') and not result.get('claims')
                elif name == 'unsupported_count':
                    ok = result['status'] == 'clarify'
                elif name == 'legend':
                    ok = ok and any(w in text for w in ('依据','存疑','未','不能','无'))
                elif name == 'location_conflict':
                    ok = ok and '北望岭下村' in text and '灵源古寺' in text and '待馆方核实' not in text
                elif name == 'historical_status':
                    ok = ok and any(w in text for w in ('未能存活','已不存','没有存活','消失','死亡'))
                elif name == 'interview':
                    ok = ok and any(s['content_type']=='interview' for s in result.get('sources',[]))
                elif name in ('district_overview', 'district_fun'):
                    ok = ok and result.get('route') == 'explore' and len(units) >= 2 and len(text) >= 300 and all(c['district'] == '吴中区' for c in result.get('cards', []))
                    ok = ok and any(c.get('images') for c in result.get('claims', []))
                elif name == 'district_list':
                    values, _, _ = service.knowledge.structured({'district': '吴中区'})
                    ok = ok and result.get('structured', {}).get('item_count') == len(values) > 3
                elif name == 'unlisted_species_story':
                    ok = ok and units == [66] and '传说' in text and not any(s in text for s in ('枫杨', '黄杨')) and result.get('focus') is None and result.get('audit', {}).get('retrieval', {}).get('mode') == 'vector'
                elif name in ('unlisted_species_wrong_district', 'short_district_negative'):
                    ok = result['status'] in ('insufficient', 'clarify') and not result.get('claims')
                elif name == 'single_site_count':
                    ok = ok and any(w in text for w in ('四棵', '4棵', '四株', '4株')) and not result.get('structured')
                elif name == 'short_district_positive':
                    ok = ok and units == [66] and '传说' in text and result.get('focus') is None
                elif name == 'hightech_district':
                    ok = ok and all(c['district'] == '虎丘区（高新区）' for c in result.get('cards', []))
                elif name == 'rich_story':
                    ok = ok and len(text) >= 500 and len(result.get('claims', [])) >= 3
                    ok = ok and any(c.get('images') for c in result.get('claims', [])) and any(c.get('heading') for c in result.get('claims', []))
                    ok = ok and '传说' in text
                if name not in ('gone_list', 'district_list', 'unsupported_count'):
                    retrieval = result.get('audit', {}).get('retrieval', {})
                    stages = [c['stage'] for c in result.get('audit', {}).get('model_calls', [])]
                    ok = ok and retrieval.get('mode') == 'vector' and retrieval.get('candidate_entries') == []
                    ok = ok and retrieval.get('corpus_fully_recalled')
                    budget = settings.explore_top_entries if result.get('route') == 'explore' else settings.qa_top_entries
                    ok = ok and len(retrieval.get('selected_entries', [])) <= budget + len(retrieval.get('supplemental_chunks', []))
                    ok = ok and stages and stages[0] == 'understand' and 'relevance' not in stages
                    if name == 'district_fun':
                        ok = ok and len(stages) <= 3
                ok = ok and not result.get('notice') and not any(w in text for w in ('待审核', '待复核', '待人工复核', '现场核实', '未认定已不存'))
                report = {'name':name,'question':query,'passed':ok,'status':result['status'],'text':text,'entries':units,
                          'focus':result.get('focus'),'audit':result.get('audit'),'validation':result.get('validation'),
                          'headings':[c['heading'] for c in result.get('claims', []) if c.get('heading')],
                          'latency_target_met': result.get('audit', {}).get('duration_ms', 0) < 12000,
                          'image_ids':[p['image_id'] for c in result.get('claims', []) for p in c.get('images', [])]}
                reports.append(report)
                print(json.dumps(report,ensure_ascii=False), flush=True)
                if name in ('sanguan_age','split_item','legend') and result.get('focus'):
                    reported_name = name + '_followup'
                    follow_question = {'sanguan_age': '具体在什么位置？', 'split_item': '配上它的书内照片，并介绍一下。',
                                       'legend': '它们真是邓禹种的吗？'}[name]
                    history = [{'role':'user','status':'complete','text':query},
                               {'role':'assistant','status':'complete','result':result}]
                    followup = await service.answer(follow_question, {'focus':result['focus']}, history, emit)
                    follow_text = '\n'.join(c['text'] for c in followup.get('claims',[]))
                    expected = {'sanguan_age':'三官殿', 'split_item':'古松园', 'legend':'邓禹'}[name]
                    item_ids = {s['item_no'] for s in followup.get('sources',[])}
                    follow_ok = followup['status']=='answered' and expected in follow_text and item_ids==({1} if name == 'legend' else {2})
                    follow_images = [im for c in followup.get('claims', []) for im in c.get('images', [])]
                    expected_photos = []
                    if name == 'split_item':
                        expected_photos = [im['image_id'] for im in knowledge.cards[23]['images'] if im.get('item_no') == 2]
                        actual_photos = [im['image_id'] for im in follow_images]
                        follow_ok = follow_ok and set(actual_photos) <= set(expected_photos)
                        follow_ok = follow_ok and (bool(actual_photos) if expected_photos else not actual_photos)
                    if name == 'legend':
                        follow_ok = follow_ok and any(w in follow_text for w in ('依据', '存疑', '无法', '不能', '传说'))
                    follow_report = {'name':name+'_followup', 'question':follow_question, 'passed':follow_ok,
                                     'status':followup['status'],'text':follow_text,'focus':followup.get('focus'),'audit':followup.get('audit'),
                                     'expected_item_photos': expected_photos, 'image_ids': [im['image_id'] for im in follow_images], 'image_item_nos': [im.get('item_no') for im in follow_images]}
                    reports.append(follow_report)
                    print(json.dumps(follow_report,ensure_ascii=False),flush=True)
            except Exception as exc:
                report={'name':reported_name,'passed':False,'error':type(exc).__name__,'error_code':getattr(exc,'code',None),
                        'message':str(exc) if isinstance(exc, ModelError) else type(exc).__name__}
                reports.append(report); print(json.dumps(report),flush=True)
    try:
        await asyncio.gather(*(run(c) for c in CASES if not args.only or c[0] in args.only))
    finally:
        await provider.close()
        await embeddings.close()
        await reranker.close()
    (ROOT / 'state').mkdir(exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.only and args.output.exists():
        previous = json.loads(args.output.read_text()).get('cases', [])
        rerun_names = {r['name'] for r in reports}
        reports = [r for r in previous if r['name'] not in rerun_names] + reports
    report={'passed':sum(r['passed'] for r in reports),'total':len(reports),'cases':reports}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('EVALUATION',report['passed'],'/',report['total'])
    raise SystemExit(report['passed']!=report['total'])

if __name__=='__main__':
    asyncio.run(main())
