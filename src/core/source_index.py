"""Local navigation index of signed, publisher-fetched original passages.

FTS matches never authorize an answer. Every returned record is authenticated,
expiry checked, digest checked and then assessed by the normal evidence gate.
"""
import re
import sqlite3
from itertools import zip_longest
from src.core.integrity import fingerprint
from src.core.analysis_agent import normalized, requires_primary_text
from src.core.schema import SourceCitation
from src.core.source_policy import load_policy


class SourceIndex:
    def __init__(self, store):
        self.store=store
        self.path=store.directory/'source_index.db'
        with self.connect() as db:
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(record_id UNINDEXED, body, tokenize="unicode61")')
            db.execute('CREATE TABLE IF NOT EXISTS navigation_metadata (version TEXT PRIMARY KEY)')
            db.execute('BEGIN IMMEDIATE')
            version=fingerprint({'algorithm':load_policy()['index_navigation_version'],
                'dictionary_identity_metadata':True,
                'suffixes':load_policy()['index_arabic_suffixes'],
                'minimum_stem_length':load_policy()['index_minimum_stem_length']})
            if not db.execute('SELECT 1 FROM navigation_metadata WHERE version=?',(version,)).fetchone():
                # Upgrade only derived search tokens, retaining signed originals
                # and their expiration. A forged record still fails authentication.
                for record_id, in db.execute('SELECT record_id FROM passages').fetchall():
                    saved=self.store.get('indexed_source',record_id)
                    if saved is None:
                        db.execute('DELETE FROM passages WHERE record_id=?',(record_id,))
                        continue
                    original=SourceCitation.model_validate(saved)
                    db.execute('UPDATE passages SET body=? WHERE record_id=?',
                        (self.navigation_body(original),record_id))
                db.execute('DELETE FROM navigation_metadata')
                db.execute('INSERT INTO navigation_metadata VALUES(?)',(version,))

    def connect(self):
        return sqlite3.connect(str(self.path),timeout=5)

    @staticmethod
    def searchable(text):
        words=re.findall(r'[^\W_]+',normalized(text.replace('ٰ','ا')))
        # Expand only navigation tokens. Original publisher text stays untouched.
        expanded=words+[w[2:] for w in words if w.startswith('ال') and len(w)>4]
        expanded += [w[:-1] for w in expanded if w.endswith('ا') and len(w)>=4 and re.fullmatch(r'[ء-ي]+',w)]
        expanded += [w[:-len(suffix)] for w in expanded
            for suffix in load_policy()['index_arabic_suffixes']
            if w.endswith(suffix) and len(w)-len(suffix)>=load_policy()['index_minimum_stem_length']
            and re.fullmatch(r'[ء-ي]+',w)]
        return ' '.join(expanded)

    def put(self, citation, ttl=None):
        from src.core.orchestrator import MasterOrchestrator
        MasterOrchestrator().validate_candidates([citation],[citation.source_language]+[l for l in citation.translations if l!=citation.source_language])
        record_id=fingerprint({'repository':citation.repository.value,'reference':citation.reference_id,
            'languages':sorted(citation.translations)})
        self.store.put('indexed_source',record_id,citation.model_dump(mode='json'),
            ttl if ttl is not None else load_policy()['source_cache_ttl_seconds'])
        body=self.navigation_body(citation)
        with self.connect() as db:
            db.execute('DELETE FROM passages WHERE record_id=?',(record_id,))
            db.execute('INSERT INTO passages VALUES(?,?)',(record_id,body))

    @classmethod
    def navigation_body(cls,citation):
        import json
        words=list(citation.translations.values())
        if citation.content_kind=='dictionary':
            for value in citation.published_attribution.values():
                metadata=json.loads(value)
                words.extend([metadata.get('title',''),metadata.get('field','')])
        return cls.searchable(' '.join(words))

    @staticmethod
    def concept_query(concept):
        """Require a complete concept, permitting each word's navigation forms."""
        stop=set(load_policy()['index_stopwords'])
        clauses=[]
        for word in re.findall(r'[^\W_]+',normalized(concept.replace('ٰ','ا'))):
            forms=list(dict.fromkeys(w for w in SourceIndex.searchable(word).split() if w not in stop))
            if forms:clauses.append('('+' OR '.join('"'+w+'"' for w in forms)+')')
        return ' AND '.join(clauses)

    def search(self, analysis, languages):
        from src.core.orchestrator import MasterOrchestrator
        groups=[p.keywords for p in analysis.question_parts] or [analysis.keywords]
        config=load_policy()['local_source_index']
        limit=config.get('candidates_per_level',{}).get(analysis.level.value,config['candidates_per_part'])
        if any(r.kind=='comparison' for p in analysis.question_parts for r in p.requirements):
            limit=max(limit,config['comparison_candidates_per_part'])
        found={}
        from src.core.evidence_routes import EvidenceRoutes
        try:routes=EvidenceRoutes(self.store)
        except (sqlite3.Error,ValueError):routes=None
        for group in groups:
            stop=set(load_policy()['index_stopwords'])
            words=list(dict.fromkeys(w for concepts in group.values() for concept in concepts
                for w in self.searchable(concept).split() if w not in stop))
            if not words:continue
            query=' OR '.join('"'+w+'"' for w in words)
            queues=[]
            with self.connect() as db:
                for concept in dict.fromkeys(c for values in group.values() for c in values):
                    specific=self.concept_query(concept)
                    if specific:
                        queues.append([row[0] for row in db.execute('SELECT record_id FROM passages WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?',
                            (specific,config['candidates_per_concept'])).fetchall()])
                broad=db.execute('SELECT record_id FROM passages WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?',
                    (query,limit*4)).fetchall()
            # Complete-concept queues serve all aspects together. Reviewed
            # navigation gets first chance; broad overlap remains a fallback.
            try:route_ids=routes.search(group) if routes else []
            except (sqlite3.Error,ValueError,TypeError,KeyError):route_ids=[]
            rows=list(dict.fromkeys(route_ids+[i for batch in zip_longest(*queues) for i in batch if i]+[r[0] for r in broad]))
            count=0
            for record_id in rows:
                saved=self.store.get('indexed_source',record_id)
                if saved is None:continue
                citation=SourceCitation.model_validate(saved)
                if requires_primary_text(analysis) and citation.content_kind not in ('quran','hadith'):continue
                if set(languages)-set(citation.translations) and not (citation.content_kind in ('article','book_excerpt','dictionary') and 'ar' in citation.translations):continue
                MasterOrchestrator().validate_candidates([citation],[citation.source_language]+[l for l in citation.translations if l!=citation.source_language])
                if citation.content_kind in ('quran','hadith') and citation.source_language!=languages[0]:
                    # Only display metadata changes; all authenticated publisher
                    # texts, URLs, passages and their digest remain identical.
                    citation=citation.model_copy(update={'source_language':languages[0],
                        'accredited_translation':citation.translations[languages[0]],
                        'source_url':citation.translation_urls[languages[0]]})
                MasterOrchestrator().validate_candidates([citation],languages)
                key=(citation.repository.value,citation.reference_id)
                if key in found:continue
                found[key]=citation.model_copy(update={'is_offline_cached':True})
                count+=1
                if count>=limit:break
        return list(found.values())
