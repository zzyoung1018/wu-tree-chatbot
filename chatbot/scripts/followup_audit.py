"""Vector-only source anchoring audit and fixed-seed bilingual followup sample."""
import argparse,asyncio,hashlib,json,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.knowledge import Knowledge
from app.embeddings import QwenEmbedding
from app.retrieval import VectorIndex
from eval_common import Budget,dump
async def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--sample-only',action='store_true');p.add_argument('--ids',default='');args=p.parse_args()
    if args.out.exists():raise SystemExit('Use a new output path')
    cfg=Settings();k=Knowledge(cfg.knowledge_dir);idx=VectorIndex(k,cfg)
    if idx.reason:raise SystemExit(idx.reason)
    questions=[q|{'entry_no':int(no)} for no,e in k.enrichment['entries'].items() for q in e['followups']]
    if args.sample_only:
        sample=random.Random(20261117).sample(questions,30)
        cases=[{'id':'followup-'+q['id']+'-'+('zh' if i<15 else 'en'),'set':'followup_'+('zh' if i<15 else 'en'),'lang':'zh' if i<15 else 'en','question':q['zh' if i<15 else 'en'],'gold_entries':[q['entry_no']],'gold_chunks':[q['chunk_id']],'expect':{'status':'answered'},'note':'Fixed seed 20261117; generated followups, not independent holdout.'} for i,q in enumerate(sample)]
        args.out.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases));return
    if args.ids:
        selected=set(args.ids.split(','));questions=[q for q in questions if q['id'] in selected]
    embed=QwenEmbedding(cfg);Budget('followup_audit:'+args.out.name).wrap(embedding=embed)
    by_unit={c['unit_id']:n for n,c in k.cards.items()};rows=[]
    protocol={'metadata_sha256':hashlib.sha256((cfg.knowledge_dir/'interaction_metadata.json').read_bytes()).hexdigest(),'method':'Chinese followup query embeddings, no UNDERSTAND/rerank; distinct parent top 5','n':len(questions)}
    try:
        for start in range(0,len(questions),20):
            batch=questions[start:start+20];vectors,usage=await embed.embed([q['zh'] for q in batch],'query')
            for q,vector in zip(batch,vectors):
                hits=idx.search(vector,set(idx.vectors),k=138);parents=list(dict.fromkeys(by_unit[h['unit_id']] for h in hits));rank=parents.index(q['entry_no'])+1
                rows.append({'id':q['id'],'question':q['zh'],'entry_no':q['entry_no'],'rank':rank,'top5':parents[:5],'passed':rank<=5})
            dump(args.out,{'protocol':protocol,'cases':rows,'complete':False});print(f'{len(rows)}/{len(questions)}',flush=True)
    finally:await embed.close()
    dump(args.out,{'protocol':protocol,'cases':rows,'complete':True,'passed':sum(q['passed'] for q in rows),'failed_ids':[q['id'] for q in rows if not q['passed']]})
if __name__=='__main__':asyncio.run(main())
