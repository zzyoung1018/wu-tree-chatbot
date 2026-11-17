import asyncio
import hashlib
import json
from pathlib import Path
import time
import shutil
import subprocess
import sys
import tarfile

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.districts import normalize_district, supports_district
from app.knowledge import Knowledge
from app.main import create_app
from app.networking import RateLimiter, client_ip
from app.service import recent_history, UNDERSTAND, ANSWER
from app.offline_enrichment import GENERIC_PLACES, place_terms
from app.store import Store
from test_api import FakeProvider
from test_service import ScriptedProvider, make_service, plan, claim, draft, emit


def test_district_aliases_and_cross_district_body():
    candidate = {'district': '吴江区', 'text': '双杨村的故事'}
    assert not supports_district(candidate, ['吴中区'])
    assert supports_district(candidate, ['吴中区', '吴江区'])
    assert supports_district(candidate, [])
    assert supports_district(candidate | {'district': '姑苏区', 'text': '还提到吴江震泽'}, ['吴江区'])
    assert normalize_district('高新区') == '虎丘区（高新区）'
    assert normalize_district('园区') == '工业园区'
    assert normalize_district('虎丘') == '虎丘'


def test_alias_catalog_and_full_location_statistics(settings):
    k = Knowledge(settings.knowledge_dir)
    rows, _, _ = k.structured({'location': '甪直'})
    # Entry 8 also explicitly belongs to 甪直; the plan's two-item count omitted it.
    assert [r['entry_no'] for r in rows] == [8, 24, 25]
    assert {c['entry_no'] for c in k.catalog('  甪直  ')} == {8, 24, 25}
    assert 29 in {c['entry_no'] for c in k.catalog('穹窿山')}
    assert [c['entry_no'] for c in k.catalog('吴中203')] == [21]
    assert k.catalog(' 银杏 ')
    assert k.public_card(k.cards[21])['official_codes'] == ['吴中203']
    assert '吴中203' not in k.entry_aliases.get(21, [])
    assert '海云院' in k.alias_locations('穹窿山海云院有什么故事')[0]
    assert k.alias_locations('甪直的瑶盛村有哪些树')


def test_place_vocabulary_keeps_named_sites_and_rejects_suffix_noise(settings):
    knowledge = Knowledge(settings.knowledge_dir)
    terms = set(knowledge.place_terms)
    assert {'三官殿', '严家花园', '保圣寺', '司徒庙', '古松园', '石壁永慧禅寺',
            '灵源寺', '灵源古寺', '明月湾', '清奇古怪'} <= terms
    assert not terms & (GENERIC_PLACES | {'13号', '3号', '公堂', '公墓前', '号杂院', '吴中', '高新区'})
    assert all(len(t) >= 2 and not any(c.isdigit() for c in t) for t in terms)
    assert list(knowledge.place_terms) == sorted(terms, key=lambda term: (-len(term), term))
    assert {'东山镇', '东山', '木渎古镇', '木渎', '甪直镇', '甪直', '光福', '金庭'} <= knowledge.area_place_terms
    assert not {'司徒庙', '保圣寺', '古松园', '明月湾', '清奇古怪'} & knowledge.area_place_terms
    for question in ('它是怎么保护全村的？', '它在园子什么位置？', '为什么种在庙前？',
                     '书里种在寺庙里的古树一共有多少项？', '全书在古镇里的银杏有多少棵？'):
        assert not any(p in question for p in terms)
    cleaned, _ = place_terms(['吴中区金庭镇三官殿院内', '马医科23 号杂院',
                             '迎枫桥弄13号', '湖沙公墓前（刘公堂）', '姑苏区虎丘山'])
    assert {'金庭镇', '三官殿', '马医科', '迎枫桥弄', '湖沙公墓', '刘公堂', '虎丘山'} <= cleaned
    assert not {'13号', '23', '号杂院', '公堂', '公墓前'} & cleaned


