"""Publication gates, signature freshness, and deterministic translation screening."""
import copy
import csv
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from app.enrichment import apply_translation_overrides, translation_review_state
from app.knowledge import Knowledge

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'


def script(name):
    sys.path.insert(0,str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overrides_fail_on_ambiguous_or_stale_text_and_are_idempotent():
    asset={'chunks':{'c':{'text_en':'old date'}},'entries':{'1':{'followups_en':['old name']}}}
    glossary={'species':{'x':{'en':'old term'}}}
    edits=[{'scope':'chunks','key':'c','field':'text_en','find':'old date','replace':'new date'},
           {'scope':'entries','key':'1','field':'followups_en[0]','find':'old name','replace':'new name'},
           {'scope':'glossary','key':'species/x','field':'en','find':'old term','replace':'new term'}]
    overrides={'version':1,'database_sha256':'hash','reviewed_chunks':['c'],'edits':edits}
    apply_translation_overrides(asset,glossary,overrides,'hash')
    expected=copy.deepcopy((asset,glossary))
    apply_translation_overrides(asset,glossary,overrides,'hash')
    apply_translation_overrides(asset,glossary,overrides,'hash',check=True)
    assert (asset,glossary)==expected
    for bad in ('old date old date','regenerated unrelated text'):
        asset['chunks']['c']['text_en']=bad
        with pytest.raises(ValueError,match='exactly once'):
            apply_translation_overrides(asset,glossary,overrides,'hash')
    with pytest.raises(ValueError,match='source/version'):
        apply_translation_overrides(asset,glossary,overrides,'wrong')


def test_status_never_inherits_stale_review_or_human_signature():
    asset={'chunks':{'c':{'text_en':'translation','source_sha256':'source'}}}
    digest=hashlib.sha256(b'translation').hexdigest()
    overrides={'reviewed_chunks':['c'],'reviewed_text_sha256':{'c':digest}}
    approvals={'chunks':{'c':{'decision':'approved','reviewer':'Test reviewer','date':'2026-01-01',
                             'source_sha256':'source','text_en_sha256':digest}}}
    assert translation_review_state(asset,overrides)['counts']['ai_reviewed']==1
    assert translation_review_state(asset,overrides,approvals)['counts']['human_approved']==1
    asset['chunks']['c']['text_en']='regenerated'
    assert translation_review_state(asset,overrides,approvals)['counts']['machine']==1
    asset['chunks']['c']['text_en']='translation'
    asset['chunks']['c']['source_sha256']='different source'
    assert translation_review_state(asset,overrides,approvals)['counts']['human_approved']==0


def test_published_corrections_static_status_and_all_override_checks(settings):
    k=Knowledge(settings.knowledge_dir)
    overrides=json.loads((settings.knowledge_dir/'translation_overrides.json').read_text())
    apply_translation_overrides(k.enrichment,k.glossary,overrides,k.manifest['database_sha256'],check=True)
    counts=k.enrichment['translation_review']['counts']
    assert counts=={'machine':0,'ai_reviewed':138,'human_approved':0}
    source=k.entry_context([31],'en')[1][0]
    assert source['translation_status']=='ai_reviewed'
    assert '狮012' in source['quote'] and 'Lion 012' not in source['quote']
    assert 'Chinese elm' in k.chunk_enrichment['shbl:story-033:body-1']['text_en']


def test_qa_detects_age_and_code_mutations_and_normalizes_numbers():
    qa=script('translation_qa')
    glossary={'species':{},'culture':{}}
    zh='树龄1500年，编号狮012。20世纪80年代修缮。2023.4.8访问。'
    en='The tree is fifteen hundred years old, code 狮012. Repaired in the 1980s. Visited April 8, 2023.'
    chunk={'text':zh,'chunk_id':'c'}
    meta={'text_en':en,'source_sha256':'hash'}
    assert qa.check_chunk(chunk,meta,glossary)['errors']==[]
    meta['text_en']=en.replace('fifteen hundred','five hundred').replace('狮012','Lion 012')
    errors=qa.check_chunk(chunk,meta,glossary)['errors']
    assert {e['check'] for e in errors}>={'official_codes','arabic_numbers_missing'}
    meta['text_en']=en+' It was planted in 1900.'
    assert any(e['check']=='arabic_numbers_added' for e in qa.check_chunk(chunk,meta,glossary)['errors'])


def test_csv_import_checks_signed_text_and_rejects_all_rows_atomically(settings,tmp_path):
    review=script('export_translation_review')
    for name in ('story.db','enrichment.json','glossary.json','translation_overrides.json'):
        shutil.copy2(settings.knowledge_dir/name,tmp_path/name)
    path=tmp_path/'review.csv'
    assert review.export_review(tmp_path,path)['approved']==0
    with path.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    rows[0].update(decision='通过',reviewer='Test reviewer',review_date='2026-01-01')
    rows[1].update(decision='通过',reviewer='Test reviewer',review_date='2026-01-01',text_en='tampered')
    def write():
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=review.FIELDS);writer.writeheader();writer.writerows(rows)
    before=(tmp_path/'enrichment.json').read_bytes()
    write()
    with pytest.raises(ValueError,match='stale'):
        review.import_review(tmp_path,path)
    assert (tmp_path/'enrichment.json').read_bytes()==before
    assert not (tmp_path/'translation_approvals.json').exists()
    rows[1]['decision']=''
    write()
    result=review.import_review(tmp_path,path)
    assert result['translation_review']['counts']['human_approved']==1
    assert result['translation_review']['counts']['ai_reviewed']==137


def test_english_verifier_reads_original_chinese(settings):
    from app.service import verification_sources, VERIFY_EN
    k=Knowledge(settings.knowledge_dir)
    _,sources,_=k.entry_context([2],lang='en')
    selected=verification_sources(sources,[{'source_ids':[sources[1]['id']]}])
    assert selected and all('text_zh' in s for s in selected)
    assert selected[1]['text_zh']==k.chunks(2)[1]['text']
    assert 'ONLY factual authority' in VERIFY_EN and 'translation_conflicts' in VERIFY_EN


@pytest.mark.asyncio
async def test_translation_conflicts_are_audit_only(settings):
    from test_service import ScriptedProvider,make_service,plan,draft,claim,emit
    p=ScriptedProvider(plan('三官殿龙柏','龙柏',lang='en'),draft(claim('An ancient tree.',source='S2')),
                       {'unsupported':[],'translation_conflicts':[{'source_id':'S2','note':'Original Chinese takes precedence.'},{'source_id':'invented','note':'ignore'}]})
    r=await make_service(settings,p,[2]).answer('Tell me about the Sanguan Hall dragon juniper.',{},[],emit,ui_lang='en')
    assert r['audit']['validation']['translation_conflicts']==[{'source_id':'S2','note':'Original Chinese takes precedence.'}]
    assert 'translation_conflicts' not in r['validation'] and 'translation_conflicts' not in r
