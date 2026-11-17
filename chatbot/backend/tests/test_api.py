import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings


class FakeProvider:
    async def close(self):
        pass

    async def json(self, *args, **kwargs):
        raise AssertionError('This test must not contact an external model')


def test_session_ownership_feedback_and_csrf(settings):
    app = create_app(settings, FakeProvider())
    with TestClient(app) as one, TestClient(app) as two:
        assert one.get('/api/catalog').status_code == 401
        assert one.get('/api/session').json()['authenticated']
        assert two.get('/api/session').json()['authenticated']
        conversation = one.post('/api/conversations', json={}).json()
        cid = conversation['id']
        assert two.get(f'/api/conversations/{cid}/messages').status_code == 404
        assert two.request('DELETE', f'/api/conversations/{cid}', json={}).status_code == 404
        assert one.post('/api/conversations', json={}, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
        assert one.post('/api/conversations', content='{}', headers={'Content-Type':'text/plain'}).status_code == 415
        result = one.post(f'/api/conversations/{cid}/messages', json={'text':'你好','request_id':'test-request-1'})
        assert result.status_code == 200 and 'event: result' in result.text
        items = one.get(f'/api/conversations/{cid}/messages').json()
        assert len(items) == 2 and items[1]['status'] == 'complete'
        mid = items[1]['id']
        assert one.post(f'/api/messages/{mid}/feedback', json={'rating':'bad','reason':'事实不准确','note':'复核'}).status_code == 200
        assert two.post(f'/api/messages/{mid}/feedback', json={'rating':'good'}).status_code == 404
        assert one.get(f'/api/conversations/{cid}/messages').json()[1]['note'] == '复核'
        # Same request ID is idempotent, including its persisted assistant answer.
        one.post(f'/api/conversations/{cid}/messages', json={'text':'你好','request_id':'test-request-1'})
        assert len(one.get(f'/api/conversations/{cid}/messages').json()) == 2
        assert one.post(f'/api/conversations/{cid}/messages', json={'text':'不一样','request_id':'test-request-1'}).status_code == 409
        assert one.get('/api/images/shbl-img-p012-1').headers['content-type'] == 'image/jpeg'
        assert one.post('/api/logout', json={}).status_code == 200
        assert one.get(f'/api/conversations/{cid}/messages').status_code == 401


def test_password_production_guard_and_cookie(settings):
    import pytest
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, app_env='production', access_password='short', deepseek_api_key='do-not-log-this-test-secret')
    assert 'do-not-log-this-test-secret' not in str(exc.value)
    from pydantic import SecretStr
    settings.access_password = SecretStr('a-long-internal-test-password')
    settings.app_env = 'production'
    app = create_app(settings, FakeProvider())
    with TestClient(app) as client:
        assert not client.get('/api/session').json()['authenticated']
        assert client.post('/api/login', json={'password':'wrong'}).status_code == 401
        response = client.post('/api/login', json={'password':'a-long-internal-test-password'})
        assert response.status_code == 200
        cookie = response.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie
        assert client.get('/api/catalog').status_code == 200


def test_server_restart_keeps_history_and_marks_interrupted_jobs(settings):
    from app.store import Store
    store = Store(settings.state_dir / 'app.sqlite')
    token, owner = store.new_session()
    cid = store.create_conversation(owner)['id']
    message, _ = store.begin_answer(owner, cid, 'long-request-1', '问题')
    restored = Store(settings.state_dir / 'app.sqlite')
    assert restored.session(token) == owner
    assert restored.messages(owner,cid)[0]['text'] == '问题'
    assert restored.message(message['id'])['status'] == 'error'
