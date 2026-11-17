"""Source-bound navigation: select stored IDs, never display generated questions."""
import hashlib
import json
from .bilingual import folded

STARTERS = [
    ('start-story', '三官殿的龙柏有多少岁？', 'How old is the Dragon Juniper at Sanguan Hall?'),
    ('start-legend', '“清奇古怪”真是邓禹种的吗？', 'Tell me about the four junipers at Situ Temple.'),
    ('start-tour', '推荐几处有故事的古树。', 'Show me a few old trees with interesting stories.'),
]


def seen_ids(knowledge, history, question):
    seen, texts = set(), {folded(question)}
    for message in history:
        if message.get('role') == 'user':
            texts.add(folded(message.get('text', '')))
        if message.get('role') == 'assistant':
            result = message.get('result') or {}
            seen.update(result.get('suggested_question_ids', []))
            texts.update(folded(q) for q in result.get('suggested_questions', []))
    for meta in knowledge.enrichment['entries'].values():
        seen.update(q['id'] for q in meta.get('followups', []) if any(folded(q[lang]) in texts for lang in ('zh', 'en')))
    seen.update(key for key, zh, en in STARTERS if folded(zh) in texts or folded(en) in texts)
    return seen, texts


def overlap(a, b):
    def pairs(s):
        s = folded(s).replace(' ', '')
        return {s[i:i + 2] for i in range(len(s) - 1)}
    left, right = pairs(a), pairs(b)
    return len(left & right) / len(left | right) if left | right else 0


def candidates(knowledge, cards, subject='', focus=None, lang='zh'):
    values = []
    for card in cards:
        original = knowledge.cards[card['entry_no']]
        for q in knowledge.enrichment['entries'][str(card['entry_no'])].get('followups', []):
            item = next(i for i in original['items'] if i['item_no'] == q['item_no'])
            named = isinstance(subject, str) and (subject in knowledge.named_subjects or subject in knowledge.place_terms)
            if subject and not knowledge.subject_matches(subject, item['species']) and not named:
                continue
            if subject and not named:
                hints = knowledge.resolve_bilingual_terms(q['zh'])
                mentioned = hints['matched_species'] + knowledge.species_members(hints.get('matched_species_groups', []))
                if mentioned and not knowledge.subject_matches(subject, mentioned):
                    continue
            values.append(q | {'text': q[lang], 'entry_no': card['entry_no']})
    return sorted(values, key=lambda q: (0 if focus and q['entry_no'] == focus['entry_no'] and q['item_no'] == focus.get('item_no') else 1))


def bridges(knowledge, cards, lang):
    current = {c['entry_no'] for c in cards}
    groups = knowledge.glossary.get('species_groups', {})
    for card in cards:
        source = knowledge.cards[card['entry_no']]
        source_species = set(source['species'])
        related_species = set(source_species)
        for group in groups.values():
            if source_species.intersection(group['members']):
                related_species.update(group['members'])
        townships = {knowledge.enrichment['items'][f"{source['unit_id']}:{i['item_no']}"]['hierarchy'].get('township') for i in source['items']} - {None, ''}
        themes = {t['theme'] for t in knowledge.enrichment['entries'][str(source['entry_no'])].get('themes', []) if isinstance(t, dict)}
        for target in knowledge.cards.values():
            if target['entry_no'] in current or not knowledge.allowed(target):
                continue
            relation = 'species' if related_species.intersection(target['species']) else ''
            if not relation and any(knowledge.enrichment['items'][f"{target['unit_id']}:{i['item_no']}"]['hierarchy'].get('township') in townships for i in target['items']):
                relation = 'township'
            if not relation and themes.intersection(t['theme'] for t in knowledge.enrichment['entries'][str(target['entry_no'])].get('themes', []) if isinstance(t, dict)):
                relation = 'theme'
            if relation:
                meta = knowledge.enrichment['entries'][str(target['entry_no'])]
                # One stable ID per destination, regardless of which relationship led there.
                yield {'id': f"bridge-entry-{target['entry_no']:03}", 'text':
                       f"{target['location']}的{target['title']}还有什么故事？" if lang == 'zh' else
                       f"What stories does the book tell about {meta['title_en']} at {meta['location_en']}?", 'relation': relation}


def inventory_bridge(knowledge, filters, lang):
    labels = []
    districts = filters.get('district', [])
    for d in districts if isinstance(districts, list) else [districts]:
        labels.append(knowledge.glossary.get('districts', {}).get(d, {}).get('en', d) if lang == 'en' else d)
    location = filters.get('location')
    if location:
        meta = knowledge.glossary.get('places', {}).get(location, {})
        labels.append(meta.get('en', location) if lang == 'en' else location)
    members = knowledge.species_members(filters.get('species'))
    labels += [knowledge.species_label(m) if lang == 'en' else m for m in members]
    if filters.get('status'):
        labels.append({'gone': ('已不存', 'no longer extant'), 'extant': ('仍存', 'extant')}[filters['status']][lang == 'en'])
    scope = (' / '.join(labels) or ('全书' if lang == 'zh' else 'the book'))
    canonical_filters = {key: sorted(value) if isinstance(value, list) else value for key, value in filters.items()}
    digest = hashlib.sha256(json.dumps(canonical_filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {'id': 'bridge-inventory-' + digest, 'text': f'从{scope}中挑几处古树讲讲故事。' if lang == 'zh' else f'Tell me a few tree stories from {scope}.'}


def choose(knowledge, result, history, question, resolved_question='', subject='', preferred_ids=()):
    lang = result.get('lang', 'zh')
    seen, texts = seen_ids(knowledge, history, question)
    if result.get('status') != 'answered':
        rows = [{'id': key, 'text': en if lang == 'en' else zh} for key, zh, en in STARTERS if key not in seen]
        return rows[:3]
    inventory = result.get('route') == 'inventory'
    filters = result.get('structured', {}).get('filters', {})
    subject = filters.get('species', '') if inventory else subject
    rows = candidates(knowledge, result.get('cards', []), subject, result.get('focus'), lang)
    def acceptable(q):
        if q['id'] in seen or folded(q['text']) in texts:
            return False
        if max(overlap(q['text'], question), overlap(q['zh'], resolved_question)) >= .65:
            return False
        intents = knowledge.glossary.get('followup_intents', {})
        for words in intents.values():
            if any(t in folded(question) or t in folded(resolved_question) for t in words) and any(t in folded(q['zh']) or t in folded(q['en']) for t in words):
                return False
        return True
    rows = [q for q in rows if acceptable(q)]
    by_id = {q['id']: q for q in rows}
    picks = [by_id[key] for key in dict.fromkeys(preferred_ids) if key in by_id][:2]
    picks += [q for q in rows if q['id'] not in {p['id'] for p in picks}][:2 - len(picks)]
    options = [inventory_bridge(knowledge, filters, lang)] if inventory else bridges(knowledge, result.get('cards', []), lang)
    bridge = next((q for q in options if q['id'] not in seen and folded(q['text']) not in texts), None)
    return ([bridge] if bridge and inventory else []) + picks + ([bridge] if bridge and not inventory else [])
