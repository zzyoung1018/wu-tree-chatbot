"""Location aliases and township stems from recorded metadata."""
import re

from .districts import DISTRICT_ALIASES


GENERIC_PLACES = {
    '寺庙', '古寺', '古镇', '村落', '全村', '园子', '花园', '公园', '古园',
    '山林', '殿堂', '祠堂', '杂院', '书院', '学院', '医院', '小学', '中学',
    '工业园区', '古树',
}
DISTRICT_TERMS = {a for aliases in DISTRICT_ALIASES.values() for a in aliases}
DISTRICT_PREFIXES = tuple(sorted(DISTRICT_TERMS, key=lambda a: (-len(a), a)))
AREA_SHORT_NAMES = {'木渎', '甪直', '东山', '光福', '金庭', '同里', '黎里', '震泽', '盛泽', '千灯', '锦溪'}
# Body-specific names absent from the index metadata. These identify a subject
# for routing/followups only; retrieval still searches every vector candidate.
ITEM_PLACE_NAMES = {
    ('shbl:story-011', 1): ('东山镇灵源古寺', '灵源寺'),
    # Entry 71's body explicitly names 镇湖西京村 and 万佛塔; keep the
    # recorded title/location metadata unchanged.
    ('shbl:story-071', 1): ('镇湖街道西京村', '镇湖西京村万佛塔'),
}
AREA_SUFFIX = re.compile(r'(?:古镇|街道|镇|乡|岛)$')
AREA_PREFIX = re.compile(r'^(.{2,}?(?:街道|古镇|镇|乡|岛))(.+)?$')
LOCAL_PREFIX = re.compile(r'^(.{2,}?(?:村|山(?!庄)|路|巷|街|弄))(.+)$')
DIRECTION = re.compile(r'(?:院内|门口|东侧|西侧|南侧|北侧|对面|内|前|后|旁|侧)$')


def area_stem(value):
    return re.sub(r'(?:古镇|街道|镇|乡)$', '', value.strip())


def place_terms(values):
    terms, areas = set(), set()

    def clean(value):
        value = re.sub(r'\d+(?:-\d+)?\s*号.*$', '', value)
        while True:
            prefix = next((a for a in DISTRICT_PREFIXES if value.startswith(a)), None)
            if not prefix:
                break
            value = value[len(prefix):]
        return DIRECTION.sub('', value)

    def add(value, area=False):
        value = clean(value)
        if (len(value) < 2 or value in GENERIC_PLACES or value in DISTRICT_TERMS
                or not re.fullmatch(r'[\u4e00-\u9fff〇]+', value)):
            return
        terms.add(value)
        if area or AREA_SUFFIX.search(value) or value in AREA_SHORT_NAMES:
            areas.add(value)

    def split(value):
        value = clean(value)
        add(value)
        area = AREA_PREFIX.match(value)
        if area:
            name, rest = area.groups()
            add(name, area=True)
            # Keep an island's full name; town/street abbreviations are useful
            # for questions such as “木渎有哪些古树”.
            if not name.endswith('岛'):
                add(AREA_SUFFIX.sub('', name), area=True)
            if rest:
                split(rest)
            return
        short = next((a for a in sorted(AREA_SHORT_NAMES) if value.startswith(a) and value != a), None)
        if short:
            add(short, area=True)
            split(value[len(short):])
            return
        local = LOCAL_PREFIX.match(value)
        if local:
            add(local[1])
            split(local[2])
        if value.endswith('古村'):
            add(value[:-2])

    for value in values:
        # Remove a spaced house number before whitespace splits it into noise.
        value = re.sub(r'\d+(?:-\d+)?\s*号[^（()）、，,;/；]*', '', value)
        for part in re.split(r'[（()）、，,\s/；;]+', value):
            if part:
                split(part)
    return terms, areas


def base_enrichment(cards, chunks, glossary, database_sha256):
    """Deterministic offline metadata; no model calls or writes to source records."""
    import hashlib
    asset = {'version': 1, 'database_sha256': database_sha256,
             'translation_review': 'offline model translation; automated checks and editorial spot review',
             'entries': {}, 'items': {}, 'chunks': {}, 'images': {}}
    names = sorted(set(glossary.get('species', {})) | {s for c in cards for s in c['species']} | {'杨树'}, key=lambda name: (-len(name), name))
    all_places, all_areas = set(), set()
    for card in cards:
        bodies = [c for c in chunks if c['unit_id'] == card['unit_id']]
        entry_aliases = list(dict.fromkeys(a for c in bodies for a in c.get('search_terms', []) if a not in card['official_codes']))
        terms, areas = place_terms([card['location'], *entry_aliases])
        all_places.update(terms); all_areas.update(areas)
        asset['entries'][str(card['entry_no'])] = {'source_title': card['title'], 'location_aliases': entry_aliases}
        for item in card['items']:
            aliases = [a for c in bodies if c.get('item_no') == item['item_no'] for a in c.get('search_terms', []) if a not in card['official_codes']]
            extra = list(ITEM_PLACE_NAMES.get((card['unit_id'], item['item_no']), ()))
            extra += [key for key, meta in glossary.get('special_terms', {}).items() if meta.get('entry_no') == card['entry_no']]
            terms, areas = place_terms([item['location'], *aliases, *extra])
            all_places.update(terms); all_areas.update(areas)
            township = next((t for t in sorted(areas, key=lambda x: (-len(x), x)) if t.endswith(('镇', '街道', '乡'))), '')
            village = next((t for t in sorted(terms, key=lambda term: (len(term), term)) if t.endswith('村')), '')
            site = item['location'].removeprefix(township).removeprefix(village)
            asset['items'][f"{card['unit_id']}:{item['item_no']}"] = {
                'hierarchy': {'district': card['district'], 'township': township,
                              'township_stem': area_stem(township), 'village': village,
                              'site': site, 'place_terms': sorted(terms), 'area_terms': sorted(areas),
                              'is_area': item['location'] in areas},
                'aliases': list(dict.fromkeys(aliases + extra)),
                'species_en': [glossary.get('species', {}).get(s, {}).get('en', s) for s in item['species']]}
        for c in bodies:
            # Longest names consume their occurrence only; 杨树 cannot match 枫杨树.
            text, mentions = c['text'], []
            for name in names:
                if name in text:
                    mentions.append(name)
                    text = text.replace(name + '树', '').replace(name, '')
            evidence = c['context_header'] + '\n' + c['text']
            asset['chunks'][c['chunk_id']] = {
                'content_hash': c['content_hash'],
                'source_sha256': hashlib.sha256(evidence.encode()).hexdigest(),
                'incidental_species': [s for s in mentions if s not in c.get('species', card['species'])],
                'mentioned_species': mentions,
                'mentioned_districts': [d for d, aliases in DISTRICT_ALIASES.items() if any(a in evidence for a in aliases)],
                'nicknames': [n for n in glossary.get('special_terms', {}) if n in c['text'].replace('、','').replace('，','')],
            }
        for im in card['images']:
            asset['images'][im['image_id']] = {'caption_en': '', 'display_caption_en': ''}
    asset['place_terms'] = sorted(all_places, key=lambda t: (-len(t), t))
    asset['area_place_terms'] = sorted(all_areas)
    return asset
