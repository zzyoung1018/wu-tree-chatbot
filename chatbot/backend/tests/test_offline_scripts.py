import importlib.util
import json
from pathlib import Path
import pytest

ANALYSIS = Path(__file__).resolve().parents[3] / 'analysis'
pytestmark = pytest.mark.skipif(not ANALYSIS.is_dir(), reason='Offline pipeline is not shipped in the application bundle')


def module(name):
    spec = importlib.util.spec_from_file_location(name, ANALYSIS / 'scripts' / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_offline_expansion_sidebar_ownership_and_integrity(tmp_path):
    builder = module('build_story_chunks')
    loader = module('load_story_db')
    stories = [e for e in builder.read_jsonl(ANALYSIS / 'pilot/stories.jsonl') if e['entry_no'] == 23]
    indexes = [e for e in builder.read_jsonl(ANALYSIS / 'pilot/index_story.jsonl') if e['entry_no'] == 23]
    stories[0]['sidebars'] = [{'text': '志愿者走访记录：罗汉松的树荫。', 'printed_pages': [92]}]
    builder.OVERRIDES[23]['sidebar_items'] = {0: 2}
    builder.write_jsonl(tmp_path / 'stories.jsonl', stories)
    builder.write_jsonl(tmp_path / 'index.jsonl', indexes)
    args = [tmp_path / name for name in ('stories.jsonl', 'index.jsonl', 'chunks.jsonl', 'entries.jsonl')]
    with pytest.raises(ValueError):
        builder.build(*args)
    builder.build(*args, strict_counts=False)
    cards = builder.read_jsonl(args[3])
    chunks = builder.read_jsonl(args[2])
    sidebar = next(c for c in chunks if c['content_type'] != 'story')
    assert sidebar['site_name'] == '木渎古镇古松园' and sidebar['species'] == ['罗汉松']
    assert next(c for c in cards[0]['children'] if c['chunk_id'] == sidebar['chunk_id'])['item_no'] == 2
    db = tmp_path / 'story.db'
    loader.build(ANALYSIS / 'schema/story_db.sql', args[3], args[2], db, strict_counts=False)
    original = db.read_bytes()
    chunks[0]['text'] += '篡改未更新哈希'
    builder.write_jsonl(args[2], chunks)
    with pytest.raises(ValueError, match='hash'):
        loader.build(ANALYSIS / 'schema/story_db.sql', args[3], args[2], db, strict_counts=False)
    assert db.read_bytes() == original


def test_generic_location_conflict_does_not_invent_lingyuan(tmp_path):
    builder = module('build_story_chunks')
    stories = [e for e in builder.read_jsonl(ANALYSIS / 'pilot/stories.jsonl') if e['entry_no'] == 96]
    indexes = [e for e in builder.read_jsonl(ANALYSIS / 'pilot/index_story.jsonl') if e['entry_no'] == 96]
    indexes[0]['heads'][0]['location'] = '另一处地点'
    args = [tmp_path / name for name in ('stories.jsonl', 'index.jsonl', 'chunks.jsonl', 'entries.jsonl')]
    builder.write_jsonl(args[0], stories)
    builder.write_jsonl(args[1], indexes)
    builder.build(*args, strict_counts=False)
    note = next(f['note'] for f in builder.read_jsonl(args[3])[0]['review_flags'] if f['code'] == 'location_conflict')
    assert '另一处地点' in note and '灵源古寺' not in note
