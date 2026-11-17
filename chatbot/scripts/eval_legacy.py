"""Unchanged V3 live acceptance checks, extracted from live_eval.py for JSONL migration."""

def check_zh27(case, result, service, settings):
    name, expected_entry, word = case['id'], case.get('expected_entry'), case.get('word')
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
    return {'legacy_zh27': bool(ok)}
