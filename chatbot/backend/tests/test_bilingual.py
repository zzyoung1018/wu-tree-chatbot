"""V4 coverage, immutable evidence and cross-language contract regressions."""
import copy
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.knowledge import Knowledge
from app.enrichment import validate_enrichment, read_corpus
from app.retrieval import VectorIndex, VectorRetrieval, input_hash
from app.main import create_app
from app.bilingual import response_language
from test_service import ScriptedProvider, make_service, plan, draft, claim, emit
from test_api import FakeProvider


def test_sidecar_coverage_original_hash_and_vectors(settings):
    k = Knowledge(settings.knowledge_dir)
    cards, chunks = read_corpus(settings.knowledge_dir)
    assert validate_enrichment(k.enrichment, k.glossary, cards, chunks, k.manifest['database_sha256']) == dict(entries=105, items=108, chunks=138, images=262)
    assert hashlib.sha256(k.path.read_bytes()).hexdigest() == 'd6870439358c4dadfd154ef202c58d90284c736d97a85d3bb909e21a013ea7d1'
    index = VectorIndex(k, Settings())
    assert not index.reason and len(index.vectors) == 138
    assert set(k.species) <= k.glossary['species'].keys() and len(k.districts) == 10
    assert '杨树' in k.chunk_incidental_species['shbl:story-066:body-1']


def test_en_sources_cards_images_are_static_without_mutating_chinese(settings):
    k = Knowledge(settings.knowledge_dir)
    original = copy.deepcopy(k.public_card(k.cards[2]))
    context, sources, cards = k.entry_context([2], lang='en')
    assert 'Dragon Juniper' in context and 'Story' in context
    source = sources[1]
    assert source['text'] == source['quote'] == k.enrichment['chunks'][source['chunk_id']]['text_en']
    assert source['text_zh'] == k.chunks(2)[1]['text']
    assert source['citation'] == 'Book of Ancient Trees, p. 76'
    assert source['title_zh'] == '龙柏' and source['type_label'] == 'Story'
    card = cards[0]
    assert card['followups'] == k.enrichment['entries']['2']['followups_en']
    assert card['items'][1]['species'] == ['Dragon Juniper']
    for im in card['images']:
        meta = k.enrichment['images'][im['image_id']]
        assert im['caption'] == meta['caption_en'] and im['display_caption'] == meta['display_caption_en']
    assert original == k.public_card(k.cards[2])


@pytest.mark.parametrize('change', ['hash', 'chunk', 'translation'])
def test_sidecar_fails_closed_on_source_mismatch_or_missing_evidence(settings, change):
    k = Knowledge(settings.knowledge_dir)
    cards, chunks = read_corpus(settings.knowledge_dir)
    asset = copy.deepcopy(k.enrichment)
    if change == 'hash': asset['database_sha256'] = 'wrong'
    elif change == 'chunk': asset['chunks'].pop(chunks[0]['chunk_id'])
    else: asset['chunks'][chunks[0]['chunk_id']]['text_en'] = ''
    with pytest.raises(ValueError, match='Invalid enrichment'):
        validate_enrichment(asset, k.glossary, cards, chunks, k.manifest['database_sha256'])


def test_production_never_parses_geography_at_runtime(settings, monkeypatch):
    import app.offline_enrichment as offline
    def forbidden(*args, **kwargs):
        raise AssertionError('Runtime must read the sidecar')
    monkeypatch.setattr(offline, 'place_terms', forbidden)
    k = Knowledge(settings.knowledge_dir)
    assert len(k.structured({'location': '镇湖'})[0]) == 3
    assert len(k.structured({'location': '木渎'})[0]) == 2
    assert k.catalog('Sanguan')


@pytest.mark.parametrize('query,species,anchors,districts', [
    ('Where is the Buddhist pine in Mudu?', {'罗汉松'}, {'木渎', '木渎古镇'}, set()),
    ('Tell me about the four cypresses Qingqiguguai at Situ Temple', {'圆柏'}, {'清奇古怪', '光福镇司徒庙'}, set()),
    ('Are there any ancient trees on Tiger Hill?', set(), {'虎丘山'}, set()),
    ("Tell me about the Hai'er Lian camellia", {'红山茶', '孩儿莲'}, {'东山雕花楼'}, set()),
    ('Podocarpus macrophyllus in SND', {'罗汉松'}, {'罗汉松'}, {'虎丘区（高新区）'}),
    ('SIP and Wuzhong District', set(), {'工业园区', '吴中区'}, {'工业园区', '吴中区'}),
])
def test_bilingual_entities_keep_canonical_keys(settings, query, species, anchors, districts):
    k = Knowledge(settings.knowledge_dir)
    hints = k.resolve_bilingual_terms(query)
    assert species <= set(hints['matched_species']) and anchors <= set(hints['matched_anchors'])
    assert set(hints['matched_districts']) == districts
    if 'Hai' in query: assert hints['matched_entries'] == [16]
    if 'Qingqiguguai' in query: assert hints['matched_entries'] == [18]
    expanded = k.expand_query(query)
    assert all(a in expanded for a in anchors)
    assert k.expand_query(expanded) == expanded


