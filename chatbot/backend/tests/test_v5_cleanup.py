import copy
import shutil
import sys
from pathlib import Path
import pytest
from app.knowledge import Knowledge
from app.service import UNDERSTAND


def test_missing_sidecar_fails_closed_unless_fixture_explicitly_opts_in(settings, tmp_path):
    for name in ('story.db', 'manifest.json'):
        shutil.copyfile(settings.knowledge_dir / name, tmp_path / name)
    with pytest.raises(FileNotFoundError, match='enrichment'):
        Knowledge(tmp_path)
    assert len(Knowledge(tmp_path, allow_fallback=True).cards) == 105


def test_model_hierarchy_must_be_in_recorded_location_or_alias():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
    from build_enrichment import merge_entry
    card={'entry_no':1,'unit_id':'test','items':[{'item_no':1,'location':'东山镇甲村寺院'}],'images':[]}
    asset={'entries':{'1':{}},'items':{'test:1':{'aliases':['旧村名'], 'hierarchy':{'township':'东山镇','site':'寺院'}}}}
    model={'entry':{},'items':{'1':{'location_en':'Temple','aliases_en':[], 'hierarchy':{'township':'不存在镇','village':'旧村名','site':'伪造寺院'}}}}
    merge_entry(asset, card, [], model)
    assert asset['items']['test:1']['hierarchy']=={'township':'东山镇','village':'旧村名','site':'寺院'}


def test_glossary_supplies_disambiguation_and_nicknames(settings):
    k=Knowledge(settings.knowledge_dir)
    hints=k.resolve_bilingual_terms('Tiger Hill')
    assert any(t.get('not_district') for t in hints['terms'])
    assert {'清奇古怪','孩儿莲'} <= k.named_subjects
    assert 'Buddhist pine=' not in UNDERSTAND and 'Mudu=' not in UNDERSTAND