def test_proxy_login_limits_are_per_user_and_spoofing_is_ignored(settings):
    settings.access_password = SecretStr('internal-test-password')
    with TestClient(create_app(settings, FakeProvider()), client=('172.18.0.2', 1234)) as client:
        for _ in range(10):
            assert client.post('/api/login', json={'password': 'wrong'}, headers={'X-Forwarded-For': '203.0.113.10'}).status_code == 401
        assert client.post('/api/login', json={'password': 'wrong'}, headers={'X-Forwarded-For': '203.0.113.10'}).status_code == 429
        assert client.post('/api/login', json={'password': 'internal-test-password'}, headers={'X-Forwarded-For': 'fake, 203.0.113.11, 172.18.0.3', 'X-Real-IP': '203.0.113.11'}).status_code == 200
    with TestClient(create_app(settings, FakeProvider()), client=('203.0.113.20', 1234)) as client:
        for i in range(10):
            assert client.post('/api/login', json={'password': 'wrong'}, headers={'X-Forwarded-For': f'203.0.113.{i}'}).status_code == 401
        assert client.post('/api/login', json={'password': 'internal-test-password'}, headers={'X-Forwarded-For': '203.0.113.99'}).status_code == 429


@pytest.mark.parametrize('forwarded,real,expected', [
    ('garbage!!, 198.51.100.77, 172.18.0.1', '172.18.0.1', '198.51.100.77'),
    ('garbage!!, 172.18.0.1', '172.18.0.1', '172.18.0.2'),
    ('198.51.100.88, 198.51.100.77, 172.18.0.1', '172.18.0.1', '198.51.100.77'),
    ('198.51.100.88, garbage!!, 172.18.0.1', '172.18.0.1', '172.18.0.2'),
    ('198.51.100.88, , 172.18.0.1', '172.18.0.1', '172.18.0.2'),
    ('172.18.0.1', '172.18.0.1', '172.18.0.2'),
    ('', '172.18.0.1', '172.18.0.2'),
    ('', ' 198.51.100.77 ', '198.51.100.77'),
    ('garbage', 'garbage', '172.18.0.2'),
    ('garbage, 2001:0db8:0:0::77, 172.18.0.1', '172.18.0.1', '2001:db8::77'),
])
def test_forwarded_chain_stops_at_first_untrusted_ip_or_invalid_hop(settings, forwarded, real, expected):
    request = Request({'type': 'http', 'client': ('172.18.0.2', 1234),
                       'headers': [(b'x-forwarded-for', forwarded.encode()), (b'x-real-ip', real.encode())]})
    assert client_ip(request, settings.trusted_proxies) == expected
    request.scope['client'] = ('198.51.100.50', 1234)
    assert client_ip(request, settings.trusted_proxies) == '198.51.100.50'


def test_poisoned_forwarded_prefix_cannot_switch_login_bucket_or_lock_gateway(settings):
    settings.access_password = SecretStr('internal-test-password')
    with TestClient(create_app(settings, FakeProvider()), client=('172.18.0.2', 1234)) as client:
        poison = {'X-Forwarded-For': 'garbage!!, 198.51.100.77, 172.18.0.1', 'X-Real-IP': '172.18.0.1'}
        for _ in range(10):
            assert client.post('/api/login', json={'password': 'wrong'}, headers=poison).status_code == 401
        # Removing the injected prefix must not obtain a fresh rate-limit bucket.
        clean = poison | {'X-Forwarded-For': '198.51.100.77, 172.18.0.1'}
        assert client.post('/api/login', json={'password': 'internal-test-password'}, headers=clean).status_code == 429
        other = poison | {'X-Forwarded-For': 'garbage!!, 198.51.100.78, 172.18.0.1'}
        assert client.post('/api/login', json={'password': 'internal-test-password'}, headers=other).status_code == 200
        # Even a malformed chain without a client has not inherited a poisoned
        # gateway bucket from those ten failed client attempts.
        unknown = poison | {'X-Forwarded-For': 'garbage!!, 172.18.0.1'}
        assert client.post('/api/login', json={'password': 'internal-test-password'}, headers=unknown).status_code == 200