def test_glossary_boundaries_and_mixed_input(settings):
    k = Knowledge(settings.knowledge_dir)
    assert not k.resolve_bilingual_terms('inside a friendship park')['matched_districts']
    assert not k.resolve_bilingual_terms('a cedarwood souvenir')['matched_species']
    assert k.resolve_bilingual_terms('木渎 Buddhist pine')['matched_species'] == ['罗汉松']
    assert k.resolve_bilingual_terms('Dragon Juniper')['matched_species'] == ['龙柏']
    assert k.expand_query('木渎的罗汉松在哪里？') == '木渎的罗汉松在哪里？'


@pytest.mark.asyncio
async def test_rerank_enriches_documents_without_changing_vector_input(settings):
    k = Knowledge(settings.knowledge_dir)
    c = k.chunks(18)[0]
    before = input_hash(c)
    class Ranker:
        configured = True
        async def rank(self, query, documents):
            assert k.chunk_enrichment[c['chunk_id']]['hook_zh'] in documents[0]
            assert c['text'] in documents[0]
            return [{'index':0,'relevance_score':.9},{'index':1,'relevance_score':.8}], {}
    retrieval = VectorRetrieval(k, settings, reranker=Ranker())
    await retrieval.reorder('unusual ancient trees', [{'chunk_id':c['chunk_id']}, {'chunk_id':k.chunks(2)[0]['chunk_id']}], enrichment='full')
    assert input_hash(k.chunks(18)[0]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('question,ui_lang,expected', [('hello','zh','en'), ('good morning!','zh','en'), ('你好','en','en'), ('您好！','zh','zh')])
async def test_greetings_have_zero_calls(settings, question, ui_lang, expected):
    provider = ScriptedProvider()
    result = await make_service(settings, provider).answer(question, {}, [], emit, ui_lang=ui_lang)
    assert result['lang'] == expected and result['status'] == 'welcome'
    assert result['suggested_questions'] and not provider.inputs
    assert ('Hello!' in result['text']) == (expected == 'en')


@pytest.mark.asyncio
async def test_english_inventory_uses_identical_complete_sql_set(settings):
    provider = ScriptedProvider(plan('吴中区银杏完整清册', '银杏', mode='inventory', lang='en', districts=['Wuzhong District'], filters={'species':'Ginkgo'}))
    svc = make_service(settings, provider)
    result = await svc.answer('How many ginkgo trees are recorded in Wuzhong District?', {}, [], emit, ui_lang='en')
    expected = svc.knowledge.structured({'district':'吴中区','species':'银杏'})[0]
    assert result['lang'] == 'en' and result['text'].startswith('Based on the complete records')
    assert result['structured']['item_count'] == len(expected)
    assert result['structured']['filters'] == {'district':'吴中区','species':'银杏'}
    assert all(c['district'] == 'Wuzhong District' for c in result['cards'])
    assert not svc.retrieval.calls and len(provider.inputs) == 1
    assert json.loads(provider.inputs[0][1])['bilingual_hints']['matched_species'] == ['银杏']


@pytest.mark.asyncio
async def test_english_answer_quotes_images_and_focus_keep_local_bindings(settings):
    provider = ScriptedProvider(plan('金庭镇三官殿龙柏树龄', 'Dragon Juniper', lang='en'),
        draft(claim('The Dragon Juniper at Sanguan Hall is about 1,500 years old.', 'FORGED TRANSLATION', 'S2') |
              {'image_ids':['shbl-img-p013-1','shbl-img-p012-4']}), {'unsupported':[]})
    svc = make_service(settings, provider, [2])
    result = await svc.answer('How old is the Dragon Juniper at Sanguan Hall?', {}, [], emit)
    assert result['lang'] == 'en' and result['status'] == 'answered'
    text = svc.knowledge.enrichment['chunks']['shbl:story-002:body-2']['text_en']
    assert result['sources'][0]['quote'] == text
    assert result['claims'][0]['quotes'] == [{'source_id':'S2','quote':text}]
    assert result['suggested_questions'] and all(q in svc.knowledge.enrichment['entries']['2']['followups_en'] or key.startswith('bridge-entry-') for key,q in zip(result['suggested_question_ids'],result['suggested_questions']))
    assert [im['image_id'] for im in result['claims'][0]['images']] == ['shbl-img-p013-1']
    assert result['focus'] == {'entry_no':2,'item_no':2,'subject':'龙柏'}
    assert 'English-speaking museum docent' in provider.inputs[1][0]
    assert json.loads(provider.inputs[1][1])['subject'] == '龙柏'


@pytest.mark.asyncio
async def test_chinese_followups_exclude_current_question(settings):
    k = Knowledge(settings.knowledge_dir)
    question = k.enrichment['entries']['18']['followups_zh'][0]
    provider = ScriptedProvider(plan(question,'清奇古怪'), draft(claim('文中记载四棵汉柏，手植和赐名说法没有明确依据。')), {'unsupported':[]})
    result = await make_service(settings, provider, [18]).answer(question, {}, [], emit)
    assert result['lang'] == 'zh' and question not in result['suggested_questions']
    assert result['suggested_questions'] and all(q in k.enrichment['entries']['18']['followups_zh'] or key.startswith('bridge-entry-') for key,q in zip(result['suggested_question_ids'],result['suggested_questions']))


@pytest.mark.asyncio
async def test_english_poplar_mentions_never_inherit_ginkgo_images_or_focus(settings):
    provider = ScriptedProvider(plan('震泽永乐寺双杨村杨树传说', '杨树', lang='en'),
        draft(claim('According to local legend, two poplars gave Shuangyang Village its name.')), {'unsupported':[]})
    result = await make_service(settings, provider, [66]).answer('Tell me the story of the poplars in Shuangyang Village.', {}, [], emit)
    assert result['lang'] == 'en' and result['focus'] is None
    assert json.loads(provider.inputs[1][1])['available_images'] == []
    assert json.loads(provider.inputs[1][1])['subject_source_ids'] == ['S1']


def test_response_language_including_code_only_queries():
    assert response_language('吴中203', 'en') == 'en'
    assert response_language('How old is it?', 'zh') == 'en'
    assert response_language('这棵罗汉松有多大？', 'en') == 'zh'


def test_api_catalog_entry_and_message_language_protocol(settings):
    app = create_app(settings, FakeProvider())
    with TestClient(app) as client:
        client.get('/api/session')
        catalog = client.get('/api/catalog?lang=en&species=Ginkgo&district=Wuzhong%20District').json()
        assert catalog['entries'] and all('Ginkgo' in c['species'] for c in catalog['entries'])
        assert 'Ginkgo' in catalog['species']
        entry = client.get('/api/entries/2?lang=en').json()
        assert entry['sources'][1]['title'] == 'Dragon Juniper' and entry['sources'][1]['text_zh']
        cid = client.post('/api/conversations', json={}).json()['id']
        response = client.post(f'/api/conversations/{cid}/messages', json={'text':'你好','lang':'en','request_id':'english-greeting-1'})
        assert 'Hello!' in response.text
        saved = client.get(f'/api/conversations/{cid}/messages').json()[-1]['result']
        assert saved['lang'] == 'en' and saved['suggested_questions']
        assert client.get('/api/catalog?lang=fr').status_code == 422
        assert client.post(f'/api/conversations/{cid}/messages', json={'text':'hi','lang':'fr','request_id':'bad-language-01'}).status_code == 422


@pytest.mark.asyncio
async def test_theme_qualifiers_cannot_disappear_into_unfiltered_inventory(settings):
    provider = ScriptedProvider(plan('吴中区造型奇特或死而复生的古树','',mode='inventory',districts=['吴中区']),
                                draft(claim('灵源寺罗汉松曾遭雷击，后来又发新芽。')), {'unsupported':[]})
    result = await make_service(settings, provider, [11]).answer('吴中区有哪些造型奇特或死而复生的古树？', {}, [], emit)
    assert result['route'] == 'explore' and 'structured' not in result
    assert result['audit']['retrieval']['plan']['filters']['district'] is None


@pytest.mark.asyncio
async def test_precise_thematic_count_is_not_fabricated(settings):
    provider = ScriptedProvider(plan('吴中区死而复生古树总数','',mode='inventory',districts=['吴中区']))
    svc = make_service(settings, provider)
    result = await svc.answer('吴中区死而复生的古树总共有多少项？', {}, [], emit)
    assert result['status'] == 'clarify' and not svc.retrieval.calls and 'structured' not in result


def test_historical_identifiers_and_uncertainty_survive_static_mirror(settings):
    k = Knowledge(settings.knowledge_dir)
    assert '沧553' in k.chunk_enrichment['shbl:story-050:body-1']['text_en']
    assert all(code in k.chunk_enrichment['shbl:story-084:body-1']['text_en'] for code in ('昆027','昆229'))
    assert 'lack clear documentary support' in k.enrichment['entries']['18']['hook_en']
    assert '1127–1279' in k.chunk_enrichment['shbl:story-071:body-1']['text_en']
    assert 'but the narrative' in k.enrichment['entries']['71']['hook_en']


@pytest.mark.asyncio
async def test_english_location_conflict_uses_static_note(settings):
    provider = ScriptedProvider(plan('灵源寺罗汉松在哪里','罗汉松',lang='en'))
    result = await make_service(settings, provider, [11]).answer('Where is the Buddhist pine at Lingyuan Temple?', {}, [], emit)
    assert result['lang'] == 'en' and len(provider.inputs) == 1
    assert result['validation']['method'] == 'recorded_location_conflict'
    assert 'Lingyuan' in result['claims'][0]['text'] and 'Beiwang' in result['claims'][0]['text']
    assert result['claims'][0]['quotes'][0]['quote'] == result['sources'][0]['text']
