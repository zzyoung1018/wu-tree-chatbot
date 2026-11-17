import json
import pytest
from app.knowledge import Knowledge
from app.provider import ModelError
from app.service import ChatService, Plan, UNDERSTAND, ANSWER, VERIFY
from app.districts import supports_district


class FixedEvidence:
    """Deterministic ranked parents; vector recall itself is tested separately."""
    def __init__(self, knowledge, entries):
        self.knowledge, self.entries, self.calls, self.options = knowledge, entries, [], []

    async def search(self, query, nos=None, *, top_entries=8, districts=(), subject='', lang='zh', themes=()):
        self.calls.append((query, nos))
        self.options.append(dict(top_entries=top_entries, districts=districts, subject=subject))
        entries = [no for no in self.entries if any(supports_district(c | {'district': self.knowledge.cards[no]['district']}, districts)
                                                    for c in self.knowledge.chunks(no))][:top_entries]
        return self.knowledge.entry_context(entries, lang), {'mode': 'vector', 'candidate_entries': [], 'selected_entries': entries}


class ScriptedProvider:
    def __init__(self, *responses):
        self.responses, self.inputs, self.options = iter(responses), [], []

    async def json(self, *args, **kwargs):
        self.inputs.append(args)
        self.options.append(kwargs)
        return next(self.responses), {'model': 'test', 'usage': {}}


def plan(query='', subject='', mode='qa', **kwargs):
    return dict(mode=mode, search_query=query, subject=subject, **kwargs)


def claim(text, quote='', source='S1'):
    return {'text': text, 'citations': [{'source_id': source, 'quote': quote}]}


def draft(*claims):
    return {'status': 'answered', 'claims': list(claims)}


def make_service(settings, provider, entries=(11,)):
    knowledge = Knowledge(settings.knowledge_dir)
    return ChatService(knowledge, provider, settings, FixedEvidence(knowledge, entries))


async def emit(stage):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['你好', 'hello', '您好！'])
async def test_pure_greeting_calls_no_model_or_retrieval(settings, question):
    provider = ScriptedProvider()
    svc = make_service(settings, provider)
    result = await svc.answer(question, {}, [], emit)
    assert result['status'] == 'welcome' and not provider.inputs and not svc.retrieval.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('question,subject,mode', [
    ('吴中区有哪些有意思的古树？', '', 'explore'), ('介绍古杨树的故事', '杨树', 'explore'),
    ('孩儿莲是什么树？', '孩儿莲', 'qa'), ('火星村的神奇树在哪里？', '神奇树', 'qa'),
    ('长在岩壁里像卧龙的是哪棵树？', '', 'qa'), ('雕花楼有哪些古树？', '', 'qa'),
])
async def test_understand_allows_ordinary_and_unknown_subjects_to_retrieval(settings, question, subject, mode):
    provider = ScriptedProvider(plan(question, subject, mode), {'status': 'insufficient'})
    svc = make_service(settings, provider, [66])
    def forbidden(*args, **kwargs):
        raise AssertionError('Ordinary questions must not use inventory or catalog gating')
    svc.knowledge.structured = forbidden
    result = await svc.answer(question, {}, [], emit)
    assert provider.inputs[0][0] == UNDERSTAND
    assert provider.options[0]['max_tokens'] == 400
    assert svc.retrieval.calls == [(question, None)]
    assert result['status'] == 'insufficient'
    assert svc.retrieval.options[0]['top_entries'] == (6 if mode == 'explore' else 8)


@pytest.mark.asyncio
async def test_gone_list_uses_full_sql_with_no_default_district(settings):
    provider = ScriptedProvider(plan(mode='inventory', filters={'status': 'gone'}))
    svc = make_service(settings, provider)
    result = await svc.answer('哪些树不存了', {}, [], emit)
    assert result['structured']['item_count'] == 4 and len(result['claims']) == 4
    assert result['structured']['filters'] == {'status': 'gone'}
    assert not svc.retrieval.calls and len(provider.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['列出吴中区所有的树', '列出吴中所有的树'])
async def test_inventory_is_not_limited_by_parent_budget(settings, question):
    provider = ScriptedProvider(plan(mode='inventory', districts=['吴中区'], filters={'district': '吴中'}))
    svc = make_service(settings, provider)
    result = await svc.answer(question, {}, [], emit)
    assert result['structured']['item_count'] == len(svc.knowledge.structured({'district': '吴中区'})[0]) > 8
    assert not svc.retrieval.calls


@pytest.mark.asyncio
async def test_unknown_inventory_species_cannot_become_top_k_answer(settings):
    provider = ScriptedProvider(plan(mode='inventory', filters={'species': '杨树'}))
    svc = make_service(settings, provider)
    result = await svc.answer('列出书中全部杨树并统计数量', {}, [], emit)
    assert result['status'] == 'clarify' and not svc.retrieval.calls


