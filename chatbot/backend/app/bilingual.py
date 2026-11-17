"""Deterministic glossary lookup; Chinese keys remain the shared query protocol."""
import unicodedata
import re


def species_members(value, groups=None):
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(dict.fromkeys(member for name in values if name
                             for member in (groups or {}).get(name, {}).get('members', [name])))


def subject_matches(subject, species, groups=None):
    """One catalog identity rule shared by counting, retrieval and image selection."""
    return not subject or bool(set(species_members(subject, groups)).intersection(species))


def folded(value):
    return ' '.join(''.join(ch if ch.isalnum() else ' ' for ch in
                           unicodedata.normalize('NFKC', value).casefold()).split())


def contains(text, term):
    start = 0
    while (at := text.find(term, start)) >= 0:
        end = at + len(term)
        left = at == 0 or not (term[0].isascii() and term[0].isalnum() and text[at - 1].isascii() and text[at - 1].isalnum())
        right = end == len(text) or not (term[-1].isascii() and term[-1].isalnum() and text[end].isascii() and text[end].isalnum())
        if left and right:
            yield at, end
        start = at + 1


class Glossary:
    def __init__(self, glossary, enrichment, cards):
        self.data, self.aliases = glossary, []
        generic = {folded(t) for m in glossary.get('species_groups', {}).values() for t in m.get('aliases', [])}
        for section in ('districts', 'species_groups', 'species', 'places', 'special_terms', 'culture', 'themes', 'theme_intents'):
            for key, meta in glossary.get(section, {}).items():
                terms = [key, meta.get('en', ''), meta.get('short_en', ''), *meta.get('aliases', []), *meta.get('aliases_en', [])]
                if section == 'species_groups':
                    terms = [meta['zh'], *meta.get('aliases', [])]
                # Botanical names identify species, not every named individual/cultivar.
                if section == 'species' and key not in {'孩儿莲', '单体红山茶', '连理山茶', '白玉兰', '山茶'}:
                    terms.append(meta.get('latin', ''))
                cased = {t['text'] for t in terms if isinstance(t, dict) and t.get('cased')}
                for term in terms:
                    original = term.get('text', '') if isinstance(term, dict) else term
                    alias = folded(original)
                    if section == 'species' and alias in generic:
                        continue
                    if len(alias) >= 2:
                        self.aliases.append((alias, section, meta.get('canonical', key),
                                             meta | ({'alias_case': original} if original in cased else {})))
        for card in cards:
            for item in card['items']:
                meta = enrichment['items'][f"{card['unit_id']}:{item['item_no']}"]
                for alias in meta.get('aliases_en', []):
                    alias = folded(alias)
                    if len(alias) >= 3 and alias not in {'temple','garden','town','village','mountain','park'}:
                        self.aliases.append((alias, 'places', item['location'], {'anchors': [item['location']]}))
        self.aliases.sort(key=lambda row: -len(row[0]))

    def resolve(self, text):
        original = unicodedata.normalize('NFKC', text)
        text = folded(text)
        result = {'matched_districts': [], 'matched_species': [], 'matched_species_groups': [], 'matched_anchors': [], 'matched_entries': [], 'matched_themes': [], 'terms': []}
        occupied = []
        for alias, section, key, meta in self.aliases:
            if meta.get('alias_case') and not re.search(r'(?<!\w)' + re.escape(meta['alias_case']) + r'(?!\w)', original):
                continue
            spans = [span for span in contains(text, alias) if not any(a <= span[0] and span[1] <= b and b - a > len(alias) for a, b in occupied)]
            if not spans:
                continue
            occupied.extend(spans)
            if section == 'districts':
                result['matched_districts'].append(key)
            if section == 'species':
                result['matched_species'].append(key)
            if section == 'species_groups':
                result['matched_species_groups'].append(key)
                result['matched_anchors'].extend(meta['members'])
            if section == 'themes':
                result['matched_themes'].append(key)
            if section == 'theme_intents':
                result['matched_themes'].extend(meta['themes'])
            if section == 'special_terms':
                if meta.get('target_species'):
                    result['matched_species'].append(meta['target_species'])
                if meta.get('entry_no'):
                    result['matched_entries'].append(meta['entry_no'])
            result['matched_anchors'].extend(meta.get('anchors', [key]))
            result['terms'].append({'key': key, 'en': meta.get('en', alias), **({'not_district': True} if meta.get('not_district') else {}), **({'latin': meta['latin']} if meta.get('latin') else {})})
        for field in ('matched_districts', 'matched_species', 'matched_species_groups', 'matched_anchors', 'matched_entries', 'matched_themes'):
            result[field] = list(dict.fromkeys(result[field]))
        result['terms'] = list({term['key']: term for term in result['terms']}.values())
        return result

    def canonical(self, section, value):
        if isinstance(value, list):
            return list(dict.fromkeys(self.canonical(section, v) for v in value))
        if section == 'species' and value:
            groups = self.data.get('species_groups', {})
            if value in groups:
                return value
            matches = [key for alias, group, key, _ in self.aliases if group == 'species_groups' and alias == folded(value)]
            if matches:
                return matches[0]
        if not value or value in self.data.get(section, {}):
            return value
        query = folded(value)
        matches = list(dict.fromkeys(key for alias, group, key, meta in self.aliases if group == section and alias == query
                                    and (not meta.get('alias_case') or value == meta['alias_case'])))
        return matches[0] if len(matches) == 1 else value


def response_language(question, ui_lang='zh'):
    # Identifier-only input follows UI preference, not the Chinese code prefix.
    normalized = ''.join(question.split()).strip('?!！？。，,.')
    if normalized and any(ch.isdigit() for ch in normalized) and not any(ch.isascii() and ch.isalpha() for ch in normalized):
        prefix = normalized.rstrip('0123456789')
        if prefix in {'吴中', '姑苏', '吴江', '相城', '昆山', '常熟', '太仓', '张家港', '高新', '园区', ''}:
            return ui_lang
    chinese = sum('\u4e00' <= ch <= '\u9fff' for ch in question)
    words = sum(any(ch.isascii() and ch.isalpha() for ch in word) for word in question.split())
    if chinese > words:
        return 'zh'
    if words:
        return 'en'
    return ui_lang
