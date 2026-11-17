import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from .config import Settings
from .knowledge import Knowledge
from .provider import DeepSeek, ModelError
from .embeddings import QwenEmbedding
from .retrieval import VectorRetrieval
from .reranking import QwenReranker
from .service import ChatService
from .store import Store
from .networking import RateLimiter, client_ip, origin_allowed

logger = logging.getLogger('treechat')
COOKIE = 'tree_session'


@dataclass
class Generation:
    stage: str = '准备检索资料'
    version: int = 0
    done: bool = False
    stopped: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def notify(self, stage=None, *, done=False):
        async with self.changed:
            if stage is not None:
                self.stage = stage
            self.done = done
            self.version += 1
            self.changed.notify_all()


class Login(BaseModel):
    password: str = Field(max_length=500)


class Question(BaseModel):
    lang: Literal['zh', 'en'] = 'zh'
    text: str = Field(min_length=1, max_length=1200)
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


class Feedback(BaseModel):
    rating: Literal['good', 'bad']
    reason: str = Field(default='', max_length=100)
    note: str = Field(default='', max_length=1500)


def create_app(settings=None, provider=None, embedding_provider=None, rerank_provider=None):
    cfg = settings or Settings()
    store = Store(cfg.state_dir / 'app.sqlite')
    knowledge = Knowledge(cfg.knowledge_dir)
    llm = provider or DeepSeek(cfg)
    embeddings = embedding_provider or QwenEmbedding(cfg)
    reranker = rerank_provider or QwenReranker(cfg)
    retrieval = VectorRetrieval(knowledge, cfg, embeddings, reranker)
    service = ChatService(knowledge, llm, cfg, retrieval)
    jobs, states = {}, {}
    limits, login_limits = RateLimiter(30, 600), RateLimiter(10, 300)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in list(jobs.values()):
            job.cancel()
        await asyncio.gather(*list(jobs.values()), return_exceptions=True)
        await llm.close()
        await embeddings.close()
        await reranker.close()

    app = FastAPI(title='树语 · 吴中古树问答', version='1.0.0', lifespan=lifespan,
                  docs_url='/api/docs' if cfg.app_env == 'development' else None, redoc_url=None, openapi_url='/api/openapi.json')
    app.state.store, app.state.knowledge, app.state.service = store, knowledge, service

    @app.middleware('http')
    async def headers_and_origin(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not origin_allowed(request, cfg):
                return JSONResponse({'detail': '请求来源不匹配。'}, status_code=403)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '请求必须使用 JSON。'}, status_code=415)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': '内容不存在或无权访问。'}, status_code=404)

    @app.exception_handler(ValueError)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    def owner(request):
        who = store.session(request.cookies.get(COOKIE))
        if not who:
            raise HTTPException(401, '请先进入内部工作区。')
        return who

    def set_cookie(response, token):
        response.set_cookie(COOKIE, token, max_age=14 * 86400, httponly=True, secure=cfg.cookie_secure, samesite='lax', path='/')

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.get('/api/session')
    def session(request: Request, response: Response):
        who = store.session(request.cookies.get(COOKIE))
        locked = bool(cfg.access_password.get_secret_value())
        if not who and not locked:
            token, who = store.new_session()
            set_cookie(response, token)
        return {'authenticated': bool(who), 'requires_password': locked, 'model_configured': bool(cfg.deepseek_api_key.get_secret_value()),
                'counts': knowledge.manifest['counts'] if who else None,
                'as_of': '2024-08', 'model': cfg.deepseek_model if who else None,
                'retrieval': retrieval.status() if who else None}

    @app.post('/api/login')
    def login(body: Login, request: Request, response: Response):
        address = client_ip(request, cfg.trusted_proxies)
        if not login_limits.allow(address):
            raise HTTPException(429, '尝试次数过多，请五分钟后重试。')
        expected = cfg.access_password.get_secret_value()
        if not expected or not hmac.compare_digest(hashlib.sha256(body.password.encode()).digest(), hashlib.sha256(expected.encode()).digest()):
            raise HTTPException(401, '访问口令不正确。')
        token, _ = store.new_session()
        set_cookie(response, token)
        login_limits.clear(address)
        return {'ok': True}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        store.logout(request.cookies.get(COOKIE, ''))
        response.delete_cookie(COOKIE, path='/')
        return {'ok': True}

    @app.get('/api/conversations')
    def conversations(request: Request):
        return store.list_conversations(owner(request))

    @app.post('/api/conversations', status_code=201)
    def create_conversation(request: Request):
        return store.create_conversation(owner(request))

    @app.delete('/api/conversations/{cid}')
    def delete_conversation(cid: str, request: Request):
        store.delete(owner(request), cid)
        return {'ok': True}

    @app.get('/api/conversations/{cid}/messages')
    def messages(cid: str, request: Request):
        return store.messages(owner(request), cid)

    async def generate(mid, question, conversation, history, state, lang='zh'):
        async def emit(stage):
            await state.notify(stage)
        try:
            result = await service.answer(question, conversation, history, emit, ui_lang=lang)
            store.finish(mid, result)
        except ModelError as error:
            errors_en = {'credentials': 'The model credentials have expired or lack access.',
                         'timeout': 'The model timed out. Please try again.',
                         'unconfigured': 'The model is not configured yet.',
                         'vector_unavailable': 'Vector search is temporarily unavailable. Please try again later.',
                         'retrieval_budget': 'Please narrow your question to a tree or place.',
                         'balance': 'The model account has insufficient credit.'}
            result = {'status': 'error', 'lang': lang, 'text': error.message if lang == 'zh' else errors_en.get(error.code, 'The answer could not be completed. Please try again.'), 'error_code': error.code}
            if error.audit:
                result['audit'] = error.audit
            store.finish(mid, result, status='error')
            logger.warning('model_error message_id=%s code=%s', mid, error.code)
        except asyncio.CancelledError:
            message = '已停止本次回答，可重新提问。' if state.stopped else '服务重启中断了回答，请重新发送问题。'
            if lang == 'en':
                message = 'Answer stopped. You can ask again.' if state.stopped else 'The service restarted. Please send your question again.'
            store.finish(mid, {'status': 'error', 'text': message, 'lang': lang}, status='error')
            raise
        except Exception:
            # Do not log prompt bodies, API headers, or upstream response bodies.
            logger.error('answer_failed message_id=%s', mid)
            store.finish(mid, {'status': 'error', 'lang': lang, 'text': '这次回答未能完成，请重试。' if lang == 'zh' else 'The answer could not be completed. Please try again.'}, status='error')
        finally:
            await state.notify(done=True)
            states.pop(mid, None)
            jobs.pop(mid, None)

    @app.post('/api/conversations/{cid}/cancel')
    async def cancel(cid: str, request: Request):
        mid = store.processing_message(owner(request), cid)
        if mid and (job := jobs.get(mid)):
            state = states[mid]
            state.stopped = True
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            # A task cancelled before its coroutine starts cannot run finally.
            if not state.done:
                store.finish(mid, {'status': 'error', 'text': '已停止本次回答，可重新提问。'}, status='error')
                await state.notify(done=True)
                jobs.pop(mid, None)
                states.pop(mid, None)
        elif mid:
            store.finish(mid, {'status': 'error', 'text': '已停止本次回答，可重新提问。'}, status='error')
        return {'ok': True}

    @app.post('/api/conversations/{cid}/messages')
    async def send(cid: str, body: Question, request: Request):
        who = owner(request)
        question = body.text.strip()
        if not question:
            raise HTTPException(422, '请输入问题。')
        conversation = store.conversation(who, cid)
        history = store.messages(who, cid)
        if not limits.allow(who, consume=False):
            raise HTTPException(429, '问题发送较快，请稍后继续。')
        message, created = store.begin_answer(who, cid, body.request_id, question)
        mid = message['id']
        if created:
            limits.allow(who)
            states[mid] = Generation(stage='准备检索资料' if body.lang == 'zh' else 'Preparing to search the book')
            jobs[mid] = asyncio.create_task(generate(mid, question, conversation, history, states[mid], body.lang))
        state = states.get(mid)

        async def stream():
            version = -1
            yield 'event: accepted\ndata: ' + json.dumps({'message_id': mid}) + '\n\n'
            while True:
                if state is None or state.done:
                    item = await asyncio.to_thread(store.message, mid)
                    yield 'event: result\ndata: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
                    return
                if state.version != version:
                    version = state.version
                    yield 'event: progress\ndata: ' + json.dumps({'stage': state.stage}, ensure_ascii=False) + '\n\n'
                try:
                    async with state.changed:
                        await asyncio.wait_for(state.changed.wait_for(lambda: state.version != version or state.done), timeout=5.0)
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.post('/api/messages/{mid}/feedback')
    def feedback(mid: str, body: Feedback, request: Request):
        store.feedback(owner(request), mid, body.rating, body.reason, body.note)
        return {'ok': True}

    @app.get('/api/catalog')
    def catalog(request: Request, q: str = '', district: str = '', species: str = '', lang: Literal['zh', 'en'] = 'zh'):
        owner(request)
        if max(len(q), len(district), len(species)) > 100:
            raise HTTPException(422, '筛选文字过长。')
        return {'entries': knowledge.catalog(q, district, species, lang),
                'districts': knowledge.districts if lang == 'zh' else [knowledge.glossary['districts'][d]['en'] for d in knowledge.districts],
                'species': knowledge.species if lang == 'zh' else [knowledge.species_label(s) for s in knowledge.species]}

    @app.get('/api/entries/{no}')
    def entry(no: int, request: Request, lang: Literal['zh', 'en'] = 'zh'):
        owner(request)
        card = knowledge.cards.get(no)
        if not card or not knowledge.allowed(card):
            raise KeyError('entry')
        return {'card': knowledge.public_card(card, lang), 'sources': [knowledge.source(c, f'S{i+1}', card, lang) for i, c in enumerate(knowledge.chunks(no))]}

    @app.get('/api/images/{image_id}')
    def image(image_id: str, request: Request):
        owner(request)
        path = knowledge.image_path(image_id)
        if not path:
            raise KeyError('image')
        return FileResponse(path, media_type='image/jpeg')

    return app
