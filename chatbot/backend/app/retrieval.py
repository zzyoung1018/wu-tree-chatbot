"""Child-only vector retrieval, optional reranking, then complete parent expansion."""
import hashlib
import json
import math
import struct
import time

from .embeddings import EmbeddingError, encoder_spec, normalized
from .context import render
from .reranking import RerankError
from .provider import ModelError
from .districts import supports_district


def input_hash(row):
    return hashlib.sha256((row['context_header'] + '\n' + row['text']).encode()).hexdigest()


def mentions_subject(text, subject, species_names):
    # Whole recorded species names own their text: 枫杨树 / 黄杨树 must
    # not suppress the rescue of a separate 杨树 mention farther down.
    for species in species_names:
        if species not in subject and subject in species + '树':
            text = text.replace(species + '树', '')
    return subject in text


class VectorIndex:
    def __init__(self, knowledge, settings):
        self.vectors, self.reason, self.meta = {}, 'missing_index', {}
        self.spec = encoder_spec(settings)
        with knowledge.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key='embedding_index'").fetchone()
            if not row:
                return
            try:
                self.meta = json.loads(row[0])
                if self.meta['encoder'] != self.spec:
                    self.reason = 'encoder_mismatch'
                    return
                rows = db.execute("""SELECT c.*, v.model, v.dim, v.content_hash AS vector_hash, v.vec
                    FROM chunks c JOIN entries e ON e.unit_id=c.unit_id
                    LEFT JOIN chunk_vectors v ON v.chunk_id=c.chunk_id
                    WHERE c.review_status!='rejected' AND e.review_status!='rejected' ORDER BY c.chunk_id""").fetchall()
                for r in rows:
                    digest = input_hash(r)
                    if (r['content_hash'] != 'sha256:' + digest[:16] or r['vector_hash'] != r['content_hash']
                        or self.meta['inputs'].get(r['chunk_id']) != digest or r['model'] != self.spec['model']
                        or r['dim'] != self.spec['dim'] or not isinstance(r['vec'], bytes)
                        or len(r['vec']) != 4 * self.spec['dim']):
                        raise ValueError('invalid or stale vector')
                    vector = struct.unpack('<' + 'f' * self.spec['dim'], r['vec'])
                    if any(not math.isfinite(v) for v in vector) or abs(sum(v * v for v in vector) - 1) > 1e-4:
                        raise ValueError('invalid norm')
                    self.vectors[r['chunk_id']] = {'chunk_id': r['chunk_id'], 'unit_id': r['unit_id'], 'vector': vector}
                self.reason = '' if self.vectors else 'empty_index'
            except (ValueError, TypeError, KeyError, AttributeError, struct.error):
                self.vectors, self.reason = {}, 'stale_or_invalid_index'

    def search(self, vector, eligible, k=20):
        vector = normalized(vector, self.spec['dim'])
        scored = [{'chunk_id': cid, 'unit_id': item['unit_id'],
                   'score': sum(a * b for a, b in zip(vector, item['vector']))}
                  for cid, item in self.vectors.items() if cid in eligible]
        return sorted(scored, key=lambda r: (-r['score'], r['chunk_id']))[:k]


