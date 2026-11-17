"""Validate static sidecars before exposing translated evidence."""
import hashlib
import json
import re
from collections import Counter


def validate_interactions(data, card, chunks, glossary):
    by_id = {c['chunk_id']: c for c in chunks}
    themes, questions = data['themes'], data['followups']
    if not 1 <= len(themes) <= 3 or len({t['theme'] for t in themes}) != len(themes):
        raise ValueError('Need 1–3 distinct controlled themes')
    if not 4 <= len(questions) <= 6 or len({q['id'] for q in questions}) != len(questions):
        raise ValueError('Need 4–6 distinct followup IDs')
    for row in themes + questions:
        chunk = by_id.get(row['chunk_id'])
        if not chunk or not row['evidence'].strip() or row['evidence'] not in chunk['text']:
            raise ValueError('Evidence must be an exact substring of its own source chunk')
    for row in themes:
        if row['theme'] not in glossary['themes']:
            raise ValueError('Unknown controlled theme')
    for q in questions:
        if (q['item_no'] != by_id[q['chunk_id']]['item_no'] or q['item_no'] not in {i['item_no'] for i in card['items']}
                or not q['id'].startswith(f"f{card['entry_no']:03}-")
                or q['kind'] not in {'fact', 'story', 'person', 'form', 'history', 'survey'}
                or not 1 <= len(q['zh']) <= 52 or not 1 <= len(q['en'].split()) <= 28):
            raise ValueError('Invalid followup item/ID/kind/question length')
    if any(sum(q['item_no'] == i['item_no'] for q in questions) < 2 for i in card['items']):
        raise ValueError('Each catalog item requires at least two own followups')


def archive_followups(asset):
    for meta in asset['entries'].values():
        for lang in ('zh', 'en'):
            meta.setdefault('legacy_followups_' + lang, meta.get('followups_' + lang, [])[:])


def apply_interactions(asset, metadata, cards, chunks, glossary):
    if metadata['database_sha256'] != asset['database_sha256'] or set(metadata['entries']) != set(asset['entries']):
        raise ValueError('Interaction metadata source/coverage mismatch')
    archive_followups(asset)
    for card in cards:
        no = str(card['entry_no'])
        bodies = [c for c in chunks if c['unit_id'] == card['unit_id']]
        data = metadata['entries'][no]
        validate_interactions(data, card, bodies, glossary)
        entry = asset['entries'][no]
        entry.setdefault('keywords', entry.get('themes', []))
        entry.update(data)
        for lang in ('zh', 'en'):
            entry['followups_' + lang] = [q[lang] for q in entry['followups']]
        for c in bodies:
            meta = asset['chunks'][c['chunk_id']]
            meta.setdefault('keywords', meta.get('themes', []))
            meta['themes'] = [t['theme'] for t in entry['themes']]
    asset['navigation_version'] = 5


def translation_target(asset, glossary, edit):
    """Resolve the deliberately small override path syntax; never create targets."""
    scope, key, field = edit['scope'], edit['key'], edit['field']
    if scope not in {'entries', 'items', 'chunks', 'images', 'glossary'}:
        raise ValueError('Unknown translation scope: ' + str(scope))
    target = glossary if scope == 'glossary' else asset[scope]
    for part in key.split('/') if scope == 'glossary' else [key]:
        target = target[part]
    match = re.fullmatch(r'([a-z_]+)(?:\[(\d+)\])?', field)
    if not match:
        raise ValueError('Invalid translation field: ' + field)
    name, index = match.groups()
    if index is not None:
        target, name = target[name], int(index)
    if not isinstance(target[name], str):
        raise ValueError('Translation target must be text: ' + field)
    return target, name


def apply_translation_overrides(asset, glossary, overrides, database_sha256, *, check=False):
    """Exact, idempotent edits after merging checkpoints. Fail before publication."""
    if overrides.get('version') != 1 or overrides.get('database_sha256') != database_sha256:
        raise ValueError('Translation overrides: source/version mismatch')
    reviewed = set(overrides.get('reviewed_chunks', []))
    if not reviewed <= asset['chunks'].keys():
        raise ValueError('Translation overrides: unknown reviewed chunk')
    for index, edit in enumerate(overrides.get('edits', [])):
        try:
            target, field = translation_target(asset, glossary, edit)
            old, new, text = edit['find'], edit['replace'], target[field]
            if not old or not new or old == new:
                raise ValueError('empty or unchanged edit')
            if check:
                if new not in text:
                    raise ValueError('published replacement missing')
            elif text.count(old) == 1:
                target[field] = text.replace(old, new, 1)
            elif old not in text and new in text:
                pass
            else:
                raise ValueError('find must occur exactly once (or already applied)')
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f'Translation override {index + 1} {edit.get("key")}/{edit.get("field")}: {exc}') from exc
    return len(overrides.get('edits', []))


def translation_review_state(asset, overrides=None, approvals=None):
    """Bind approval to both texts so future regeneration cannot inherit a signature."""
    overrides, approvals = overrides or {}, approvals or {}
    reviewed = set(overrides.get('reviewed_chunks', []))
    hashes = overrides.get('reviewed_text_sha256', {})
    people = set()
    for key, meta in asset['chunks'].items():
        digest = hashlib.sha256(meta['text_en'].encode()).hexdigest()
        status = 'ai_reviewed' if key in reviewed and hashes.get(key, digest) == digest else 'machine'
        approval = approvals.get('chunks', {}).get(key, {})
        if (approval.get('decision') == 'approved' and approval.get('reviewer') and approval.get('date')
                and approval.get('source_sha256') == meta['source_sha256']
                and approval.get('text_en_sha256') == digest):
            status = 'human_approved'
            people.add(approval['reviewer'])
        meta['translation_status'] = status
    counts = Counter(m['translation_status'] for m in asset['chunks'].values())
    review = overrides.get('review', {})
    asset['translation_review'] = {
        'counts': {key: counts[key] for key in ('machine', 'ai_reviewed', 'human_approved')},
        'reviewer': review.get('reviewer', ''), 'method': review.get('method', ''),
        'date': review.get('date', ''), 'human_reviewers': sorted(people),
    }
    return asset['translation_review']


