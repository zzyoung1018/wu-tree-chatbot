"""Local deterministic API fixture for browser_improvements.mjs; never uses model APIs."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.staticfiles import StaticFiles
from app.config import Settings
from app.main import create_app
from app.followups import choose as choose_followups


class NoModels:
    async def close(self):
        pass

    async def json(self, *args, **kwargs):
        raise AssertionError('Browser fixture must never call models')


def create_test_app():
    temporary = tempfile.TemporaryDirectory(prefix='tree-browser-test-')
    settings = Settings(_env_file=None, state_dir=Path(temporary.name), access_password='', deepseek_api_key='',
                        embedding_api_key='', embedding_base_url='')
    app = create_app(settings, NoModels())
    app.state.fixture_directory = temporary
    async def answer(question, conversation, history, emit, ui_lang='zh'):
        if question not in ('你好', 'hello', 'How old is the Dragon Juniper at Sanguan Hall?') and ui_lang != 'en':
            await emit('浏览器测试：正在等待')
            await asyncio.sleep(6 if question == '正常完成' else 30)
        _, sources, cards = app.state.knowledge.entry_context([2], ui_lang)
        result = {'status': 'answered', 'lang': ui_lang, 'text': '',
                'claims': [{'text': 'The Dragon Juniper at Sanguan Hall is about 1,500 years old.' if ui_lang == 'en' else '三官殿龙柏树龄约1500年。', 'source_ids': ['S2']}],
                'sources': sources[1:], 'cards': cards, 'route':'qa', 'focus':{'entry_no':2,'item_no':2,'subject':'龙柏'}}
        rows = choose_followups(app.state.knowledge, result, history, question, question, '龙柏')
        result.update(suggested_questions=[q['text'] for q in rows], suggested_question_ids=[q['id'] for q in rows])
        return result
    app.state.service.answer = answer
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        try:
            async with original_lifespan(app):
                yield
        finally:
            temporary.cleanup()
    app.router.lifespan_context = lifespan
    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[2] / 'frontend/dist', html=True))
    return app
