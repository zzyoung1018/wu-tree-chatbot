import pytest
from app.knowledge import Knowledge
from app.bilingual import subject_matches
from test_service import ScriptedProvider, make_service, emit
from test_bilingual import plan

@pytest.mark.parametrize('group,en,zh',[('camellia','camellia','山茶类'),('magnolia','magnolia','玉兰类'),('cypress_juniper','cypress','柏树'),('pine','pine','松树')])
def test_group_union_matches_member_counts_and_bilingual_generic(settings,group,en,zh):
    k=Knowledge(settings.knowledge_dir); members=k.glossary['species_groups'][group]['members']
    count=len(k.structured({'species':group})[0])
    assert count==sum(len(k.structured({'species':s})[0]) for s in members)
    assert count==len(k.structured({'species':members})[0])
    assert k.bilingual.canonical('species',en)==k.bilingual.canonical('species',zh)==group
    assert k.resolve_bilingual_terms(en)['matched_species_groups']==[group]
    assert not k.resolve_bilingual_terms(en)['matched_species']


def test_groups_cover_catalog_and_precise_species_win(settings):
    k=Knowledge(settings.knowledge_dir);groups=k.glossary['species_groups']
    assert {m for g in groups.values() for m in g['members']}==set(k.species)
    assert k.resolve_bilingual_terms('yulan magnolia')['matched_species']==['玉兰']
    assert not k.resolve_bilingual_terms('yulan magnolia')['matched_species_groups']
    assert k.resolve_bilingual_terms('Buddhist pine')['matched_species']==['罗汉松']
    assert not k.resolve_bilingual_terms('Can I sip tea under an old tree?')['matched_districts']
    assert not k.resolve_bilingual_terms('sips SIPs xSIP')['matched_districts']
    assert set(k.resolve_bilingual_terms('SIP and SND')['matched_districts'])=={'工业园区','虎丘区（高新区）'}
    assert not subject_matches('杨树',['枫杨','黄杨'])
    assert k.subject_matches('pine',['马尾松']) and not k.subject_matches('pine',['罗汉松'])

@pytest.mark.asyncio
async def test_inventory_uses_union_and_static_related_note(settings):
    p=ScriptedProvider(plan('全书松树总数','pine',mode='inventory',filters={'species':'马尾松'}))
    svc=make_service(settings,p)
    result=await svc.answer('书里松树一共有多少项？',{},[],emit)
    members=svc.knowledge.glossary['species_groups']['pine']['members']
    assert result['structured']['filters']['species']==members
    assert result['structured']['item_count']==len(svc.knowledge.structured({'species':members})[0])
    assert '罗汉松、雪松另计' in result['text'] and len(p.inputs)==1

@pytest.mark.asyncio
async def test_background_group_does_not_replace_positive_inventory_filter(settings):
    p=ScriptedProvider(plan('全书银杏树项总数','银杏',mode='inventory',filters={'species':'银杏'}))
    r=await make_service(settings,p).answer('先不管柏树，书中银杏一共有多少树项？',{},[],emit)
    assert r['structured']['filters']['species']=='银杏'
    k=Knowledge(settings.knowledge_dir)
    assert k.bilingual.canonical('districts','sip')=='sip'
    assert k.bilingual.canonical('districts','SIP')=='工业园区'

@pytest.mark.asyncio
async def test_inventory_plan_with_explicit_clarification_cannot_count_unfiltered_catalog(settings):
    p=ScriptedProvider(plan('全书超过一千年的古树数量','',mode='inventory',filters={},clarification='目录无法按树龄区间精确计数。'))
    svc=make_service(settings,p)
    r=await svc.answer('超过一千年的古树有多少棵？',{},[],emit)
    assert r['status']=='clarify' and 'structured' not in r and not svc.retrieval.calls
    assert r['cards']==[] and '无法按树龄' in r['text']