def test_release_package_excludes_platform_metadata_and_private_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / 'scripts').mkdir()
    shutil.copyfile(root / 'scripts/package_release.py', tmp_path / 'scripts/package_release.py')
    for name in ('backend/app/example.py', 'backend/.DS_Store', 'frontend/Thumbs.db',
                 'data/knowledge/._story.db', 'backend/._metadata/hidden.txt',
                 'state/private.sqlite', '.env', '.env.example'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('placeholder\n')
    (tmp_path / '.env').write_text('DEEPSEEK_API_KEY=test-release-api-key\n')
    result = subprocess.run([sys.executable, str(tmp_path / 'scripts/package_release.py')],
                            capture_output=True, text=True, check=True)
    archive = tmp_path / 'release/wu-tree-chatbot.tar.gz'
    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
        assert 'wu-tree-chatbot/backend/app/example.py' in names
        assert 'wu-tree-chatbot/.env.example' in names
        assert all(not any(p in {'.DS_Store', 'Thumbs.db', '.env', 'state'} or p.startswith('._')
                           for p in Path(name).parts) for name in names)
    assert (tmp_path / 'release/SHA256SUMS').read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert 'test-release-api-key' not in result.stdout + result.stderr


def test_limiter_global_expiry_lru_and_session_cap(settings):
    now = [0]
    limiter = RateLimiter(2, 10, max_keys=3, clock=lambda: now[0])
    for key in range(4):
        assert limiter.allow(key)
    assert list(limiter.windows) == [1, 2, 3]
    assert limiter.allow(1) and not limiter.allow(1)
    now[0] = 11
    assert limiter.allow(4) and list(limiter.windows) == [4]
    store = Store(settings.state_dir / 'bounded.sqlite', max_sessions=3)
    tokens = [store.new_session()[0] for _ in range(5)]
    assert store.session(tokens[0]) is None and store.session(tokens[-1])
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 3


def test_origin_allowlist_and_trusted_forwarded_host(settings):
    settings.allowed_origins = ['https://trees.example:8443']
    app = create_app(settings, FakeProvider())
    with TestClient(app, client=('172.18.0.2', 1234)) as client:
        client.get('/api/session')
        assert client.post('/api/conversations', json={}, headers={'Origin': 'https://testserver', 'X-Forwarded-Proto': 'http'}).status_code == 201
        assert client.post('/api/conversations', json={}, headers={'Origin': 'https://trees.example:8443', 'Sec-Fetch-Site': 'cross-site'}).status_code == 201
        headers = {'Origin': 'https://forwarded.example:9443', 'X-Forwarded-Host': 'forwarded.example:9443', 'X-Forwarded-Proto': 'https'}
        assert client.post('/api/conversations', json={}, headers=headers).status_code == 201
        assert client.post('/api/conversations', json={}, headers=headers | {'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/conversations', json={}, headers={'Origin': 'null'}).status_code == 403
    with TestClient(create_app(settings, FakeProvider()), client=('203.0.113.50', 1234)) as client:
        client.get('/api/session')
        assert client.post('/api/conversations', json={}, headers=headers).status_code == 403


def test_cancel_releases_lock_and_sse_does_not_poll_sqlite(settings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, cancelled = Event(), Event()
    app = create_app(settings, FakeProvider())
    reads = []
    original_message = app.state.store.message
    monkeypatch.setattr(app.state.store, 'message', lambda mid: (reads.append(mid), original_message(mid))[1])
    async def slow(question, conversation, history, emit, ui_lang='zh'):
        entered.set()
        try:
            for _ in range(6):
                await emit('核对中')
                await asyncio.sleep(.1)
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr(app.state.service, 'answer', slow)
    with TestClient(app) as client, TestClient(app) as other, ThreadPoolExecutor() as pool:
        client.get('/api/session')
        other.get('/api/session')
        cid = client.post('/api/conversations', json={}).json()['id']
        response = pool.submit(client.post, f'/api/conversations/{cid}/messages', json={'text': '慢问题', 'request_id': 'cancel-test-1'})
        assert entered.wait(2)
        assert other.post(f'/api/conversations/{cid}/cancel', json={}).status_code == 404
        time.sleep(.65)
        assert len(reads) == 1  # begin_answer only; progress notifications never read SQLite.
        start = time.monotonic()
        assert client.post(f'/api/conversations/{cid}/cancel', json={}).status_code == 200
        assert time.monotonic() - start < 1 and cancelled.is_set()
        assert '已停止本次回答' in response.result(timeout=2).text
        assert len(reads) == 2  # one final read for the SSE result.
        messages = client.get(f'/api/conversations/{cid}/messages').json()
        assert messages[-1]['status'] == 'error'
        async def fast(*args, **kwargs):
            return {'status': 'answered', 'text': '下一次回答'}
        monkeypatch.setattr(app.state.service, 'answer', fast)
        assert '下一次回答' in client.post(f'/api/conversations/{cid}/messages', json={'text': '新问题', 'request_id': 'cancel-test-2'}).text
        assert client.request('DELETE', f'/api/conversations/{cid}', json={}).status_code == 200


@pytest.mark.parametrize('location,expected', [
    ('木渎镇', 2), ('同里镇', 1), ('同里古镇', 1), ('甪直古镇', 3),
    ('震泽镇', 4), ('震泽古镇', 4), ('虞山镇', 6), ('虞山街道', 6), ('镇湖街道', 3), ('镇湖', 3),
])
def test_town_suffix_variants_have_complete_item_counts(settings, location, expected):
    k = Knowledge(settings.knowledge_dir)
    values, _, _ = k.structured({'location': location})
    assert len(values) == expected
    if location == '镇湖街道':
        assert [v['entry_no'] for v in values] == [69, 70, 71]


@pytest.mark.parametrize('query,entries', [('三官殿', [2]), ('灵源寺', [11]), ('清奇古怪', [18])])
def test_catalog_supports_child_locations_and_body_aliases(settings, query, entries):
    k = Knowledge(settings.knowledge_dir)
    assert [c['entry_no'] for c in k.catalog(query)] == entries


def test_history_is_compact_and_only_last_three_completed_pairs():
    history = []
    for n in range(5):
        history.extend([{'role': 'user', 'status': 'complete', 'text': str(n)},
                        {'role': 'assistant', 'status': 'complete', 'result': {'claims': [{'text': '古树' * 200}]}}])
    history.extend([{'role': 'user', 'status': 'complete', 'text': 'unfinished'},
                    {'role': 'assistant', 'status': 'processing'}])
    recent = recent_history(history)
    assert [p['question'] for p in recent] == ['2', '3', '4']
    assert all(len(p['answer']) <= 80 for p in recent)


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['配上它的书内照片，并介绍一下。', '有多大年纪了？', '为什么叫这个名字？',
                                     '它是怎么保护全村的？', '这棵罗汉松有多大年纪了？'])
async def test_understand_resolves_omitted_or_mid_sentence_subject(settings, question):
    provider = ScriptedProvider(plan('木渎古松园的罗汉松：' + question, '罗汉松', keep_focus=True),
        draft(claim('罗汉松在木渎古松园。', source='S2') | {'image_ids': ['shbl-img-p024-1']}), {'unsupported': []})
    svc = make_service(settings, provider, [23])
    focus = {'entry_no': 23, 'item_no': 2, 'subject': '罗汉松'}
    history = [{'role': 'user', 'status': 'complete', 'text': '木渎的罗汉松在哪里？'},
               {'role': 'assistant', 'status': 'complete', 'text': '木渎古松园。'}]
    result = await svc.answer(question, {'focus': focus}, history, emit)
    assert result['focus'] == focus and result['status'] == 'answered'
    assert svc.retrieval.calls[0][1] is None and '古松园的罗汉松' in svc.retrieval.calls[0][0]
    payload = json.loads(provider.inputs[0][1])
    assert payload['current_focus'] == focus | {'location': '木渎古镇古松园', 'multi_item_ambiguous': False}
    assert payload['recent_history'][0]['question'] == history[0]['text']
    assert all(im['item_no'] == 2 for c in result['claims'] for im in c.get('images', []))


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['它们真是邓禹种的吗？', '这四棵树有什么故事？'])
async def test_plural_followup_keeps_tree_group(settings, question):
    focus = {'entry_no': 18, 'item_no': 1, 'subject': '圆柏'}
    provider = ScriptedProvider(plan('司徒庙的圆柏：' + question, '圆柏', keep_focus=True),
        draft(claim('司徒庙的圆柏有清奇古怪之称。')), {'unsupported': []})
    svc = make_service(settings, provider, [18])
    result = await svc.answer(question, {'focus': focus}, [], emit)
    assert '司徒庙的圆柏' in svc.retrieval.calls[0][0] and result['focus'] == focus


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['那司徒庙的圆柏呢？', '这棵银杏有多大？', '吴江区的罗汉松有什么故事？', '吴中203有多大？'])
async def test_explicit_new_subject_does_not_inherit_old_focus(settings, question):
    provider = ScriptedProvider(plan(question, keep_focus=False), {'status': 'insufficient'})
    svc = make_service(settings, provider, [23])
    await svc.answer(question, {'focus': {'entry_no': 23, 'item_no': 2, 'subject': '罗汉松'}}, [], emit)
    assert '古松园的罗汉松：' not in svc.retrieval.calls[0][0]
    assert json.loads(provider.inputs[1][1])['current_focus'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('focus', [None, {'entry_no': 23, 'item_no': None, 'subject': '罗汉松、银杏'}])
async def test_understand_can_clarify_ambiguous_reference_without_retrieval(settings, focus):
    provider = ScriptedProvider(plan(mode='clarify', clarification='你指的是哪一棵树？'))
    svc = make_service(settings, provider)
    result = await svc.answer('这棵树多大？', {'focus': focus}, [], emit)
    assert result['status'] == 'clarify' and not svc.retrieval.calls
    current = json.loads(provider.inputs[0][1])['current_focus']
    assert (current is None) if not focus else current['multi_item_ambiguous']


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['书里种在寺庙里的树共有多少项？', '全书树龄超过一千年的树有多少？', '吴中区有多少棵银杏？'])
async def test_unsupported_inventory_scope_clarifies(settings, question):
    provider = ScriptedProvider(plan(mode='clarify', clarification='目前只能按目录中的树／树群项统计。'))
    svc = make_service(settings, provider)
    result = await svc.answer(question, {}, [], emit)
    assert result['status'] == 'clarify' and not svc.retrieval.calls and len(provider.inputs) == 1