def test_nullable_optional_plan_fields():
    parsed = Plan.model_validate(dict(mode='qa', search_query=None, subject=None, districts=None, filters=None, clarification=None))
    assert parsed.search_query == '' and parsed.districts == [] and parsed.filters.model_dump(exclude_none=True) == {}


@pytest.mark.asyncio
async def test_understand_invalid_json_gets_one_retry(settings):
    provider = ScriptedProvider({'mode': 'invalid'}, plan(mode='inventory', filters={'status': 'gone'}))
    result = await make_service(settings, provider).answer('哪些树已经不存了？', {}, [], emit)
    assert result['structured']['item_count'] == 4
    assert [c['stage'] for c in result['audit']['model_calls']] == ['understand', 'understand_retry']
    provider = ScriptedProvider({'mode': 'invalid'}, {'mode': 'invalid'})
    with pytest.raises(ModelError, match='理解'):
        await make_service(settings, provider).answer('哪些树已经不存了？', {}, [], emit)
    assert len(provider.inputs) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('unsupported', [[], [0], [1]])
async def test_explore_keeps_valid_paragraphs_even_when_first_is_rejected(settings, unsupported):
    provider = ScriptedProvider(plan('吴中区有趣的古树', mode='explore', districts=['吴中区']),
        draft(claim('下面介绍4处有意思的古树。'), claim('三官殿的龙柏树龄约1500年。', source='S2'),
              claim('这片古树还有动人的传说。')), {'unsupported': unsupported})
    svc = make_service(settings, provider, [2, 18, 21, 23, 24, 25, 26, 27])
    result = await svc.answer('吴中区有哪些有意思的古树？', {}, [], emit)
    assert result['status'] == 'answered' and len(result['claims']) == 3 - len(unsupported)
    assert result['validation']['omitted_claims'] == unsupported
    assert len(result['audit']['retrieval']['selected_entries']) <= 6
    assert [c['stage'] for c in result['audit']['model_calls']] == ['understand', 'answer', 'verify']


@pytest.mark.asyncio
async def test_uncited_intro_and_bad_extra_id_do_not_trigger_rewrite(settings):
    provider = ScriptedProvider(plan(subject='龙柏'),
        draft({'text': '不妨看看这棵颇有故事的古树。'},
              claim('三官殿的龙柏树龄约1500年。', source='S2') | {'citations': [{'source_id': 'S999'}, {'source_id': 'S2'}]}),
        {'unsupported': []})
    result = await make_service(settings, provider, [2]).answer('三官殿龙柏的故事', {}, [], emit)
    assert result['status'] == 'answered' and len(provider.inputs) == 3
    assert all(c['source_ids'] == ['S2'] for c in result['claims'])


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [draft({'text': '缺少引用'}), draft(claim('错误引用', source='S999')), {'status': 'bad'}])
async def test_format_repair_uses_same_two_answer_budget(settings, bad):
    provider = ScriptedProvider(plan(subject='龙柏'), bad,
                                draft(claim('龙柏树龄约1500年。', source='S2')), {'unsupported': []})
    result = await make_service(settings, provider, [2]).answer('三官殿龙柏有多大？', {}, [], emit)
    assert result['status'] == 'answered'
    assert len([s for s, _ in provider.inputs if s.startswith(ANSWER)]) == 2


@pytest.mark.asyncio
async def test_hard_age_error_gets_one_revision_then_warm_evidence_fallback(settings):
    bad = draft(claim('香樟树龄约1500年。', source='S1'))
    provider = ScriptedProvider(plan(subject='香樟'), bad, {'unsupported': [0]}, bad, {'unsupported': [0]})
    result = await make_service(settings, provider, [2]).answer('香樟的树龄', {}, [], emit)
    assert result['status'] == 'insufficient' and not result['claims']
    assert '最相近的原文' in result['text'] and result['sources'] and result['cards']
    assert len([s for s, _ in provider.inputs if s.startswith(ANSWER)]) == 2


@pytest.mark.asyncio
async def test_borrowed_intro_cannot_survive_as_only_core(settings):
    bad = draft({'text': '来看看这棵有趣的树。'}, claim('香樟树龄1500年。'))
    provider = ScriptedProvider(plan(subject='香樟'), bad, {'unsupported': [1]}, bad, {'unsupported': [1]})
    result = await make_service(settings, provider, [2]).answer('香樟树龄？', {}, [], emit)
    assert result['status'] == 'insufficient' and not result['claims']


