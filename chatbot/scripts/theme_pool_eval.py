"""Compare the theme pool using exactly the saved off-run plans and questions."""
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.knowledge import Knowledge
from app.embeddings import QwenEmbedding
from app.reranking import QwenReranker
from app.retrieval import VectorRetrieval
from app.service import Plan
from eval_common import Budget,dump,read_cases
from retrieval_eval import metrics
async def main():
    out=ROOT/'state/improve-v5/theme-pool-on.json'
    if out.exists():raise SystemExit('Output already exists')
    baseline=json.loads((ROOT/'state/improve-v5/theme-pool-off.json').read_text());cases={c['id']:c for c in read_cases(str(ROOT/'state/improve-v5/holdout-theme-subset.jsonl'))}
    cfg=Settings();k=Knowledge(cfg.knowledge_dir);embedding=QwenEmbedding(cfg);ranker=QwenReranker(cfg);Budget('theme_pool_eval').wrap(embedding=embedding,reranker=ranker);r=VectorRetrieval(k,cfg,embedding,ranker)
    meta={c['chunk_id']:c|{'entry_no':n} for n in k.cards for c in k.chunks(n)};rows=[]
    try:
        for row in baseline['cases']:
            plan=Plan.model_validate(row['plan']);case=cases[row['id']]
            _,audit=await r.search(row['resolved_query'],top_entries=cfg.explore_top_entries if plan.mode=='explore' else cfg.qa_top_entries,districts=plan.districts,subject=plan.subject,lang=plan.lang,rerank_enrichment='off',themes=plan.themes,theme_pool='on')
            measured=metrics(case,audit,meta);pool=set(k.theme_entries(plan.themes,plan.districts));gold=set(case['gold_entries'])
            rows.append({'id':row['id'],'plan':row['plan'],'off':row['arms']['off'],'on':measured,'pool_audit':audit['theme_pool'],'labels':{'precision':len(pool&gold)/len(pool) if pool else 0,'recall':len(pool&gold)/len(gold),'entries':sorted(pool)}})
            dump(out,{'complete':False,'cases':rows})
    finally:await embedding.close();await ranker.close()
    before=sum(x['off']['precision_at_k'] for x in rows)/len(rows);after=sum(x['on']['precision_at_k'] for x in rows)/len(rows)
    original=json.loads((ROOT/'state/improve-v5/ab-holdout.json').read_text())['summary']['off']['holdout_theme']['theme_precision_at_k']
    losses=[x['id'] for x in rows if x['on']['gold_selected_count']<x['off']['gold_selected_count']]
    selected='on' if after-original>=.15 and not losses else 'off'
    dump(out,{'complete':True,'protocol':{'paired_plans_from':'theme-pool-off.json','gate_baseline':'ab-holdout.json/off/holdout_theme','independent_blind_test':False},'cases':rows,'summary':{'same_plan_off_precision':before,'on_precision':after,'phase_a_off_precision':original,'improvement_vs_phase_a':after-original,'lost_cases':losses,'selected_default':selected}})
if __name__=='__main__':asyncio.run(main())
