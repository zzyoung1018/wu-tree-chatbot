"""V5 paired retrieval experiment; frozen V4 evidence and shared online planning."""
import argparse
import asyncio
import hashlib
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.embeddings import QwenEmbedding
from app.knowledge import Knowledge
from app.provider import DeepSeek
from app.retrieval import VectorRetrieval
from app.reranking import QwenReranker
from app.service import ChatService, Plan, UNDERSTAND
from eval_common import Budget, dump, read_cases, tokens


def metrics(case, audit, meta):
    hits = audit['ranked_hits']
    parents = list(dict.fromkeys(meta[h['chunk_id']]['entry_no'] for h in hits))
    gold = set(case.get('gold_entries', []))
    selected = set(audit['selected_entries'])
    child_gold = set(case.get('gold_chunks', []))
    if not child_gold and case.get('gold_type'):
        child_gold = {cid for cid, c in meta.items() if c['entry_no'] in gold and c['content_type'] == case['gold_type']}
    ranks = {str(no): parents.index(no) + 1 if no in parents else None for no in sorted(gold)}
    valid = [r for r in ranks.values() if r is not None]
    child_ranks = {cid: next((i for i, h in enumerate(hits, 1) if h['chunk_id'] == cid), None) for cid in sorted(child_gold)}
    vector_ranks = {cid: next((h['vector_rank'] for h in hits if h['chunk_id'] == cid), None) for cid in sorted(child_gold)}
    rank = min(valid) if valid else None
    return {'parent_ranks': ranks, 'rank': rank, 'child_ranks': child_ranks,
            'vector_ranks': vector_ranks, 'hit_at_1': bool(rank and rank <= 1),
            'hit_at_3': bool(rank and rank <= 3), 'mrr': 1 / rank if rank else 0,
            'in_context': bool(gold & selected) if case.get('theme_gold') else bool(gold) and gold <= selected,
            'gold_selected_count': len(gold & selected), 'selected_entries': audit['selected_entries'],
            'context_chunks': audit['context_chunks'], 'top_entries': audit['top_entries'],
            'precision_at_k': len(gold & selected) / min(audit['top_entries'], len(gold)) if case.get('theme_gold') and gold else None,
            'ranking': hits, 'rerank': audit['rerank'], 'embedding': audit['embedding'], 'duration_ms': audit['duration_ms']}


def summarize(rows, arms):
    summary, paired = {}, {}
    for arm in arms:
        summary[arm] = {}
        for group in sorted({r['set'] for r in rows} | {'all'}):
            values = [r['arms'][arm] for r in rows if arm in r.get('arms', {}) and (group == 'all' or r['set'] == group)]
            if not values:
                continue
            themes = [v['precision_at_k'] for v in values if v['precision_at_k'] is not None]
            summary[arm][group] = {'n': len(values), **{key: sum(v[key] for v in values) / len(values)
                for key in ('hit_at_1', 'hit_at_3', 'in_context', 'mrr')},
                'theme_precision_at_k': sum(themes) / len(themes) if themes else None,
                'rerank_tokens': sum(tokens(v['rerank']) for v in values),
                'mean_duration_ms': sum(v['duration_ms'] for v in values) / len(values)}
    for left, right in itertools.combinations(arms, 2):
        pair = {'wins': [], 'losses': [], 'ties': [], 'entered_context': [], 'dropped_context': []}
        for row in rows:
            if not all(a in row.get('arms', {}) for a in (left, right)):
                continue
            a, b = row['arms'][left], row['arms'][right]
            va, vb = (a['gold_selected_count'], b['gold_selected_count']) if row.get('theme_gold') else (a['mrr'], b['mrr'])
            pair['wins' if vb > va else 'losses' if vb < va else 'ties'].append(row['id'])
            if b['in_context'] and not a['in_context']:
                pair['entered_context'].append(row['id'])
            if a['in_context'] and not b['in_context']:
                pair['dropped_context'].append(row['id'])
        paired[right + '_vs_' + left] = pair
    return summary, paired


