"""Run JSONL end-to-end cases against current code or the immutable V4 snapshot."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from eval_common import Budget, BudgetExceeded, QUALIFIERS, check_case, check_v4, dump, read_cases, tokens
from eval_legacy import check_zh27


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--code-root', type=Path)
    parser.add_argument('--worker-code-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--concurrency', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--resume', action='store_true')
    return parser, parser.parse_args()


async def run(args):
    sys.dont_write_bytecode = True
    code_root = (args.worker_code_root or ROOT).resolve()
    sys.path.insert(0, str(code_root / 'backend'))
    from app.config import Settings
    from app.embeddings import QwenEmbedding
    from app.knowledge import Knowledge
    from app.provider import DeepSeek
    from app.retrieval import VectorRetrieval
    from app.reranking import QwenReranker
    from app.service import ChatService
    directory = (args.directory or code_root / 'data/knowledge').resolve()
    cfg = Settings(_env_file=ROOT / '.env', knowledge_dir=directory, state_dir=ROOT / 'state/improve-v5/runtime')
    knowledge = Knowledge(directory)
    llm, embedding, reranker = DeepSeek(cfg), QwenEmbedding(cfg), QwenReranker(cfg)
    Budget('live_eval_v5:' + args.out.name).wrap(llm, embedding, reranker)
    service = ChatService(knowledge, llm, cfg, VectorRetrieval(knowledge, cfg, embedding, reranker))
    cases = read_cases(args.cases)
    protocol = {'code_root': str(code_root), 'directory': str(directory), 'cases': cases,
                'qualifier_markers': QUALIFIERS, 'knowledge_sha256': knowledge.manifest['database_sha256'],
                'code_sha256': hashlib.sha256(b''.join(p.read_bytes() for p in sorted((code_root/'backend/app').glob('*.py')))).hexdigest(),
                'sidecars_sha256': hashlib.sha256((directory/'enrichment.json').read_bytes()+(directory/'glossary.json').read_bytes()).hexdigest()}
    # JSON roundtrip normalizes tuples for equality after resume.
    protocol = json.loads(json.dumps(protocol))
    report = json.loads(args.out.read_text()) if args.out.exists() else {'protocol': protocol, 'cases': []}
    if report['protocol'] != protocol:
        raise ValueError('Resume protocol does not match the saved run')
    completed = {c['id'] for c in report['cases']}
    semaphore = asyncio.Semaphore(args.concurrency)
    async def emit(stage):
        pass
    async def one(case):
        if case['id'] in completed:
            return
        async with semaphore:
            row = {'id': case['id'], 'set': case['set'], 'question': case['question']}
            try:
                result = await service.answer(case['question'], {}, [], emit, ui_lang=case.get('ui_lang', case['lang']))
                checks = check_case(case, result, knowledge)
                if case.get('legacy_check') == 'zh27':
                    checks.update(check_zh27(case, result, service, cfg))
                if case.get('legacy_check') == 'v4_8':
                    checks.update(check_v4(case, result, knowledge))
                row.update(result=result, checks=checks, passed=all(checks.values()))
                if case.get('followup') and result.get('focus'):
                    history = [{'role': 'user', 'status': 'complete', 'text': case['question']},
                               {'role': 'assistant', 'status': 'complete', 'result': result}]
                    follow = await service.answer(case['followup'], {'focus': result['focus']}, history, emit)
                    text = '\n'.join(c['text'] for c in follow.get('claims', []))
                    expected = {'sanguan_age': '三官殿', 'split_item': '古松园', 'legend': '邓禹'}[case['id']]
                    item_ids = {s['item_no'] for s in follow.get('sources', [])}
                    ok = follow['status'] == 'answered' and expected in text and item_ids == ({1} if case['id'] == 'legend' else {2})
                    if case['id'] == 'split_item':
                        expected_photos = {im['image_id'] for im in knowledge.cards[23]['images'] if im.get('item_no') == 2}
                        actual = {im['image_id'] for c in follow.get('claims', []) for im in c.get('images', [])}
                        ok = ok and actual <= expected_photos and (bool(actual) if expected_photos else not actual)
                    if case['id'] == 'legend':
                        ok = ok and any(w in text for w in ('依据', '存疑', '无法', '不能', '传说'))
                    row['followup'] = {'result': follow, 'passed': bool(ok), 'question': case['followup']}
                    row['passed'] = row['passed'] and bool(ok)
            except BudgetExceeded:
                dump(args.out, report)
                raise
            except Exception as exc:
                row.update(passed=False, error={'type': type(exc).__name__, 'code': getattr(exc, 'code', None)})
            report['cases'].append(row)
            dump(args.out, report)
            print(json.dumps({'id': case['id'], 'passed': row['passed'], 'checks': row.get('checks'), 'error': row.get('error')}, ensure_ascii=False), flush=True)
    try:
        await asyncio.gather(*(one(c) for c in cases))
    finally:
        await llm.close()
        await embedding.close()
        await reranker.close()
    report['summary'] = {}
    for group in sorted({c['set'] for c in cases} | {'all'}):
        rows = [r for r in report['cases'] if group == 'all' or r['set'] == group]
        audits = [r.get('result', {}).get('audit', {}) for r in rows]
        timed = [a['duration_ms'] for a in audits if 'duration_ms' in a]
        report['summary'][group] = {'n': len(rows), 'passed': sum(r['passed'] for r in rows),
            'timed_cases': len(timed), 'mean_duration_ms': sum(timed) / len(timed) if timed else None,
            'rerank_tokens': sum(tokens(a.get('retrieval', {}).get('rerank', {})) for a in audits),
            'model_tokens': sum(tokens(c) for a in audits for c in a.get('model_calls', [])),
            'failed_ids': [r['id'] for r in rows if not r['passed']]}
    report['complete'] = len(report['cases']) == len(cases)
    dump(args.out, report)
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
    return 0 if all(r['passed'] for r in report['cases']) else 1


def main():
    parser, args = arguments()
    if args.out.exists() and not args.resume:
        parser.error('Output already exists; use a new path or --resume for missing cases only')
    if args.code_root:
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--cases', args.cases,
                   '--out', str(args.out), '--worker-code-root', str(args.code_root.resolve()), '--concurrency', str(args.concurrency)]
        if args.directory:
            command += ['--directory', str(args.directory)]
        if args.resume:
            command.append('--resume')
        return subprocess.run(command, cwd=ROOT, env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'}).returncode
    return asyncio.run(run(args))


if __name__ == '__main__':
    raise SystemExit(main())