@pytest.mark.asyncio
async def test_town_inventory_uses_sql_and_single_site_count_uses_body(settings):
    provider = ScriptedProvider(plan(mode='inventory', filters={'location': '震泽古镇'}))
    svc = make_service(settings, provider)
    result = await svc.answer('震泽古镇有哪些古树？', {}, [], emit)
    assert result['structured']['item_count'] == 4 and not svc.retrieval.calls
    provider = ScriptedProvider(plan(subject='清奇古怪'), draft(claim('司徒庙清奇古怪共有4棵古柏。')), {'unsupported': []})
    result = await make_service(settings, provider, [18]).answer('司徒庙清奇古怪有几棵？', {}, [], emit)
    assert result['route'] == 'qa' and 'structured' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('district', ['高新区', '园区'])
async def test_inventory_normalizes_district_aliases(settings, district):
    provider = ScriptedProvider(plan(mode='inventory', districts=[district], filters={'district': district}))
    result = await make_service(settings, provider).answer(f'列出{district}所有古树', {}, [], emit)
    assert result['structured']['filters']['district'] == normalize_district(district)


@pytest.mark.asyncio
async def test_inventory_preserves_multi_district_plan(settings):
    provider = ScriptedProvider(plan(mode='inventory', districts=['吴中区', '吴江区'], filters={'district': '吴中'}))
    svc = make_service(settings, provider)
    result = await svc.answer('列出吴中区和吴江区所有古树条目', {}, [], emit)
    assert result['structured']['filters']['district'] == ['吴中区', '吴江区']
    assert result['structured']['item_count'] == sum(len(svc.knowledge.structured({'district': d})[0]) for d in ('吴中区', '吴江区'))


