import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from .context import render, TYPE_LABELS, TYPE_LABELS_EN
from .places import area_stem, fallback_enrichment
from .bilingual import Glossary, species_members, subject_matches
from .enrichment import read_corpus, validate_enrichment


class Knowledge:
    def __init__(self, directory, allow_fallback=False):
        self.directory = directory
        self.path = directory / 'story.db'
        self.manifest = json.loads((directory / 'manifest.json').read_text())
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.manifest['database_sha256']:
            raise ValueError('知识库文件与 manifest 的哈希不一致')
        with self.db() as db:
            self.cards = {r['entry_no']: json.loads(r['card']) for r in db.execute('SELECT entry_no,card FROM entries')}
            self.item_aliases, self.entry_aliases = {}, {}
            by_unit = {c['unit_id']: c for c in self.cards.values()}
            for row in db.execute("SELECT unit_id,item_no,chunk FROM chunks WHERE review_status!='rejected'"):
                card = by_unit[row['unit_id']]
                chunk = json.loads(row['chunk'])
                codes = set(card['official_codes']) | set(chunk.get('official_codes', []))
                aliases = [term for term in chunk.get('search_terms', []) if term not in codes]
                for mapping, key in ((self.item_aliases, (row['unit_id'], row['item_no'])),
                                     (self.entry_aliases, card['entry_no'])):
                    mapping[key] = list(dict.fromkeys(mapping.get(key, []) + aliases))
        self.districts = sorted({c['district'] for c in self.cards.values()})
        self.species = sorted({s for c in self.cards.values() for s in c['species']})
        glossary_path, enrichment_path = directory / 'glossary.json', directory / 'enrichment.json'
        self.has_enrichment = enrichment_path.exists()
        self.glossary = json.loads(glossary_path.read_text()) if glossary_path.exists() else {'version': 1}
        _, chunks = read_corpus(directory)
        if enrichment_path.exists():
            self.enrichment = json.loads(enrichment_path.read_text())
            validate_enrichment(self.enrichment, self.glossary, list(self.cards.values()), chunks,
                                self.manifest['database_sha256'])
        else:
            if not allow_fallback:
                raise FileNotFoundError('Missing enrichment.json; build and validate sidecars before startup')
            self.enrichment = fallback_enrichment(list(self.cards.values()), chunks,
                                                 self.manifest['database_sha256'], self.glossary)
        self.bilingual = Glossary(self.glossary, self.enrichment, self.cards.values())
        self.chunk_enrichment = self.enrichment['chunks']
        self.named_subjects = set(self.glossary.get('special_terms', {})) | {
            name for meta in self.chunk_enrichment.values() for name in meta.get('nicknames', [])}
        self.chunk_incidental_species = {key: tuple(meta['incidental_species']) for key, meta in self.chunk_enrichment.items()}
        places, areas, self.item_place_terms = set(self.enrichment['place_terms']), set(self.enrichment['area_place_terms']), {}
        for card in self.cards.values():
            for item in card['items']:
                key = (card['unit_id'], item['item_no'])
                meta = self.enrichment['items'][f'{key[0]}:{key[1]}']
                hierarchy = meta['hierarchy']
                named = [hierarchy.get(field, '') for field in ('township', 'village', 'site')]
                self.item_place_terms[key] = frozenset(hierarchy['place_terms']) | {n for n in named if n}
                if hierarchy.get('is_area'):
                    areas.add(item['location'])
                places.update(hierarchy['place_terms'])
                areas.update(hierarchy.get('area_terms', []))
        self.place_terms = tuple(sorted(places, key=lambda term: (-len(term), term)))
        self.area_place_terms = frozenset(areas)

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def allowed(self, card):
        return card['review_status'] != 'rejected'

    def resolve_bilingual_terms(self, text):
        return self.bilingual.resolve(text)

    def expand_query(self, text):
        # Chinese queries keep their established behavior. Latin input gets static anchors.
        if not any(ch.isascii() and ch.isalpha() for ch in text):
            return text
        anchors = [a for a in self.resolve_bilingual_terms(text)['matched_anchors'] if a not in text]
        return text + ('（' + '；'.join(anchors) + '）' if anchors else '')

    def species_label(self, name):
        return self.glossary.get('species', {}).get(name, {}).get('en', name)

    def species_members(self, value):
        return species_members(value, self.glossary.get('species_groups', {}))

    def theme_entries(self, themes, districts=()):
        return [no for no, card in self.cards.items() if self.allowed(card)
                and (not districts or card['district'] in districts)
                and set(themes).intersection(t['theme'] for t in self.enrichment['entries'][str(no)].get('themes', []) if isinstance(t, dict))]

    def subject_matches(self, subject, species):
        return subject_matches(subject, species, self.glossary.get('species_groups', {}))

    def public_card(self, card, lang='zh'):
        keys = ('unit_id', 'entry_no', 'title', 'district', 'location', 'items', 'species', 'status',
                'printed_pages', 'as_of', 'review_flags', 'official_codes')
        result = {k: card[k] for k in keys if k in card}
        result['location_aliases'] = self.location_aliases(card)
        result['images'] = [{k: im[k] for k in ('image_id', 'printed_page', 'caption', 'credit', 'kind', 'rights', 'item_no', 'role') if k in im}
                            | {'url': '/api/images/' + im['image_id'],
                               'width': im.get('native_px', [None, None])[0],
                               'height': im.get('native_px', [None, None])[1]} for im in card['images']]
        meta = self.enrichment['entries'][str(card['entry_no'])]
        result.update(hook=meta.get('hook_' + lang, ''), followups=meta.get('followups_' + lang, []), lang=lang)
        if lang == 'en':
            result.update(title=meta.get('title_en', card['title']) + ' (' + card['title'] + ')',
                          title_zh=card['title'], location=meta.get('location_en', card['location']),
                          location_zh=card['location'], species=[self.species_label(s) for s in card['species']],
                          species_zh=card['species'], district_zh=card['district'],
                          district=self.glossary.get('districts', {}).get(card['district'], {}).get('en', card['district']),
                          review_flags=meta.get('review_flags_en', card.get('review_flags', [])))
            result['items'] = [item | {'location': self.enrichment['items'][f"{card['unit_id']}:{item['item_no']}"].get('location_en', item['location']),
                                      'location_zh': item['location'], 'species_zh': item['species'],
                                      'species': [self.species_label(s) for s in item['species']]} for item in card['items']]
            result['images'] = [im | self.enrichment['images'][im['image_id']] |
                                {'caption': self.enrichment['images'][im['image_id']].get('caption_en', ''),
                                 'display_caption': self.enrichment['images'][im['image_id']].get('display_caption_en', '')}
                                for im in result['images']]
        return result

    def location_aliases(self, card):
        aliases = list(self.entry_aliases.get(card['entry_no'], []))
        for item in card['items']:
            key = (card['unit_id'], item['item_no'])
            aliases.extend(self.item_aliases.get(key, []))
            aliases.extend(self.enrichment['items'][f'{key[0]}:{key[1]}'].get('aliases', []))
        return list(dict.fromkeys(aliases))

    def catalog(self, q='', district='', species='', lang='zh'):
        q = q.strip().casefold()
        district = self.bilingual.canonical('districts', district)
        species = self.bilingual.canonical('species', species)
        return [self.public_card(c, lang) for c in self.cards.values() if self.allowed(c)
                and (not q or q in json.dumps([c['title'], c['location'], c['species'], c['official_codes'],
                                              self.location_aliases(c),
                                              [self.enrichment['entries'][str(c['entry_no'])].get(k, '') for k in ('title_en', 'location_en')],
                                              [i['location'] for i in c['items']],
                                              [self.enrichment['items'][f"{c['unit_id']}:{i['item_no']}"].get('aliases_en', []) for i in c['items']]], ensure_ascii=False).casefold())
                and (not district or district == c['district']) and self.subject_matches(species, c['species'])]

    def alias_locations(self, question):
        """Resolve unique index aliases for query enrichment only, never candidate gating."""
        owners = {}
        for no, aliases in self.entry_aliases.items():
            for alias in aliases:
                owners.setdefault(alias, set()).add(no)
        locations = []
        for alias, nos in owners.items():
            matches = alias in question
            if not matches and len(nos) == 1:
                card = self.cards[next(iter(nos))]
                matches = any(h.get('township_stem') and h.get('village') and
                              h['township_stem'] in question and h['village'] in question
                              for i in card['items']
                              for h in [self.enrichment['items'][f"{card['unit_id']}:{i['item_no']}"]['hierarchy']])
            if len(nos) == 1 and matches:
                card = self.cards[next(iter(nos))]
                if self.allowed(card):
                    locations.append(card['location'])
        return list(dict.fromkeys(locations))

    def code_matches(self, query):
        normalized = ''.join(query.split())
        return [(code, c) for c in self.cards.values() if self.allowed(c) for code in c['official_codes']
                if code in normalized and not re.search(re.escape(code) + r'\d', normalized)]

    def chunks(self, no):
        card = self.cards.get(no)
        if not card or not self.allowed(card):
            return []
        with self.db() as db:
            return [json.loads(r[0]) | {'item_no': r[1]} for r in db.execute("SELECT chunk,item_no FROM chunks WHERE unit_id=? AND review_status!='rejected' ORDER BY seq", (card['unit_id'],))]

    def source(self, chunk, sid, card, lang='zh'):
        pages = chunk['locator']['printed_pages']
        item_no = next((c['item_no'] for c in card['children'] if c['chunk_id'] == chunk['chunk_id']), None)
        item = next((i for i in card['items'] if i['item_no'] == item_no), None)
        result = dict(id=sid, chunk_id=chunk['chunk_id'], unit_id=chunk['unit_id'], entry_no=card['entry_no'],
                    item_no=item_no, title='、'.join(chunk.get('species', [])) or card['title'], entry_title=card['title'],
                    location=item['location'] if item else card['location'],
                    content_type=chunk['content_type'], type_label=TYPE_LABELS[chunk['content_type']],
                    printed_pages=pages, text=chunk['text'], context_header=chunk['context_header'],
                    evidence_text=chunk['context_header'] + '\n' + chunk['text'],
                    certainty_flags=chunk.get('certainty_flags', []),
                    review_flags=card.get('review_flags', []), as_of=chunk['as_of'],
                    official_codes=card['official_codes'],
                    content_hash=chunk['content_hash'],
                    citation='《树碑立传》第' + '、'.join(map(str, pages)) + '页')
        if lang == 'en':
            meta = self.chunk_enrichment[chunk['chunk_id']]
            entry = self.enrichment['entries'][str(card['entry_no'])]
            result.update(title_zh=result['title'], location_zh=result['location'], text_zh=chunk['text'],
                          translation_status=meta.get('translation_status', 'machine'),
                          title=', '.join(self.species_label(s) for s in chunk.get('species', [])) or entry.get('title_en', card['title']),
                          entry_title=entry.get('title_en', card['title']),
                          location=self.enrichment['items'].get(f"{card['unit_id']}:{item_no}", {}).get('location_en', entry.get('location_en', card['location'])),
                          type_label=TYPE_LABELS_EN[chunk['content_type']], text=meta.get('text_en', chunk['text']),
                          context_header=meta.get('context_header_en', chunk['context_header']),
                          review_flags=entry.get('review_flags_en', card.get('review_flags', [])),
                          citation='Book of Ancient Trees, ' + ('p. ' if len(pages) == 1 else 'pp. ') + ', '.join(map(str, pages)))
            result['evidence_text'] = result['context_header'] + '\n' + result['text']
        result['quote'] = result['text']
        result['lang'] = lang
        return result

    def entry_context(self, nos, lang='zh'):
        """Load explicitly identified entries; no text search or scoring."""
        parents = [{'card': self.cards[n], 'chunks': self.chunks(n), 'hit_ids': []}
                   for n in dict.fromkeys(nos) if n in self.cards and self.allowed(self.cards[n])]
        return self.pack([p for p in parents if p['chunks']], lang)

    def pack(self, parents, lang='zh'):
        sources = [self.source(c, f'S{i + 1}', p['card'], lang) for i, (p, c) in enumerate((p, c) for p in parents for c in p['chunks'])]
        views = parents
        if lang == 'en':
            views = [p | {'card': p['card'] | self.public_card(p['card'], lang),
                          'chunks': [c | {'text': self.chunk_enrichment[c['chunk_id']].get('text_en', c['text'])} for c in p['chunks']]}
                     for p in parents]
        context, _ = render(views, lang)
        return context, sources, [self.public_card(p['card'], lang) for p in parents]

    def structured(self, filters, lang='zh'):
        """Full-set parameterized SQL; status applies to items, never just parent status."""
        where = ["e.review_status!='rejected'"]
        args = []
        for field in ('district', 'status'):
            if filters.get(field):
                if field == 'district' and isinstance(filters[field], list):
                    where.append('e.district IN (' + ','.join('?' for _ in filters[field]) + ')')
                    args.extend(filters[field])
                    continue
                where.append(('e.' if field == 'district' else 'i.') + field + '=?')
                args.append(filters[field])
        if filters.get('species'):
            members = self.species_members(filters['species'])
            where.append('EXISTS (SELECT 1 FROM json_each(i.species) WHERE value IN (' + ','.join('?' for _ in members) + '))')
            args.extend(members)
        with self.db() as db:
            rows = db.execute('''SELECT i.*, e.entry_no FROM items i JOIN entries e ON e.unit_id=i.unit_id WHERE '''
                              + ' AND '.join(where) + ' ORDER BY e.entry_no,i.item_no', args).fetchall()
        values, sources, cards = [], [], {}
        for r in rows:
            no, item = r['entry_no'], r['item_no']
            location = filters.get('location')
            if location:
                key = (r['unit_id'], item)
                names = [r['location'], *self.enrichment['items'][f'{key[0]}:{key[1]}'].get('aliases', [])]
                stem = area_stem(location)
                # A known township stem must match a place term, not the middle
                # of another name (镇湖 must not match 东山镇湖沙公墓).
                area_match = len(stem) >= 2 and stem in self.item_place_terms.get(key, ())
                if not (area_match or (stem not in self.area_place_terms and any(location in name for name in names))):
                    continue
            chunks = [c for c in self.chunks(no) if c['item_no'] == item and c['content_type'] == 'story']
            if not chunks:
                continue
            refs = []
            for c in chunks:
                source = self.source(c, f'S{len(sources) + 1}', self.cards[no], lang)
                sources.append(source)
                refs.append(source['id'])
            values.append(dict(entry_no=no, item_no=item, location=r['location'], species=json.loads(r['species']), status=r['status'], source_ids=refs))
            if lang == 'en':
                values[-1].update(location=self.enrichment['items'][f"{r['unit_id']}:{item}"].get('location_en', r['location']),
                                  species=[self.species_label(s) for s in json.loads(r['species'])])
            cards[no] = self.public_card(self.cards[no], lang)
        return values, sources, list(cards.values())

    def image_path(self, image_id):
        with self.db() as db:
            row = db.execute('SELECT file,unit_id FROM images WHERE image_id=?', (image_id,)).fetchone()
        if not row or not any(c['unit_id'] == row['unit_id'] and self.allowed(c) for c in self.cards.values()):
            return None
        path = self.directory / 'images' / Path(row['file']).name
        return path if path.is_file() else None