@pytest.mark.asyncio
async def test_explore_all_hard_errors_fall_back_to_objective_cards(settings):
    bad = draft(claim('这棵树是外星人栽种的。'))
    provider = ScriptedProvider(plan(mode='explore'), bad, {'unsupported': [0]}, bad, {'unsupported': [0]})
    result = await make_service(settings, provider, [18, 23]).answer('推荐几处古树故事', {}, [], emit)
    assert result['status'] == 'answered' and result['claims'] and result['validation']['method'] == 'source_guide'
    assert '外星人' not in ''.join(c['text'] for c in result['claims'])


@pytest.mark.asyncio
async def test_citation_fixes_are_bound_to_original_text_and_compact_verifier(settings):
    provider = ScriptedProvider(plan(subject='龙柏'), draft(claim('三官殿龙柏树龄约1500年。')),
        {'unsupported': [], 'citation_fixes': [{'index': 0, 'source_ids': ['S999', 'S2']}, {'index': 8, 'source_ids': ['S1']}]})
    result = await make_service(settings, provider, [2]).answer('三官殿龙柏几岁？', {}, [], emit)
    assert result['claims'][0]['source_ids'] == ['S2']
    assert result['claims'][0]['quotes'] == [{'source_id': 'S2', 'quote': result['sources'][0]['text']}]
    payload = json.loads(provider.inputs[2][1])
    assert all(set(s) == {'id', 'location', 'title', 'type', 'text', 'official_codes', 'review_flags'} for s in payload['sources'])
    assert all('quotes' not in c and 'image_ids' not in c for c in payload['claims'])
    assert 'evidence_text' not in provider.inputs[2][1]


@pytest.mark.asyncio
async def test_body_mention_does_not_get_parent_image_or_focus(settings):
    provider = ScriptedProvider(plan(subject='杨树'),
        draft(claim('传说桥南桥北各有一株高大杨树，因此得名双杨村。') | {'image_ids': ['shbl-img-p047-1']}), {'unsupported': []})
    result = await make_service(settings, provider, [66]).answer('给我讲讲杨树的故事', {}, [], emit)
    assert result['status'] == 'answered' and result['focus'] is None and result['clear_focus']
    payload = json.loads(provider.inputs[1][1])
    assert payload['answer_scope'] == 'body_mentions' and payload['available_images'] == []
    assert not result['claims'][0].get('images')


@pytest.mark.asyncio
@pytest.mark.parametrize('subject,entry', [('清奇古怪', 18), ('孩儿莲', 10)])
async def test_tree_nicknames_keep_book_image_candidates(settings, subject, entry):
    provider = ScriptedProvider(plan(subject=subject), draft(claim('这是书中的古树故事。')), {'unsupported': []})
    await make_service(settings, provider, [entry]).answer(f'介绍{subject}', {}, [], emit)
    assert json.loads(provider.inputs[1][1])['available_images']


@pytest.mark.asyncio
async def test_inline_images_must_belong_to_cited_item(settings):
    provider = ScriptedProvider(plan(subject='龙柏'), draft(claim('龙柏树龄约1500年。', source='S2') |
        {'image_ids': ['shbl-img-p013-1', 'shbl-img-p012-4', 'invented']}), {'unsupported': []})
    result = await make_service(settings, provider, [2]).answer('介绍三官殿龙柏，配上图片', {}, [], emit)
    assert [p['image_id'] for p in result['claims'][0]['images']] == ['shbl-img-p013-1']
    assert result['validation']['omitted_images'] == ['shbl-img-p012-4', 'invented']
    assert result['focus'] == {'entry_no': 2, 'item_no': 2, 'subject': '龙柏'}


@pytest.mark.asyncio
async def test_recorded_location_conflict_still_has_one_call_fast_path(settings):
    provider = ScriptedProvider(plan(subject='罗汉松'))
    result = await make_service(settings, provider).answer('灵源古寺的罗汉松在哪里？', {}, [], emit)
    text = result['claims'][0]['text']
    assert result['status'] == 'answered' and len(provider.inputs) == 1
    assert '北望岭下村' in text and '灵源古寺' in text
    assert result['validation']['method'] == 'recorded_location_conflict'


@pytest.mark.asyncio
async def test_location_conflict_does_not_answer_unrelated_place(settings):
    provider = ScriptedProvider(plan(subject='罗汉松'), {'status': 'insufficient'})
    result = await make_service(settings, provider).answer('火星村的罗汉松在哪里？', {}, [], emit)
    assert result['status'] == 'insufficient' and not result['claims']


