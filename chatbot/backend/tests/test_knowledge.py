from app.knowledge import Knowledge
from app.service import Draft, validate_claims


def test_explicit_entry_context_preserves_child_order(settings):
    _, sources, cards = Knowledge(settings.knowledge_dir).entry_context([2])
    assert [s['chunk_id'] for s in sources] == ['shbl:story-002:body-1', 'shbl:story-002:body-2']
    assert [c['entry_no'] for c in cards] == [2]


def test_full_set_gone_includes_partial_parent(settings):
    rows, sources, cards = Knowledge(settings.knowledge_dir).structured({'status': 'gone'})
    assert [(r['entry_no'], r['item_no']) for r in rows] == [(1, 2), (14, 1), (29, 1), (34, 1)]
    assert len(cards) == 4
    assert sources[0]['item_no'] == 2
    assert sources[0]['chunk_id'] == 'shbl:story-001:body-3'


def test_structured_total_is_items_not_individual_trees(settings):
    rows, _, cards = Knowledge(settings.knowledge_dir).structured({})
    assert len(rows) == 108 and len(cards) == 105


def test_item_location_and_citation_preserved(settings):
    _, sources, _ = Knowledge(settings.knowledge_dir).entry_context([23])
    assert [(s['item_no'], s['location']) for s in sources] == [(1, '木渎古镇严家花园'), (2, '木渎古镇古松园')]
    assert sources[1]['title'] == '罗汉松'


def test_confirmed_corpus_is_available_without_a_review_mode(settings):
    knowledge = Knowledge(settings.knowledge_dir)
    assert len(knowledge.catalog()) == 105
    assert knowledge.entry_context([2])[1]
    assert len(knowledge.structured({'status': 'gone'})[0]) == 4
    assert knowledge.image_path('shbl-img-p012-1') is not None
    assert all(c['review_status'] == 'verified' for c in knowledge.cards.values())


def test_sql_and_image_paths_do_not_accept_arbitrary_input(settings):
    knowledge = Knowledge(settings.knowledge_dir)
    assert knowledge.structured({'location': "' OR 1=1 --"})[0] == []
    assert knowledge.image_path('../../.env') is None


def test_citations_are_soft_cleaned_and_quotes_remain_server_owned(settings):
    _, sources, _ = Knowledge(settings.knowledge_dir).entry_context([2])
    claim = {'text': '下面介绍4处有趣的古树。', 'citations': [{'source_id': 'S2'}, {'source_id': 'S999'}]}
    bound, errors = validate_claims(Draft(status='answered', claims=[claim]), sources)
    assert not errors and bound[0]['source_ids'] == ['S2']
    assert bound[0]['quotes'] == [{'source_id': 'S2', 'quote': sources[1]['text']}]
    assert validate_claims(Draft(status='answered', claims=[claim | {'citations': [{'source_id': 'S999'}]}]), sources)[1] == ['missing_sources']
    # Legacy quote fields cannot replace the database text; factual numbers go to VERIFY.
    claim = claim | {'text': '树龄1502年。', 'citations': [{'source_id': 'S2', 'quote': '伪造原文'}]}
    bound, errors = validate_claims(Draft(status='answered', claims=[claim]), sources)
    assert not errors and bound[0]['quotes'][0]['quote'] == sources[1]['text']
