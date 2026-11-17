import json
import pytest
from app.knowledge import Knowledge
from app.followups import choose, candidates
from test_service import ScriptedProvider,make_service,plan,draft,claim,emit


def result(k,entries,focus=None,route='qa',filters=None):
    return {'status':'answered','lang':'zh','cards':[k.public_card(k.cards[n]) for n in entries], 'focus':focus,'route':route,'structured':{'filters':filters or {}}}


def test_followups_bind_items_avoid_covered_age_and_inventory_drift(settings):
    k=Knowledge(settings.knowledge_dir)
    r=result(k,[2],{'entry_no':2,'item_no':2})
    qs=choose(k,r,[],'三官殿龙柏有多少岁？','三官殿龙柏树龄','龙柏')
    assert qs and not any('树龄' in q['text'] for q in qs)
    r=result(k,[23],{'entry_no':23,'item_no':2})
    qs=choose(k,r,[],'讲讲木渎罗汉松','木渎罗汉松','罗汉松')
    assert qs and not any('广玉兰' in q['text'] or '聚合果' in q['text'] for q in qs)
    rows,_,cards=k.structured({'district':'吴中区','species':'银杏'})
    r=result(k,[c['entry_no'] for c in cards],route='inventory',filters={'district':'吴中区','species':'银杏'})
    qs=choose(k,r,[],'吴中区银杏总数是多少？')
    assert qs[0]['id'].startswith('bridge-inventory-') and '吴中区' in qs[0]['text']
    assert not any('黑陶' in q['text'] for q in qs)
    allowed={q['id'] for c in cards for q in k.enrichment['entries'][str(c['entry_no'])]['followups']}
    assert all(q['id'] in allowed for q in qs[1:])


def test_six_clicks_never_repeat_shown_ids_even_across_languages(settings):
    k=Knowledge(settings.knowledge_dir);history=[];seen=set();r=result(k,[18],{'entry_no':18,'item_no':1});question='介绍司徒庙的树'
    for turn in range(7):
        r['lang']='en' if turn%2 else 'zh'
        qs=choose(k,r,history,question)
        ids=[q['id'] for q in qs]
        assert ids and not seen.intersection(ids) and len(ids)<=3
        seen.update(ids)
        history += [{'role':'user','status':'complete','text':question},{'role':'assistant','status':'complete','result':r|{'suggested_questions':[q['text'] for q in qs],'suggested_question_ids':ids}}]
        question=qs[0]['text']

@pytest.mark.asyncio
async def test_model_only_selects_local_ids_and_only_cited_cards(settings):
    p=ScriptedProvider(plan('三官殿龙柏','龙柏'),draft(claim('龙柏树龄约1500年。',source='S2'))|{'followup_ids':['invented','f023-1']},{'unsupported':[]})
    svc=make_service(settings,p,[2,23]);r=await svc.answer('三官殿龙柏有多少岁？',{},[],emit)
    assert r['suggested_question_ids'] and 'invented' not in r['suggested_question_ids']
    assert all(key.startswith(('f002-','bridge-entry-')) for key in r['suggested_question_ids'])
    payload=json.loads(p.inputs[1][1]);assert payload['followup_candidates']
    assert len(r['suggested_question_ids'])==len(r['suggested_questions'])<=3


def test_insufficient_uses_only_static_starters(settings):
    k=Knowledge(settings.knowledge_dir)
    r=result(k,[18]);r['status']='insufficient'
    assert all(q['id'].startswith('start-') for q in choose(k,r,[],'未知'))
