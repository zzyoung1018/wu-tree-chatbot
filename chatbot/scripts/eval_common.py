"""Shared, deterministic evaluation checks and cross-process API usage ledger."""
import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {'rerank': 30_000_000, 'model': 6_000_000}
QUALIFIERS = ('传说', '相传', '据说', '存疑', '无明确', '缺乏', '无从', '无法证实',
              'legend', 'said to', 'reportedly', 'uncertain', 'no clear', 'no definitive',
              'not established', 'no documentary', 'unverified', 'according to tradition')


class BudgetExceeded(RuntimeError):
    pass


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def read_cases(paths):
    cases = []
    for name in paths.split(','):
        for line in Path(name).read_text().splitlines():
            if line.strip():
                case = json.loads(line)
                if not all(case.get(k) for k in ('id', 'set', 'lang', 'question')):
                    raise ValueError('Missing required case fields')
                cases.append(case)
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    return cases


def tokens(call):
    usage = call.get('usage', {})
    return usage.get('total_tokens', usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0))


class Budget:
    def __init__(self, script, path=None):
        self.script = script
        self.path = Path(path or ROOT / 'state/improve-v5/usage.json')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def ledger(self):
        with self.path.with_suffix('.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {'limits': LIMITS, 'calls': []}
            try:
                yield data
            finally:
                data['totals'] = {kind: sum(c['charged_tokens'] for c in data['calls'] if c['kind'] == kind)
                                  for kind in ('model', 'rerank', 'embedding')}
                dump(self.path, data)

    async def call(self, kind, estimate, invoke):
        # Reserve a conservative input/output bound before calling, including
        # failed requests. A crashed process leaves its reservation charged.
        with self.ledger() as data:
            if any(sum(c['charged_tokens'] for c in data['calls'] if c['kind'] == key) >= limit
                   for key, limit in LIMITS.items()):
                raise BudgetExceeded('V5 API budget reached; stop all calls')
            used = sum(c['charged_tokens'] for c in data['calls'] if c['kind'] == kind)
            if kind in LIMITS and used + estimate > LIMITS[kind]:
                raise BudgetExceeded('V5 API budget would be exceeded: ' + kind)
            index = len(data['calls'])
            data['calls'].append({'script': self.script, 'date': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                                  'kind': kind, 'charged_tokens': estimate, 'reserved_tokens': estimate, 'status': 'reserved'})
        try:
            result, usage = await invoke()
        except BaseException:
            with self.ledger() as data:
                data['calls'][index]['status'] = 'failed_usage_unknown'
            raise
        count = tokens(usage)
        with self.ledger() as data:
            row = data['calls'][index]
            row.update(status='complete' if count else 'usage_unknown', usage=usage,
                       charged_tokens=count if count else estimate)
        return result, usage

    def wrap(self, provider=None, embedding=None, reranker=None):
        # Count each HTTP attempt, including internal retries and malformed model
        # output. Logical method wrapping alone misses a provider's first attempt.
        def http_client(client, kind):
            original_post = client.post
            async def post(*args, **kwargs):
                payload = kwargs.get('json', {})
                estimate = 2 * len(json.dumps(payload, ensure_ascii=False)) + payload.get('max_tokens', 0) + 2000
                if kind == 'rerank':
                    inputs = payload.get('input', {})
                    estimate += 2 * len(inputs.get('query', '')) * len(inputs.get('documents', []))
                async def request():
                    response = await original_post(*args, **kwargs)
                    try:
                        raw = response.json().get('usage', {})
                        usage = {k:v for k,v in raw.items() if k in ('total_tokens','prompt_tokens','completion_tokens')
                                 and type(v) is int and v >= 0}
                    except (ValueError, AttributeError, TypeError):
                        usage = {}
                    return response, {'usage': usage, 'http_status': response.status_code}
                response, _ = await self.call(kind, estimate, request)
                return response
            client.post = post
        for obj, kind in ((provider, 'model'), (embedding, 'embedding'), (reranker, 'rerank')):
            if obj is not None and getattr(obj, 'client', None) is not None:
                http_client(obj.client, kind)
        provider = None if provider is not None and getattr(provider, 'client', None) is not None else provider
        embedding = None if embedding is not None and getattr(embedding, 'client', None) is not None else embedding
        reranker = None if reranker is not None and getattr(reranker, 'client', None) is not None else reranker
        original_json = provider.json if provider else None
        original_embed = embedding.embed if embedding else None
        original_rank = reranker.rank if reranker else None
        async def model(system, user, max_tokens=1800):
            return await self.call('model', 2 * (len(system) + len(user)) + max_tokens + 1000,
                                   lambda: original_json(system, user, max_tokens=max_tokens))
        async def embed(texts, text_type='document'):
            return await self.call('embedding', 2 * sum(map(len, texts)) + 1000,
                                   lambda: original_embed(texts, text_type))
        async def rank(query, documents):
            estimate = 2 * (sum(map(len, documents)) + len(query) * len(documents)) + 2000
            return await self.call('rerank', estimate, lambda: original_rank(query, documents))
        if provider:
            provider.json = model
        if embedding:
            embedding.embed = embed
        if reranker:
            reranker.rank = rank


def check_case(case, result, knowledge):
    text = result.get('text', '') + '\n' + '\n'.join(c['text'] for c in result.get('claims', []))
    folded = text.casefold()
    checks = {}
    expect = case.get('expect', {})
    for key in ('status', 'route', 'lang'):
        if key in expect:
            checks[key] = result.get(key) == expect[key]
    for key in ('item_count', 'entry_count'):
        if key in expect:
            checks[key] = result.get('structured', {}).get(key) == expect[key]
    plan = result.get('audit', {}).get('retrieval', {}).get('plan', {})
    if 'districts' in expect:
        checks['districts'] = sorted(plan.get('districts', [])) == sorted(expect['districts'])
    if 'species' in expect:
        species = result.get('structured', {}).get('filters', {}).get('species', [])
        species = [species] if isinstance(species, str) else species
        checks['species'] = set(species) == set(expect['species'])
    if 'answer_any' in case:
        checks['answer_any'] = all(any(w.casefold() in folded for w in group) for group in case['answer_any'])
    if 'answer_none' in case:
        checks['answer_none'] = all(w.casefold() not in folded for w in case['answer_none'])
    if case.get('qualifier'):
        checks['qualifier'] = any(q in folded for q in QUALIFIERS)
    cited = {s['entry_no'] for s in result.get('sources', [])}
    if case.get('gold_entries'):
        gold = set(case['gold_entries'])
        checks['gold_cited'] = bool(cited & gold) if case.get('theme_gold') else gold <= cited
    checks['response_language'] = result.get('lang') == case['lang']
    checks['static_quotes'] = all(s['quote'] == (
        knowledge.chunk_enrichment[s['chunk_id']]['text_en'] if result.get('lang') == 'en' else
        next(c['text'] for c in knowledge.chunks(s['entry_no']) if c['chunk_id'] == s['chunk_id']))
        for s in result.get('sources', []))
    return checks


def check_v4(case, result, knowledge):
    name, expected = case['id'], case.get('expected_entry')
    checks = {'followups': bool(result['suggested_questions']) if result['cards'] else True}
    if expected:
        checks['expected_entry_cited'] = expected in [c['entry_no'] for c in result['cards']]
    if name == 'inventory_en':
        checks['complete_count'] = result.get('structured', {}).get('item_count') == len(knowledge.structured({'district': '吴中区', 'species': '银杏'})[0])
    if name == 'poplar_en':
        checks['incidental_safety'] = result.get('focus') is None and not any(c.get('images') for c in result['claims'])
    if name == 'tiger_en':
        checks['no_huqiu_district_filter'] = result['audit']['retrieval'].get('plan', {}).get('districts') in ([], ['姑苏区'])
    if name == 'theme_zh':
        checks['thematic_exploration'] = result.get('route') == 'explore' and 'structured' not in result
    return checks