class VectorRetrieval:
    def __init__(self, knowledge, settings, embeddings=None, reranker=None):
        self.knowledge, self.settings = knowledge, settings
        self.embeddings, self.reranker = embeddings, reranker
        self.index = VectorIndex(knowledge, settings)
        self.last_error, self.last_rerank_error = '', ''

    def status(self):
        reason = 'unconfigured' if not self.embeddings or not self.embeddings.configured else self.index.reason
        return {'mode': 'vector', 'ready': not reason, 'reason': reason,
                'model': self.settings.embedding_model, 'dimensions': self.settings.embedding_dimensions,
                'vectors': len(self.index.vectors), 'last_error': self.last_error,
                'rerank': {'enabled': self.settings.rerank_enabled, 'model': self.settings.rerank_model,
                           'configured': bool(self.reranker and self.reranker.configured),
                           'last_error': self.last_rerank_error}}

    def eligible(self, nos=None):
        return {c['chunk_id'] for no in (nos or self.knowledge.cards) for c in self.knowledge.chunks(no)}

    def rerank_documents(self, dense, enrichment=None):
        enrichment = self.settings.rerank_enrichment if enrichment is None else enrichment
        if enrichment not in ('off', 'lean', 'full'):
            raise ValueError('Unknown rerank enrichment arm')
        chunks = {c['chunk_id']: c for no in self.knowledge.cards for c in self.knowledge.chunks(no)}
        documents = [chunks[h['chunk_id']]['context_header'] + '\n' + chunks[h['chunk_id']]['text'] for h in dense]
        if enrichment != 'off':
            for i, hit in enumerate(dense):
                meta = self.knowledge.chunk_enrichment.get(hit['chunk_id'], {})
                parts = [*meta.get('themes', []), meta.get('hook_zh', '')]
                if enrichment == 'full':
                    parts.append(meta.get('hook_en', ''))
                parts.extend(meta.get('synthetic_queries_zh', []))
                if enrichment == 'full':
                    parts.extend(meta.get('synthetic_queries_en', []))
                header = ' · '.join(parts).strip(' ·')
                if header:
                    documents[i] = header + '\n' + documents[i]
        return documents

    async def reorder(self, query, dense, enrichment=None):
        enrichment = self.settings.rerank_enrichment if enrichment is None else enrichment
        if enrichment not in ('off', 'lean', 'full'):
            raise ValueError('Unknown rerank enrichment arm')
        arm = {'enrichment': enrichment}
        if not self.settings.rerank_enabled:
            return dense, {'status': 'disabled', **arm}
        if len(dense) < 2:
            return dense, {'status': 'skipped', 'reason': 'fewer_than_two_candidates', **arm}
        if not self.reranker or not self.reranker.configured:
            self.last_rerank_error = 'unconfigured'
            return dense, {'status': 'failed', 'reason': 'unconfigured', **arm}
        # Only child texts already retrieved by vector similarity enter rerank.
        # Preserve the index->chunk mapping; never accept model-written IDs/text.
        documents = self.rerank_documents(dense, enrichment)
        try:
            ordered, call = await self.reranker.rank(query, documents)
        except RerankError as exc:
            self.last_rerank_error = exc.code
            return dense, {'status': 'failed', 'reason': exc.code, **arm}
        self.last_rerank_error = ''
        return [dense[r['index']] | {'rerank_rank': rank, 'rerank_score': r['relevance_score']}
                for rank, r in enumerate(ordered, 1)], {'status': 'applied', **call, **arm}

    async def search(self, query, nos=None, *, candidate_limit=None, top_entries=8, districts=(), subject='', lang='zh', rerank_enrichment=None, themes=(), theme_pool=None):
        query = self.knowledge.expand_query(query)
        candidate_limit = self.settings.retrieval_candidate_limit if candidate_limit is None else candidate_limit
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 500:
            raise ValueError('candidate_limit must be between 1 and 500')
        if type(top_entries) is not int or not 1 <= top_entries <= 20:
            raise ValueError('top_entries must be between 1 and 20')
        started = time.monotonic()
        pool_mode = self.settings.theme_pool if theme_pool is None else theme_pool
        if pool_mode not in ('off', 'on'):
            raise ValueError('Unknown theme pool setting')
        pool = self.knowledge.theme_entries(themes, districts)
        pool_applied = pool_mode == 'on' and bool(themes) and len(pool) >= 3
        if pool_applied:
            intersected = sorted(set(pool).intersection(nos)) if nos else pool
            pool_applied = len(intersected) >= 3
            if pool_applied:
                nos = intersected
        eligible = self.eligible(nos)
        audit = {'mode': 'vector', 'ranking': 'cosine', 'candidate_entries': nos or [],
                 'embedding': None, 'rerank': {'status': 'not_run'}, 'index_encoder': self.index.spec,
                 'candidate_limit': candidate_limit, 'eligible_count': len(eligible),
                 'context_budget': self.settings.retrieval_context_chars, 'top_entries': top_entries,
                 'districts': list(districts), 'subject': subject, 'supplemental_chunks': []}
        audit['theme_pool'] = {'setting': pool_mode, 'themes': list(themes), 'pool_size': len(pool), 'applied': pool_applied}

        def unavailable(reason):
            self.last_error = reason
            audit.update(error_code=reason, duration_ms=round((time.monotonic() - started) * 1000))
            message = ('向量索引尚未就绪，请检查模型配置和知识库版本后重试。'
                       if not reason.startswith('embedding_') else
                       '向量检索服务暂不可用，请检查测试密钥或稍后重试。')
            raise ModelError('vector_unavailable', message, audit={'retrieval': audit})

        if not eligible:
            audit.update(hits=[], ranked_hits=[], selected_entries=[], context_chunks=[], dropped_unmatched=[], n_chars=0,
                         duration_ms=round((time.monotonic() - started) * 1000))
            return ('', [], []), audit
        state = self.status()
        if not state['ready']:
            unavailable(state['reason'])
        try:
            vector, audit['embedding'] = await self.embeddings.query(query)
        except EmbeddingError as exc:
            unavailable('embedding_' + exc.code)
        self.last_error = ''
        dense = [h | {'vector_rank': rank, 'vector_score': h['score']}
                 for rank, h in enumerate(self.index.search(vector, eligible, k=candidate_limit), 1)]
        ranked_hits, audit['rerank'] = await self.reorder(query, dense, rerank_enrichment)
        audit['candidate_count'] = len(dense)
        audit['corpus_fully_recalled'] = len(dense) == len(eligible)
        if audit['rerank']['status'] == 'applied':
            audit['ranking'] = 'rerank'
        audit['ranked_hits'] = ranked_hits
        chunks = {c['chunk_id']: c | {'district': card['district']}
                  for no, card in self.knowledge.cards.items() for c in self.knowledge.chunks(no)}
        hits = [h for h in ranked_hits if not districts or chunks[h['chunk_id']]['district'] in districts or
                set(districts).intersection(self.knowledge.chunk_enrichment.get(h['chunk_id'], {}).get('mentioned_districts', [])) or
                (not self.knowledge.has_enrichment and supports_district(chunks[h['chunk_id']], districts))]
        selected, units = [], set()
        for hit in hits:
            if hit['unit_id'] not in units:
                selected.append(hit)
                units.add(hit['unit_id'])
                if len(units) == top_entries:
                    break
        by_unit = {c['unit_id']: c for c in self.knowledge.cards.values()}
        def mentions(chunk):
            meta = self.knowledge.chunk_enrichment.get(chunk['chunk_id'])
            if meta and (subject in self.knowledge.glossary.get('species', {}) or subject in self.knowledge.glossary.get('species_groups', {})):
                return self.knowledge.subject_matches(subject, meta.get('mentioned_species', []) + meta['incidental_species'])
            return mentions_subject(chunk['text'], subject, self.knowledge.species)

        if subject and not any(self.knowledge.subject_matches(subject, by_unit[u]['species']) or
                               any(mentions(c) for c in self.knowledge.chunks(by_unit[u]['entry_no'])) for u in units):
            extra = [h for h in hits if h['unit_id'] not in units and mentions(chunks[h['chunk_id']])][:3]
            selected.extend(extra)
            audit['supplemental_chunks'] = [h['chunk_id'] for h in extra]
        parents, dropped = self.expand(selected, budget=self.settings.retrieval_context_chars, lang=lang)
        context, sources, cards = self.knowledge.pack(parents, lang)
        audit.update(hits=selected, selected_entries=[c['entry_no'] for c in cards],
                     context_chunks=[s['chunk_id'] for s in sources], dropped_unmatched=dropped,
                     district_filtered_count=len(ranked_hits) - len(hits), n_chars=len(context),
                     duration_ms=round((time.monotonic() - started) * 1000))
        return (context, sources, cards), audit

    def expand(self, hits, budget=100000, lang='zh'):
        grouped = {}
        for hit in hits:
            grouped.setdefault(hit['unit_id'], []).append(hit['chunk_id'])
        by_unit = {c['unit_id']: c for c in self.knowledge.cards.values()}
        parents = [{'card': by_unit[unit], 'chunks': self.knowledge.chunks(by_unit[unit]['entry_no']), 'hit_ids': ids}
                   for unit, ids in grouped.items()]
        dropped = []
        def context_size():
            return len(self.knowledge.pack(parents, lang)[0]) if lang == 'en' else len(render(parents)[0])
        for group in reversed(parents):
            for body in (False, True):
                for chunk in list(group['chunks']):
                    if context_size() <= budget:
                        return parents, dropped
                    if chunk['chunk_id'] not in group['hit_ids'] and (chunk['content_type'] == 'story') == body:
                        group['chunks'].remove(chunk)
                        dropped.append(chunk['chunk_id'])
        if context_size() > budget:
            raise ModelError('retrieval_budget', '相关原文超过本次阅读范围，请指定一处树木或地点后继续。')
        return parents, dropped
