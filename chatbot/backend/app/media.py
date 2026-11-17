"""Offer book images by recorded entry/item, then resolve model-selected IDs."""
from .bilingual import subject_matches


def image_candidates(cards, sources, *, body_mentions=False, excluded_units=(), subject='', species_groups=None):
    # A photo of the indexed tree is not a photo of another species mentioned
    # incidentally in its chapter. We have no image-level annotation for those.
    if body_mentions:
        return []
    inventory = []
    for card in cards:
        if card['unit_id'] in excluded_units:
            continue
        for picture in card['images']:
            item_no = picture.get('item_no')
            related = [s for s in sources if s['unit_id'] == card['unit_id']
                       and (item_no is None or s['item_no'] == item_no)]
            if not related:
                continue
            item = next((i for i in card['items'] if i['item_no'] == item_no), None)
            if subject and item and not subject_matches(subject, item.get('species_zh', item['species']), species_groups):
                continue
            label = (f"{item['location']} · {'、'.join(item['species'])} · 书内配图" if item
                     else f"{card['location']} · {card['title']}条目配图")
            inventory.append(picture | {'entry_no': card['entry_no'], 'unit_id': card['unit_id'],
                'display_caption': picture.get('display_caption') or picture.get('caption') or label,
                'source_ids': [s['id'] for s in related]})
    return inventory


def attach_images(claims, inventory):
    """Never accept a model-written URL or a different tree item's photograph."""
    by_id = {picture['image_id']: picture for picture in inventory}
    seen, omitted = set(), []
    for claim in claims:
        pictures = []
        for image_id in claim.pop('image_ids', []):
            picture = by_id.get(image_id)
            if picture is None or not set(claim['source_ids']).intersection(picture['source_ids']):
                omitted.append(image_id)
                continue
            if image_id in seen:
                continue
            seen.add(image_id)
            pictures.append(picture)
        if pictures:
            claim['images'] = pictures
    return omitted