@pytest.mark.asyncio
async def test_provider_recovers_from_one_transient_connection_error(settings):
    import httpx
    from app.provider import DeepSeek
    provider = DeepSeek(settings)
    await provider.client.aclose()
    attempts = []
    def transport(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError('connection closed')
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        data, _ = await provider.json('json', '{}')
        assert data == {'ok': True} and len(attempts) == 2
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_provider_error_does_not_expose_credentials(settings):
    import httpx
    from app.provider import DeepSeek
    provider = DeepSeek(settings)
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={'message': 'sensitive upstream body'})))
    try:
        with pytest.raises(ModelError) as exc:
            await provider.json('json', '{}')
        assert exc.value.code == 'credentials' and 'sensitive' not in str(exc.value) and 'test-only-key' not in str(exc.value)
    finally:
        await provider.close()



@pytest.mark.asyncio
async def test_optional_null_fields_do_not_cost_extra_answer_call(settings):
    provider = ScriptedProvider(plan(subject='龙柏'), draft(claim('龙柏树龄约1500年。', source='S2') |
        {'heading': None, 'image_ids': None}), {'unsupported': []})
    result = await make_service(settings, provider, [2]).answer('三官殿龙柏的树龄？', {}, [], emit)
    assert result['status'] == 'answered' and len(provider.inputs) == 3


@pytest.mark.asyncio
async def test_unrelated_parent_is_not_duplicated_in_verifier_and_named_focus_survives(settings):
    provider = ScriptedProvider(plan('木渎的罗汉松在哪里？', '罗汉松'),
        draft(claim('木渎古松园内有一棵罗汉松。', source='S2'), claim('天平山另有罗汉松。', source='S3')),
        {'unsupported': []})
    svc = make_service(settings, provider, [23, 1, 11, 18])
    result = await svc.answer('木渎的罗汉松在哪里？', {}, [], emit)
    assert result['focus'] == {'entry_no': 23, 'item_no': 2, 'subject': '罗汉松'}
    supplied = json.loads(provider.inputs[2][1])['sources']
    assert len(supplied) == len(svc.knowledge.chunks(23)) + len(svc.knowledge.chunks(1))


@pytest.mark.asyncio
async def test_comparison_does_not_use_single_place_conflict_fast_path(settings):
    provider = ScriptedProvider(plan('灵源古寺与三官殿的位置比较'), {'status': 'insufficient'})
    await make_service(settings, provider, [11, 2]).answer('灵源古寺与三官殿分别在哪里？', {}, [], emit)
    assert len(provider.inputs) == 2 and provider.inputs[1][0] == ANSWER


@pytest.mark.asyncio
async def test_two_answer_limit_includes_format_and_core_errors(settings):
    provider = ScriptedProvider(plan(subject='香樟'), draft({'text': '没有引用'}),
        draft(claim('香樟树龄1500年。')), {'unsupported': [0]})
    result = await make_service(settings, provider, [2]).answer('香樟树龄？', {}, [], emit)
    assert result['status'] == 'insufficient' and len(provider.inputs) == 4


@pytest.mark.asyncio
async def test_explore_intro_with_citation_cannot_be_only_survivor(settings):
    bad = draft(claim('不妨看看这几棵有意思的树。'), claim('这棵树是外星人栽种的。'))
    provider = ScriptedProvider(plan(mode='explore'), bad, {'unsupported': [1]}, bad, {'unsupported': [1]})
    result = await make_service(settings, provider, [18]).answer('讲讲古树的故事', {}, [], emit)
    assert result['validation']['method'] == 'source_guide' and len(provider.inputs) == 5


@pytest.mark.asyncio
async def test_missing_photo_never_substitutes_other_item_in_same_card(settings):
    focus = {'entry_no': 23, 'item_no': 2, 'subject': '罗汉松'}
    provider = ScriptedProvider(plan('木渎古松园罗汉松书内照片', '罗汉松', keep_focus=True),
        draft(claim('罗汉松在古松园内。', source='S2'),
              claim('同条目另有广玉兰。', source='S1') | {'image_ids': ['shbl-img-p023-1']}), {'unsupported': []})
    result = await make_service(settings, provider, [23, 1, 11]).answer('配上它的书内照片', {'focus': focus}, [], emit)
    assert result['status'] == 'answered' and result['focus'] == focus
    assert json.loads(provider.inputs[1][1])['available_images'] == []
    assert not any(c.get('images') for c in result['claims'])
    assert result['validation']['omitted_images'] == ['shbl-img-p023-1']


@pytest.mark.asyncio
async def test_unknown_inventory_filter_is_never_silently_dropped(settings):
    provider = ScriptedProvider(plan(mode='inventory', filters={'min_age': 1000}),
                                plan(mode='clarify', clarification='目前不能按树龄区间进行全量统计。'))
    svc = make_service(settings, provider)
    result = await svc.answer('全书超过一千年的古树有多少项？', {}, [], emit)
    assert result['status'] == 'clarify' and 'structured' not in result and not svc.retrieval.calls
