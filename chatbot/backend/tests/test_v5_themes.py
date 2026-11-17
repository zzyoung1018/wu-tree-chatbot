import copy
import json
from collections import Counter
from pathlib import Path
import pytest
from app.knowledge import Knowledge
from app.config import Settings
from app.enrichment import read_corpus, validate_enrichment
from app.retrieval import VectorRetrieval


def test_themes_are_controlled_with_exact_source_proofs(settings):
    k=Knowledge(settings.knowledge_dir);cards,chunks=read_corpus(settings.knowledge_dir)
    assert len(k.glossary['themes'])<=20
    counts=Counter(t['theme'] for e in k.enrichment['entries'].values() for t in e['themes'])
    assert all(counts[t]>=3 for t in k.glossary['themes'])
    bad=copy.deepcopy(k.enrichment);bad['entries']['1']['themes'][0]['evidence']='不存在的证据'
    with pytest.raises(ValueError,match='exact substring'):
        validate_enrichment(bad,k.glossary,cards,chunks,k.manifest['database_sha256'])
    assert '亲子导览' not in k.glossary['themes']
    assert set(k.resolve_bilingual_terms('带孩子看树')['matched_themes'])=={'形态奇观','花果物产','村落与地名'}

@pytest.mark.asyncio
async def test_theme_pool_requires_three_entries_and_intersects_district(settings):
    settings=Settings(knowledge_dir=settings.knowledge_dir, rerank_enabled=False)
    k=Knowledge(settings.knowledge_dir)
    class Embed:
        configured=True
        async def query(self,q):return [1.0]+[0.0]*(settings.embedding_dimensions-1),{}
    settings.rerank_enabled=False
    r=VectorRetrieval(k,settings,Embed())
    pool=k.theme_entries(['诗文书画中的树'],['常熟市'])
    assert len(pool)>=3
    (_,_,cards),audit=await r.search('常熟诗文',districts=['常熟市'],themes=['诗文书画中的树'],theme_pool='on')
    assert audit['theme_pool']['applied'] and {c['entry_no'] for c in cards}<=set(pool)
    _,audit=await r.search('张家港',districts=['张家港市'],themes=['诗文书画中的树'],theme_pool='on')
    assert not audit['theme_pool']['applied'] and audit['eligible_count']==138


def test_each_sidecar_field_has_a_runtime_consumer_or_offline_declaration(settings):
    k=Knowledge(settings.knowledge_dir)
    offline={'entries':{'source_title','location_aliases','keywords','legacy_followups_zh','legacy_followups_en'},
             'items':set(),'chunks':{'keywords'},'images':set()}
    runtime={'entries':{'title_en','location_en','hook_zh','hook_en','themes','followups','followups_zh','followups_en','review_flags_en'},
             'items':{'aliases','aliases_en','hierarchy','location_en','species_en'},
             'chunks':{'content_hash','source_sha256','text_en','context_header_en','themes','hook_zh','hook_en','synthetic_queries_zh','synthetic_queries_en','incidental_species','mentioned_species','mentioned_districts','nicknames','translation_status'},
             'images':{'caption_en','display_caption_en'}}
    # Archival keywords/legacy questions support offline editorial traceability only.
    for section in runtime:
        actual={key for row in k.enrichment[section].values() for key in row}
        assert actual==runtime[section]|offline[section]
    assert {key for row in k.enrichment['items'].values() for key in row['hierarchy']}=={'district','township','township_stem','village','site','place_terms','area_terms','is_area'}
    assert all(k.enrichment['items'][f"{c['unit_id']}:{i['item_no']}"]['hierarchy']['site'] in k.item_place_terms[(c['unit_id'],i['item_no'])]
               for c in k.cards.values() for i in c['items'] if k.enrichment['items'][f"{c['unit_id']}:{i['item_no']}"]['hierarchy']['site'])