def validate_enrichment(asset, glossary, cards, chunks, database_sha256, *, translated=True):
    def require(condition, message):
        if not condition:
            raise ValueError('Invalid enrichment: ' + message)
    require(asset.get('version') == glossary.get('version') == 1, 'version')
    require(asset.get('database_sha256') == database_sha256, 'database hash mismatch')
    require(set(asset.get('entries', {})) == {str(c['entry_no']) for c in cards}, 'entry coverage')
    require(set(asset.get('items', {})) == {f"{c['unit_id']}:{i['item_no']}" for c in cards for i in c['items']}, 'item coverage')
    require(set(asset.get('chunks', {})) == {c['chunk_id'] for c in chunks}, 'chunk coverage')
    require(set(asset.get('images', {})) == {im['image_id'] for c in cards for im in c['images']}, 'image coverage')
    if isinstance(asset.get('translation_review'), dict):
        statuses = Counter(m.get('translation_status') for m in asset['chunks'].values())
        require(set(statuses) <= {'machine', 'ai_reviewed', 'human_approved'}, 'translation status')
        require(asset['translation_review'].get('counts') == {
            key: statuses[key] for key in ('machine', 'ai_reviewed', 'human_approved')}, 'translation status counts')
    require({c['district'] for c in cards} <= glossary.get('districts', {}).keys(), 'district glossary')
    require({s for c in cards for s in c['species']} <= glossary.get('species', {}).keys(), 'species glossary')
    for name, term in glossary['species'].items():
        require(bool(term.get('en') and term.get('latin')), 'species terminology: ' + name)
    for c in chunks:
        meta = asset['chunks'][c['chunk_id']]
        digest = hashlib.sha256((c['context_header'] + '\n' + c['text']).encode()).hexdigest()
        require(meta.get('content_hash') == c['content_hash'] and meta.get('source_sha256') == digest, 'stale chunk ' + c['chunk_id'])
        require(isinstance(meta.get('incidental_species'), list), 'incidental species')
        if translated:
            for field in ('text_en', 'context_header_en'):
                require(isinstance(meta.get(field), str) and bool(meta[field].strip()), c['chunk_id'] + ': ' + field)
            for field in ('synthetic_queries_zh', 'synthetic_queries_en'):
                require(2 <= len(meta.get(field, [])) <= 3, c['chunk_id'] + ': ' + field)
    for card in cards:
        meta = asset['entries'][str(card['entry_no'])]
        for item in card['items']:
            imeta = asset['items'][f"{card['unit_id']}:{item['item_no']}"]
            h = imeta.get('hierarchy', {})
            require(h.get('district') == card['district'] and isinstance(h.get('place_terms'), list), 'item hierarchy')
            if translated:
                require(bool(imeta.get('location_en') and imeta.get('species_en')), 'item translation')
        if not translated:
            continue
        for field in ('title_en', 'location_en', 'hook_zh', 'hook_en'):
            require(isinstance(meta.get(field), str) and bool(meta[field].strip()), str(card['entry_no']) + ': ' + field)
        require(len(meta['hook_zh']) <= 60 and len(meta['hook_en'].split()) <= 35, 'hook length: ' + str(card['entry_no']))
        for field in ('followups_zh', 'followups_en'):
            lower, upper = (4, 6) if 'followups' in meta else (2, 3)
            require(lower <= len(meta.get(field, [])) <= upper and all(isinstance(q, str) and q.strip() for q in meta[field]), 'followups: ' + str(card['entry_no']))
        if asset.get('navigation_version') == 5:
            validate_interactions(meta, card, [c for c in chunks if c['unit_id'] == card['unit_id']], glossary)
            require(all(meta['followups_' + lang] == [q[lang] for q in meta['followups']] for lang in ('zh', 'en')), 'derived followups')
        require(len(meta.get('review_flags_en', [])) == len(card.get('review_flags', [])), 'review flags')
        for image in card['images']:
            info = asset['images'][image['image_id']]
            require(bool(info.get('display_caption_en')), 'image display caption')
            require(not image.get('caption') or bool(info.get('caption_en')), 'image caption translation')
    if asset.get('navigation_version') == 5:
        counts = Counter(t['theme'] for e in asset['entries'].values() for t in e['themes'])
        require(len(glossary['themes']) <= 20 and all(counts[t] >= 3 for t in glossary['themes']), 'controlled themes need >=3 entries')
        require(all(set(m['themes']) <= glossary['themes'].keys() for m in asset['chunks'].values()), 'chunk themes')
    return {'entries': len(cards), 'items': len(asset['items']), 'chunks': len(chunks), 'images': len(asset['images'])}


def read_corpus(directory):
    import sqlite3
    with sqlite3.connect((directory / 'story.db').resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        cards = [json.loads(r[0]) for r in db.execute('SELECT card FROM entries ORDER BY entry_no')]
        chunks = [json.loads(r[0]) | {'item_no': r[1]} for r in db.execute('SELECT chunk,item_no FROM chunks ORDER BY seq')]
    return cards, chunks
