from app.knowledge import Knowledge
from app.media import image_candidates, attach_images


def test_only_matching_item_photos_are_offered(settings):
    _, sources, cards = Knowledge(settings.knowledge_dir).entry_context([2, 23])
    inventory = image_candidates(cards, [s for s in sources if s['item_no'] == 2])
    assert {p['image_id'] for p in inventory} == {'shbl-img-p013-1', 'shbl-img-p013-2'}
    assert all('龙柏' in p['display_caption'] for p in inventory)
    assert not image_candidates(cards, sources, body_mentions=True)


def test_image_ids_must_match_the_paragraph_sources_and_are_not_repeated(settings):
    _, sources, cards = Knowledge(settings.knowledge_dir).entry_context([2])
    inventory = image_candidates(cards, sources)
    claims = [
        {'source_ids': ['S2'], 'image_ids': ['shbl-img-p013-1', 'shbl-img-p012-4', 'https://fake/image.jpg']},
        {'source_ids': ['S2'], 'image_ids': ['shbl-img-p013-1']},
    ]
    omitted = attach_images(claims, inventory)
    assert omitted == ['shbl-img-p012-4', 'https://fake/image.jpg']
    picture = claims[0]['images'][0]
    assert picture['image_id'] == 'shbl-img-p013-1' and picture['url'] == '/api/images/shbl-img-p013-1'
    assert picture['printed_page'] == 13 and picture['item_no'] == 2
    assert not claims[1].get('images') and not any('image_ids' in c for c in claims)


def test_original_caption_and_credit_are_preserved(settings):
    _, sources, cards = Knowledge(settings.knowledge_dir).entry_context([1])
    painting = next(p for p in image_candidates(cards, sources) if p['kind'] == 'painting')
    assert painting['display_caption'] == painting['caption']
    assert painting['credit'] == '上海博物馆藏'
