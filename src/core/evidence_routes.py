"""Signed concept-to-original navigation learned from reviewed evidence.

Routes contain search concepts and source IDs/digests, never answer prose or
permission to publish. Every new query still receives normal evidence review.
"""
import sqlite3
import time
from src.core.integrity import fingerprint
from src.core.source_policy import load_policy


def source_record_id(citation):
    return fingerprint({'repository':citation.repository.value,'reference':citation.reference_id,
        'languages':sorted(citation.translations)})


class EvidenceRoutes:
    def __init__(self, store):
        self.store=store
        self.config=load_policy()['evidence_navigation']
        self.path=store.directory/'evidence_routes.db'
        with self.connect() as db:
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS routes USING fts5(route_id UNINDEXED, concepts, tokenize="unicode61")')

    def connect(self):
        return sqlite3.connect(str(self.path),timeout=5)

    def remember(self, analysis, citations, ttl=None):
        if not self.config['enabled'] or analysis.level.value=='LEVEL_D' or not citations:
            return False
        from src.core.source_index import SourceIndex
        groups=[p.keywords for p in analysis.question_parts] or [analysis.keywords]
        concepts=sorted(set(k for group in groups for values in group.values() for k in values))
        if not concepts:return False
        sources=[];until=time.time()+min(self.config['ttl_seconds'],ttl if ttl is not None else self.config['ttl_seconds'])
        for c in citations:
            record_id=source_record_id(c)
            saved=self.store.get('indexed_source',record_id)
            if not saved or saved.get('source_digest')!=c.source_digest:return False
            with self.store.connect() as db:
                expiry=db.execute("SELECT expires FROM records WHERE kind='indexed_source' AND id=?",(record_id,)).fetchone()
            if not expiry:return False
            until=min(until,expiry[0])
            sources.append({'record_id':record_id,'digest':c.source_digest})
        if until<=time.time():return False
        route={'concepts':concepts,'sources':sources}
        route_id=fingerprint(route)
        self.store.put('evidence_route',route_id,route,until-time.time(),expires_at=until)
        with self.connect() as db:
            db.execute('DELETE FROM routes WHERE route_id=?',(route_id,))
            db.execute('INSERT INTO routes VALUES(?,?)',(route_id,SourceIndex.searchable(' '.join(concepts))))
        return True

    def search(self, keywords):
        if not self.config['enabled']:return []
        from src.core.source_index import SourceIndex
        queries=list(dict.fromkeys(SourceIndex.concept_query(c) for values in keywords.values() for c in values
            if len(c.split())>=self.config['minimum_concept_words']))
        scores={}
        for query in queries:
            if not query:continue
            with self.connect() as db:
                # Expired pointers must not occupy the best-match limit. This
                # join filters navigation only; get() authenticates each hit.
                db.execute('ATTACH DATABASE ? AS signed',(str(self.store.database),))
                rows=db.execute('''SELECT route_id FROM routes JOIN signed.records AS r ON r.id=routes.route_id
                    WHERE routes MATCH ? AND r.kind='evidence_route' AND r.expires>?
                    ORDER BY bm25(routes) LIMIT ?''',
                    (query,time.time(),self.config['routes_per_concept'])).fetchall()
            for route_id, in rows:
                scores[route_id]=scores.get(route_id,0)+1
        # Match the whole concept plan before choosing a route. Stopping at
        # the first shared concept can discard the other side of a comparison.
        ranked=[]
        for route_id,score in scores.items():
            try:route=self.store.get('evidence_route',route_id)
            except (ValueError,TypeError):continue
            if route:ranked.append((score,route))
        ranked.sort(key=lambda pair:(-pair[0],len(pair[1]['sources'])))
        found=[]
        for _,route in ranked:
            for source in route['sources']:
                try:saved=self.store.get('indexed_source',source['record_id'])
                except (ValueError,TypeError):continue
                if saved and saved.get('source_digest')==source['digest'] and source['record_id'] not in found:
                    found.append(source['record_id'])
                    if len(found)>=self.config['candidates_per_part']:return found
        return found

    def backfill(self):
        """Existing signed decisions provide hints only, not reusable approvals."""
        from src.core.schema import QueryAnalysis, SourceCitation
        from src.core.orchestrator import MasterOrchestrator
        with self.store.connect() as db:
            originals=db.execute("SELECT id FROM records WHERE kind='indexed_source' AND expires>?",(time.time(),)).fetchall()
            decisions=db.execute("SELECT id,expires FROM records WHERE kind='decision' AND expires>?",(time.time(),)).fetchall()
        by_evidence={};report={'remembered':0,'skipped':0}
        for record_id, in originals:
            try:
                c=SourceCitation.model_validate(self.store.get('indexed_source',record_id))
                MasterOrchestrator().validate_candidates([c],[c.source_language]+[l for l in c.translations if l!=c.source_language])
                by_evidence[(c.content_kind,c.reference_id,c.source_digest)]=c
            except Exception:continue
        for record_id,expires in decisions:
            try:
                saved=self.store.get('decision',record_id)
                analysis=QueryAnalysis.model_validate(saved['analysis'])
                citations=[by_evidence[(kind,ref,digest)] for (kind,ref),digest in zip(saved['references'],saved['digests'])]
                if len(citations)!=len(saved['references']) or len(citations)!=len(saved['digests']):
                    raise ValueError('Incomplete evidence references')
                if self.remember(analysis,citations,ttl=expires-time.time()):report['remembered']+=1
                else:report['skipped']+=1
            except Exception:report['skipped']+=1
        return report