def decide(rows):
    """Pre-registered rule; development cases never influence arm selection."""
    rows = [r for r in rows if r['set'].startswith('holdout_') and not r.get('skipped')]
    required = {'holdout_zh', 'holdout_en', 'holdout_theme'}
    if (not required <= {r['set'] for r in rows}
            or any(any(a not in r.get('arms', {}) for a in ('off', 'lean', 'full')) for r in rows)):
        return {'status': 'pending', 'reason': 'Complete planned holdout comparison required'}
    results = {}
    for arm in ('lean', 'full'):
        net, lost_zh, fact_hit_delta = 0, [], 0
        for row in rows:
            off, candidate = row['arms']['off'], row['arms'][arm]
            if row['set'] in ('holdout_en', 'holdout_theme'):
                delta = (candidate['gold_selected_count'] - off['gold_selected_count']) if row.get('theme_gold') else int(candidate['in_context']) - int(off['in_context'])
                net += (delta > 0) - (delta < 0)
            if row['set'] == 'holdout_zh' and off['in_context'] and not candidate['in_context']:
                lost_zh.append(row['id'])
            if not row.get('theme_gold'):
                fact_hit_delta += int(candidate['hit_at_1']) - int(off['hit_at_1'])
        results[arm] = {'net_benefit_en_theme': net, 'lost_zh': lost_zh, 'fact_hit_at_1_delta': fact_hit_delta,
                        'eligible': net >= 3 and not lost_zh and fact_hit_delta >= -1}
    choice = 'off'
    if results['lean']['eligible']:
        choice = 'lean'
    if results['full']['eligible'] and (choice == 'off' or results['full']['net_benefit_en_theme'] >= results['lean']['net_benefit_en_theme'] + 2):
        choice = 'full'
    return {'status': 'measured', 'selected_arm': choice, 'arms': results}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=str(ROOT / 'backend/tests/evalsets/dev16.jsonl'))
    parser.add_argument('--arms', default='off,lean,full')
    parser.add_argument('--query-mode', choices=('planned', 'raw'), default='planned')
    parser.add_argument('--directory', type=Path, default=ROOT / 'state/improve-v5/baseline-v4/data/knowledge')
    parser.add_argument('--out', type=Path, default=ROOT / 'state/improve-v5/ab-dev16.json')
    parser.add_argument('--noise-off', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Resume missing cases/arms only; never replace observations')
    parser.add_argument('--theme-pool', choices=('off', 'on'), default='off')
    args = parser.parse_args()
    cases, arms = read_cases(args.cases), args.arms.split(',')
    if set(arms) - {'off', 'lean', 'full'} or len(set(arms)) != len(arms):
        parser.error('Use distinct off,lean,full arms')
    if args.query_mode == 'raw' and any(c['set'] != 'dev_zh' or not c['id'].startswith('dev-') for c in cases):
        parser.error('raw is only allowed for dev16')
    if args.out.exists() and not args.resume:
        parser.error('Output already exists; use a new path or --resume')
    settings = Settings(knowledge_dir=args.directory)
    knowledge = Knowledge(settings.knowledge_dir)
    llm, embedding, reranker = DeepSeek(settings), QwenEmbedding(settings), QwenReranker(settings)
    budget = Budget('retrieval_eval:' + args.out.name)
    budget.wrap(llm, embedding, reranker)
    retrieval = VectorRetrieval(knowledge, settings, embedding, reranker)
    service = ChatService(knowledge, llm, settings, retrieval)
    if not retrieval.status()['ready']:
        raise SystemExit('Vector retrieval is not ready: ' + retrieval.status()['reason'])
    meta = {c['chunk_id']: c | {'entry_no': n} for n in knowledge.cards for c in knowledge.chunks(n)}
    cache_path = ROOT / 'state/improve-v5/plans.json'
    plans = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    code_hash = hashlib.sha256(b''.join((ROOT / 'backend/app' / p).read_bytes() for p in ('service.py', 'bilingual.py', 'knowledge.py'))).hexdigest()
    data_hash = hashlib.sha256((args.directory / 'enrichment.json').read_bytes() + (args.directory / 'glossary.json').read_bytes()).hexdigest()
    protocol = {'query_mode': args.query_mode, 'arms': arms, 'theme_pool': args.theme_pool, 'directory': str(args.directory.resolve()),
                'cases_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'understand_sha256': hashlib.sha256(UNDERSTAND.encode()).hexdigest(), 'code_sha256': code_hash,
                'sidecars_sha256': data_hash, 'noise_off': args.noise_off}
    report = json.loads(args.out.read_text()) if args.out.exists() else {'protocol': protocol, 'cases': []}
    if report['protocol'] != protocol:
        raise SystemExit('Resume protocol differs from recorded run')
    done = {row['id']: row for row in report['cases']}
    try:
        for case in cases:
            row = done.get(case['id'])
            if row is None:
                row = {k: case[k] for k in ('id', 'set', 'question', 'theme_gold') if k in case}
                row['arms'] = {}
                report['cases'].append(row)
            nos = None
            if args.query_mode == 'raw':
                query = case['question']
                matches = knowledge.code_matches(query)
                nos = list(dict.fromkeys(card['entry_no'] for _, card in matches))
                for code, card in matches:
                    query = query.replace(code, card['location'] + '的' + card['title'])
                plan = Plan(search_query=query)
            else:
                key = hashlib.sha256(json.dumps([protocol['understand_sha256'], code_hash, data_hash, case['question'], case.get('ui_lang', case['lang'])], ensure_ascii=False).encode()).hexdigest()
                if key not in plans:
                    planning = await service.plan_question(case['question'], {}, [], case.get('ui_lang', case['lang']))
                    plan = planning['plan']
                    query = service.retrieval_question(case['question'], plan, planning['matched_codes'])
                    plans[key] = {'plan': plan.model_dump(), 'retrieval_question': query, 'calls': planning['calls']}
                    dump(cache_path, plans)
                plan, query = Plan.model_validate(plans[key]['plan']), plans[key]['retrieval_question']
                row['planning_calls'] = plans[key]['calls']
            row.update(plan=plan.model_dump(), resolved_query=query)
            if plan.mode in ('inventory', 'clarify'):
                row['skipped'] = plan.mode
                dump(args.out, report)
                continue
            top = settings.explore_top_entries if plan.mode == 'explore' else settings.qa_top_entries
            for label in arms + (['off_repeat'] if args.noise_off and case['id'].startswith('dev-') else []):
                if label in row['arms']:
                    continue
                arm = 'off' if label == 'off_repeat' else label
                try:
                    _, audit = await retrieval.search(query, nos, candidate_limit=20 if args.query_mode == 'raw' else None,
                        top_entries=top, districts=plan.districts, subject=plan.subject, lang=plan.lang, rerank_enrichment=arm,
                        themes=plan.themes if plan.mode == 'explore' else [], theme_pool=args.theme_pool)
                    if audit['rerank']['status'] != 'applied':
                        raise RuntimeError('Real reranking was not applied')
                    row['arms'][label] = metrics(case, audit, meta)
                    row.pop('error', None)
                    print(json.dumps({'id': case['id'], 'arm': label, 'rank': row['arms'][label]['rank'], 'in_context': row['arms'][label]['in_context']}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    row['error'] = {'arm': label, 'type': type(exc).__name__, 'code': getattr(exc, 'code', None)}
                    dump(args.out, report)
                    raise
                dump(args.out, report)
        report['summary'], report['paired'] = summarize(report['cases'], arms)
        report['decision'] = decide(report['cases']) if args.query_mode == 'planned' else {'status': 'not_applicable'}
        noise = [r for r in report['cases'] if 'off_repeat' in r['arms']]
        report['noise'] = {'n': len(noise), 'changed_cases': [r['id'] for r in noise if
            [h['chunk_id'] for h in r['arms']['off']['ranking']] != [h['chunk_id'] for h in r['arms']['off_repeat']['ranking']]]}
        ranks = [rank for row in report['cases'] for rank in row.get('arms', {}).get('off', {}).get('vector_ranks', {}).values() if rank]
        report['vector_gold_analysis'] = {'ranks': sorted(ranks), 'minimum_candidate_limit_on_observed_gold': max(ranks, default=None),
                                         'note': 'Observed corpus/development gold only; not a safe general limit or a default change.'}
        report['complete'] = True
        dump(args.out, report)
        print(json.dumps({'complete': True, 'decision': report['decision'], 'noise': report['noise']}), flush=True)
    finally:
        await llm.close()
        await embedding.close()
        await reranker.close()


if __name__ == '__main__':
    asyncio.run(main())
