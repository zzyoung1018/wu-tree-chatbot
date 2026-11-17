"""Three real-model mutations in a temporary knowledge copy; never publish them."""
import asyncio,copy,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.knowledge import Knowledge
from app.enrichment import translation_review_state
from app.provider import DeepSeek
from app.service import VERIFY,VERIFY_EN,verification_sources
from eval_common import Budget,dump
async def main():
    out=ROOT/'state/improve-v5/translation-mutations.json'
    if out.exists():raise SystemExit('Output already exists; do not rerun to improve a score')
    cfg=Settings();base=json.loads((cfg.knowledge_dir/'enrichment.json').read_text());llm=DeepSeek(cfg);Budget('translation_mutation_eval').wrap(provider=llm);rows=[]
    mutations=[('age',2,'shbl:story-002:body-2','1,500','9,999','The Dragon Juniper at Sanguan Hall is about 9,999 years old.'),
    ('person',55,'shbl:story-055:body-1','the educator Zhang Jiyou planted','the poet Li Bai planted','Li Bai planted the deodar cedar at Huangfeiji in 1924.'),
    ('hedge',3,'shbl:story-003:body-1','Local villagers say this tree was planted in the Tianji 4th year of Sun Hao of the Wu state during the Three Kingdoms period, more than 1,800 years ago, but judging by the current tree-age survey results, this legend can clearly only serve as mealtime conversation.','Documentary evidence establishes that this tree was planted in the Tianji 4th year of Sun Hao of the Wu state during the Three Kingdoms period, more than 1,800 years ago.','Documentary evidence establishes that the Yinshan Island camphor was planted in Sun Hao’s Tianji 4th year.')]
    try:
        with tempfile.TemporaryDirectory(prefix='v5-translation-mutations-') as temp:
            directory=Path(temp)
            for name in ('story.db','manifest.json','glossary.json'):shutil.copyfile(cfg.knowledge_dir/name,directory/name)
            for kind,no,cid,old,new,claim in mutations:
                asset=copy.deepcopy(base);text=asset['chunks'][cid]['text_en'];assert text.count(old)==1
                asset['chunks'][cid]['text_en']=text.replace(old,new,1);translation_review_state(asset)
                dump(directory/'enrichment.json',asset);k=Knowledge(directory)
                _,sources,_=k.entry_context([no],lang='en');s=next(s for s in sources if s['chunk_id']==cid)
                claims=[{'heading':'','text':claim,'source_ids':[s['id']]}]
                response,usage=await llm.json(VERIFY+'\n'+VERIFY_EN,json.dumps({'question':'Is this statement supported by the book?','claims':claims,'sources':verification_sources([s],claims),'lang':'en','answer_scope':'direct'},ensure_ascii=False),max_tokens=1000)
                passed=0 in response.get('unsupported',[]) and any(c.get('source_id')==s['id'] for c in response.get('translation_conflicts',[]))
                rows.append({'kind':kind,'chunk_id':cid,'changed_from':old,'changed_to':new,'claim':claim,'verdict':response,'usage':usage,'passed':passed});dump(out,{'complete':False,'cases':rows});print(kind,passed,flush=True)
    finally:await llm.close()
    dump(out,{'complete':True,'method':'Real VERIFY calls on English-source age/person/hedge mutations in a temporary knowledge directory; original Chinese unchanged','passed':sum(x['passed'] for x in rows),'n':3,'cases':rows})
if __name__=='__main__':asyncio.run(main())