@pytest.mark.asyncio
async def test_official_code_hints_preserve_raw_question_at_every_stage(settings):
    question = '吴中203 是什么树？'
    provider = ScriptedProvider(plan('吴中203 石壁永慧禅寺石楠', '石楠'), draft(claim('这是石楠。')), {'unsupported': []})
    svc = make_service(settings, provider, [21])
    await svc.answer(question, {}, [], emit)
    for system, user in provider.inputs:
        data = json.loads(user)
        assert data['question'] == question
        if system == UNDERSTAND:
            assert data['code_hints'][0]['code'] == '吴中203'
        else:
            assert '编号对应' in data['resolved_question']
    assert svc.retrieval.calls[0][1] is None


def test_cancel_finishes_orphaned_processing_message(settings):
    app = create_app(settings, FakeProvider())
    with TestClient(app) as client:
        client.get('/api/session')
        cid = client.post('/api/conversations', json={}).json()['id']
        store = app.state.store
        with store.db() as db:
            owner = db.execute('SELECT owner FROM conversations WHERE id=?', (cid,)).fetchone()[0]
        message, _ = store.begin_answer(owner, cid, 'orphaned-request', '未调度的问题')
        assert client.post(f'/api/conversations/{cid}/cancel', json={}).status_code == 200
        assert store.message(message['id'])['status'] == 'error'
        assert '已停止' in store.message(message['id'])['result']['text']
        assert store.processing_message(owner, cid) is None
