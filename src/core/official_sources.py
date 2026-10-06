"""Read-only accredited MCP retrieval with authenticated local caching.
Published Quran/Hadith, original articles and readable book pages are evidence.
Library descriptions remain navigation metadata, never quoted answer evidence.
"""
import json
import re
import sqlite3
import time
import math
from collections import Counter
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
import requests
from src.core.analysis_agent import AnalysisAgent, SafetyStop, normalized, requires_primary_text
from src.core.integrity import AuthenticatedStore, fingerprint
from src.core.schema import SourceCitation, SourceRepository, SourcePassage, DiscoveryRanking
from src.core.source_policy import ROOT, load_policy, validate_source_url

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class OfficialSources:
    def __init__(self, deadline, store=None):
        self.deadline = deadline
        self.policy = load_policy()
        self.store = store or AuthenticatedStore()
        self.unavailable = []
        self.transient_failure = False

    def record_failure(self, exc):
        """A failed connection is not evidence that a source has no answer."""
        status=getattr(getattr(exc,'response',None),'status_code',None)
        if isinstance(exc,(requests.Timeout,requests.ConnectionError)) or status==429 or (status and status>=500):
            self.transient_failure=True

    def timeout(self):
        remaining = min(self.deadline,getattr(self,'network_deadline',self.deadline)) - time.monotonic()
        if remaining <= 0:
            raise SafetyStop('deadline_exceeded')
        return min(remaining, self.policy['api_timeout_seconds'])

    def call(self, name, arguments):
        key=fingerprint({'tool':name,'arguments':arguments,'format':1})
        if name in ('search','fetch'):
            saved=self.store.get('remote_catalog',key)
            if saved:return saved
        response = requests.post(self.policy['mcp_endpoint'],
            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':arguments}},
            headers={'Accept':'application/json, text/event-stream'}, timeout=self.timeout(),allow_redirects=False)
        response.raise_for_status()
        text = response.content.decode('utf-8')
        if response.headers.get('content-type','').startswith('text/event-stream'):
            records = [line[6:] for line in text.splitlines() if line.startswith('data: ')]
            messages = [json.loads(line) for line in records]
            message = next((item for item in messages if item.get('id') == 1),None)
        else:
            message = json.loads(text)
        if not message or message.get('error'):
            raise SafetyStop('source_api_error')
        result = message['result']
        if result.get('isError'):
            raise SafetyStop('source_tool_error')
        if time.monotonic() >= self.deadline:
            raise SafetyStop('deadline_exceeded')
        if name in ('search','fetch'):
            data=result.get('structuredContent')
            if not data:
                try:data=json.loads(next(c['text'] for c in result.get('content',[]) if c.get('type')=='text'))
                except (ValueError,StopIteration):data={}
            # Never retain empty/error searches as evidence that nothing exists.
            if data.get('results') or (name=='fetch' and data.get('url')):
                self.store.put('remote_catalog',key,result,self.policy['source_cache_ttl_seconds'])
        return result

    @staticmethod
    def text(result):
        return '\n'.join(c['text'] for c in result.get('content',[]) if c.get('type') == 'text')

    def check_link(self, url):
        # Follow redirects ourselves so each destination passes the exact-host policy.
        for _ in range(4):
            validate_source_url(url)
            with requests.get(url, timeout=self.timeout(),allow_redirects=False,stream=True) as response:
                if response.status_code in (301,302,303,307,308):
                    url = urljoin(url,response.headers.get('Location',''))
                    continue
                if response.status_code != 200:
                    raise SafetyStop('citation_link_unavailable')
                return utcnow()
        raise SafetyStop('citation_redirect_loop')

    def get(self, kind, ref, languages):
        key = fingerprint({'kind':kind,'reference':ref,'languages':languages,'format_version':5})
        cached = None if getattr(self,'force_refresh',False) else self.store.get('source',key)
        if cached:
            citation = SourceCitation.model_validate(cached)
            if citation.source_digest != self.digest(citation):
                raise SafetyStop('source_cache_integrity_failed')
            return citation.model_copy(update={'is_offline_cached':True})
        if kind == 'quran':
            whole=re.fullmatch(r'(\d+):1-(\d+)',ref)
            if whole:
                from src.core.chapter_reader import ChapterReader
                citation=ChapterReader(self).read(whole.group(1),languages,int(whole.group(2)))
            else:citation = self.quran(ref,languages)
        elif kind=='quran_surah':
            from src.core.chapter_reader import ChapterReader
            citation=ChapterReader(self).read(ref,languages)
        elif kind == 'hadith':
            citation = self.hadith(ref,languages)
        elif kind in ('article', 'library', 'shamela', 'dawa', 'dictionary'):
            # Bare legacy IDs are catalog metadata, not observed content links.
            if (kind=='article' and not ref.startswith('https://') or
                    kind=='library' and not re.fullmatch(r'library:\d+:[a-z]{2,3}',ref)):
                return None
            from src.core.publication_reader import PublicationReader
            reader = PublicationReader(self)
            if kind=='dictionary':
                from src.core.dictionary_reader import DictionaryReader
                found=DictionaryReader(self).read(ref,languages)
            else:
                found = reader.article(ref) if kind == 'article' else (reader.shamela(ref) if kind=='shamela' else (reader.dawa(ref) if kind=='dawa' else reader.library(ref)))
            checked={url:self.check_link(url) for url in dict.fromkeys(url for item in found for url in item.translation_urls.values())}
            for item in found:
                item.link_checked_at = checked[item.source_url]
                item.source_digest = self.digest(item)
                item.provenance_verified = True
                item_key = fingerprint({'kind':item.content_kind,'reference':item.reference_id,'languages':languages,'format_version':5})
                self.store.put('source',item_key,item.model_dump(mode='json'),self.policy['source_cache_ttl_seconds'])
                from src.core.source_index import SourceIndex
                SourceIndex(self.store).put(item)
            return found
        else:
            # A book landing page, title or description is not a verified book excerpt.
            return None
        if not citation:
            return None
        checks = [self.check_link(url) for url in dict.fromkeys(citation.translation_urls.values())]
        citation.link_checked_at = min(checks)
        citation.source_digest = self.digest(citation)
        citation.provenance_verified = True
        self.store.put('source',key,citation.model_dump(mode='json'),self.policy['source_cache_ttl_seconds'])
        if kind=='quran_surah':
            range_key=fingerprint({'kind':'quran','reference':citation.reference_id,'languages':languages,'format_version':5})
            self.store.put('source',range_key,citation.model_dump(mode='json'),self.policy['source_cache_ttl_seconds'])
        from src.core.source_index import SourceIndex
        SourceIndex(self.store).put(citation)
        return citation

    @staticmethod
    def digest(citation):
        return fingerprint({'repository':citation.repository.value,'reference':citation.reference_id,
            'texts':citation.translations,'urls':citation.translation_urls,
            'grade':citation.scholarly_grading,'kind':citation.content_kind,
            'passages':{l:[p.model_dump(mode='json') for p in ps] for l,ps in citation.passages.items()},
            'attribution':citation.published_attribution})

    def quran(self, ref, languages):
        surah,ayah = map(int,ref.split(':'))
        if not 1 <= surah <= 114 or not 1 <= ayah <= 286:
            raise SafetyStop('invalid_verse_reference')
        texts, urls = {}, {}
        arabic = None
        passages = {}
        for language in [l for l in languages if l != 'ar'] or ['en']:
            metadata_key = fingerprint({'quran_translation_language':language})
            metadata = self.store.get('catalog',metadata_key)
            if metadata is None:
                response = requests.get(self.policy['quran_api']+'/translations/list/'+language,
                    timeout=self.timeout(),allow_redirects=False)
                response.raise_for_status()
                metadata = response.json()['translations']
                self.store.put('catalog',metadata_key,metadata,self.policy['source_cache_ttl_seconds'])
            matching = [t for t in metadata if t.get('language_iso_code')==language]
            if not matching:
                return None
            preferred = self.policy['preferred_quran_translations'].get(language)
            translation = next((t for t in matching if t['key']==preferred),matching[0])
            key = translation['key']
            if not re.fullmatch(r'[a-z0-9_]+',key):
                raise SafetyStop('invalid_translation_key')
            response = requests.get(self.policy['quran_api']+f'/translation/aya/{key}/{surah}/{ayah}',
                timeout=self.timeout(),allow_redirects=False)
            response.raise_for_status()
            verse = response.json()['result']
            if int(verse['sura'])!=surah or int(verse['aya'])!=ayah:
                raise SafetyStop('wrong_verse_returned')
            if not verse.get('arabic_text') or not verse.get('translation'):
                return None
            if arabic is not None and arabic!=verse['arabic_text']:
                raise SafetyStop('scripture_versions_disagree')
            arabic = verse['arabic_text']
            texts[language] = verse['translation']
            # Footnotes are publisher commentary, kept separately from the quoted verse.
            if verse.get('footnotes'):
                texts[language] += '\n\n'+verse['footnotes']
            # Documented publisher browse route using its actual catalog key, checked before publication.
            urls[language] = self.policy['quran_page_template'].format(language=language,
                translation_key=key,surah=surah,ayah=ayah)
            passages[language]=[SourcePassage(kind='quran',text=verse['translation'],reference_id=ref,source_url=urls[language])]
            if verse.get('footnotes'):
                passages[language].append(SourcePassage(kind='commentary',text=verse['footnotes'],reference_id=ref,source_url=urls[language]))
        texts['ar'] = arabic
        urls['ar'] = next(iter(urls.values()))
        passages['ar']=[SourcePassage(kind='quran',text=arabic,reference_id=ref,source_url=urls['ar'])]
        citation = self.citation(SourceRepository.QURAN_ENC_PUBLISHED,ref,texts,urls,languages,'quran')
        citation.passages={l:passages[l] for l in languages}
        citation.retrieval_endpoint=self.policy['quran_api']
        return citation

    def hadith(self, ref, languages):
        text = self.text(self.call('get_hadith',{'id':ref,'language':languages}))
        sections = re.split(r'(?m)^\[([a-z]{2,3}) — [^\n]+\]\n',text)
        # The publisher omits language section headers for a single-language result.
        # Establish its language from the returned citation route, never a guess.
        if len(sections)==1 and len(languages)==1:
            link=re.search(r'^Source: (https://\S+)',text,re.M)
            if not link:
                return None
            url=validate_source_url(link.group(1))
            if urlsplit(url).path.split('/')[1]!=languages[0]:
                return None
            sections=['',languages[0],text]
        texts,urls,grades,passages,attributions = {},{},{},{},{}
        for i in range(1,len(sections)-1,2):
            language, section = sections[i:i+2]
            if language not in languages:
                continue
            narration = re.search(r'\[EXACT\][^\n]*\n(.*?)\n\[/EXACT\]',section,re.S)
            attribution = re.search(r'\[ATTRIBUTION\][^\n]*\n(.*?)\n\[/ATTRIBUTION\]',section,re.S)
            link = re.search(r'^Source: (https://\S+)',section,re.M)
            if not narration or not attribution or not link:
                continue
            grade = re.search(r'^Grade: (.+)$',attribution.group(1),re.M)
            if not grade or grade.group(1).strip() not in self.policy['authenticated_hadith_grades']:
                continue
            # Published explanations are separate exact paragraphs, never synthesized by AI.
            commentary = re.search(r'\[COMMENTARY\][^\n]*\nExplanation:[ \t]*\n?(.*?)\n\[/COMMENTARY\]',section,re.S)
            texts[language] = narration.group(1) + ('\n\n'+commentary.group(1).replace('\n\nBenefits:\n','\n\n') if commentary else '')
            url=validate_source_url(link.group(1))
            route=urlsplit(url)
            if route.hostname!='hadeethenc.com' or route.path.rstrip('/')!=f'/{language}/browse/hadith/{ref}':
                raise SafetyStop('wrong_hadith_reference_returned')
            urls[language] = url
            grades[language] = grade.group(1).strip()
            attributions[language]=attribution.group(1)
            passages[language]=[SourcePassage(kind='hadith',text=narration.group(1),reference_id=ref,source_url=urls[language])]
            if commentary:
                passages[language].append(SourcePassage(kind='commentary',text=commentary.group(1).replace('\n\nBenefits:\n','\n\n'),reference_id=ref,source_url=urls[language]))
        if set(languages) - set(texts):
            return None
        citation=self.citation(SourceRepository.HADEETH_ENC,ref,texts,urls,languages,'hadith',grades[languages[0]])
        citation.passages=passages
        citation.published_attribution=attributions
        return citation

    def citation(self,repo,ref,texts,urls,languages,kind,grade=None):
        main = languages[0]
        return SourceCitation(repository=repo,reference_id=ref,arabic_text=texts['ar'],
            accredited_translation=texts[main],source_language=main,translations={l:texts[l] for l in languages},
            translation_urls={l:urls[l] for l in languages},source_url=urls[main],
            scholarly_grading=grade,is_offline_cached=False,content_kind=kind,
            retrieval_endpoint=self.policy['mcp_endpoint'],retrieved_at=utcnow())

    def discover(self,analysis):
        # Local old corpus is an index only. Its texts/URLs/handwritten summaries are never published.
        groups=[p.keywords for p in analysis.question_parts] or [analysis.keywords]
        queues=[];catalog={}
        search_key=fingerprint({'keywords':groups,'requirements':[p.model_dump(mode='json') for p in analysis.question_parts],'level':analysis.level.value,'version':14})
        cached_search=self.store.get('search_catalog',search_key)
        if cached_search is not None:
            return [tuple(r) for r in cached_search]
        db_path = ROOT / 'data/cache/sharia_sources_cache.db'
        if db_path.exists():
            try:
                with sqlite3.connect(f'file:{db_path}?mode=ro',uri=True) as db:
                    columns = [r[1] for r in db.execute('PRAGMA table_info(quran_cache)')]
                    # Support the current cache schema without treating it as trusted evidence.
                    if {'surah_number','ayah_number','translation','arabic_text','surah_name_ar','surah_name_en'} <= set(columns):
                        rows = db.execute("SELECT surah_number,ayah_number,translation,arabic_text,surah_name_ar,surah_name_en FROM quran_cache WHERE language='en'").fetchall()
                        valid={(r[0],r[1]) for r in rows}
                        catalog.update({('quran',f'{r[0]}:{r[1]}'):{'title':r[3]+' '+r[2],'origin':'local_index'} for r in rows})
                        for keywords in groups:
                            ranked=self.rank_local_index(rows,keywords)
                            contextual=[]
                            for kind,ref in ranked:
                                surah,ayah=map(int,ref.split(':'))
                                contextual.append((kind,ref))
                                # Nearby verses may resolve pronouns or complete
                                # an exception; all still require fresh retrieval
                                # and full semantic assessment.
                                contextual.extend(('quran',f'{surah}:{ayah+offset}') for offset in
                                    self.policy['quran_context_offsets'] if (surah,ayah+offset) in valid)
                            queues.append(contextual)
            except sqlite3.Error:
                self.unavailable.append('local_index')
        pool = ThreadPoolExecutor(max_workers=self.policy['source_workers'])
        submitted={};grouped=[]
        from src.core.publication_reader import PublicationReader
        reader=PublicationReader(self)
        from src.core.dictionary_reader import DictionaryReader
        dictionary=DictionaryReader(self)
        article_tasks=[]
        # First round gives every collection a worker before additional synonyms.
        plans=[]
        for group_id,keywords in enumerate(groups):
            by_collection=[]
            for corpus in ('library','hadith','quran'):
                concepts=[(language,k) for language in ('ar','en')
                    for k in keywords.get(language,[])[:self.policy['remote_keywords_per_part']]]
                by_collection.append([(corpus,language,k) for language,k in concepts])
            by_collection.append([('article','ar',k) for k in keywords.get('ar',[])[:self.policy['publication_retrieval']['article_keywords_per_part']]])
            by_collection.append([('shamela','ar',k) for k in keywords.get('ar',[])[:self.policy['shamela_retrieval']['keywords_per_part']]])
            by_collection.append([('dawa','ar',k) for k in keywords.get('ar',[])[:self.policy['dawa_retrieval']['keywords_per_part']]])
            by_collection.append([('quranpedia','ar',k) for k in keywords.get('ar',[])[:self.policy['quranpedia_retrieval']['keywords_per_part']]])
            focused=analysis.model_copy(update={'question_parts':[],'keywords':keywords})
            by_collection.append([('dictionary','ar',k) for k in dictionary.plan_terms(focused)])
            plans.append([(group_id,*entry) for batch in zip_longest(*by_collection) for entry in batch if entry])
        for batch in zip_longest(*plans):
            for entry in batch:
                if not entry:continue
                group_id,corpus,language,keyword=entry
                key=(corpus,language,normalized(keyword))
                if key not in submitted:
                    if corpus=='article':submitted[key]=pool.submit(reader.search_articles,keyword)
                    elif corpus=='shamela':submitted[key]=pool.submit(reader.search_shamela,keyword)
                    elif corpus=='dawa':submitted[key]=pool.submit(reader.search_dawa,keyword)
                    elif corpus=='quranpedia':submitted[key]=pool.submit(reader.search_quranpedia,keyword)
                    elif corpus=='dictionary':submitted[key]=pool.submit(dictionary.search,keyword)
                    else:submitted[key]=pool.submit(self.call,'search',{'query':keyword,'sources':[corpus],
                        'language':language,'limit':self.policy['discovery_search_limit']})
                if corpus in ('article','shamela','dawa','quranpedia','dictionary'):article_tasks.append((group_id,corpus,submitted[key]))
                else:grouped.append((group_id,submitted[key]))
        tasks=list(submitted.values())
        remote=[[] for _ in groups]
        try:
            done,pending = wait(tasks,timeout=None if math.isinf(self.deadline) else
                max(0,min(self.deadline,getattr(self,'network_deadline',self.deadline),time.monotonic()+self.policy['discovery_budget_seconds'])-time.monotonic()))
            for group_id,task in grouped:
                if task not in done:
                    task.cancel()
                    self.unavailable.append('search_timeout')
                    continue
                try:
                    references=[]
                    result=task.result()
                    data=result.get('structuredContent')
                    if not data:
                        data=json.loads(next(c['text'] for c in result.get('content',[]) if c.get('type')=='text'))
                    for item in data.get('results',[]):
                        validate_source_url(item['url'])
                        parts=item['id'].split(':')
                        if parts[0]=='hadith' and parts[1].isdigit():
                            references.append(('hadith',parts[1]))
                        elif parts[0]=='quran' and len(parts)>=3 and parts[1].isdigit() and parts[2].isdigit():
                            references.append(('quran',f'{parts[1]}:{parts[2]}'))
                        elif parts[0]=='library':
                            # Preserve the opaque API ID and open the publication.
                            references.append(('library',item['id']))
                        else:continue
                        catalog[references[-1]]={'title':item.get('title',''),'origin':'publisher_catalog'}
                    remote[group_id].append(references)
                except Exception as exc:
                    self.record_failure(exc)
                    self.unavailable.append('source_search_failed')
            for group_id,corpus,task in article_tasks:
                if task not in done:
                    self.unavailable.append(corpus+'_search_timeout')
                    continue
                try:
                    remote[group_id].append(task.result())
                except Exception as exc:
                    self.record_failure(exc)
                    self.unavailable.append(corpus+'_search_failed')
        finally:
            pool.shutdown(wait=False,cancel_futures=True)
        queues.extend(list(dict.fromkeys(r for batch in zip_longest(*group) for r in batch if r)) for group in remote)
        # Round-robin across aspect rankings and remote searches. A broad first
        # topic must not consume the entire candidate budget before later parts.
        observed=list(dict.fromkeys(r for batch in zip_longest(*queues) for r in batch if r))
        if requires_primary_text(analysis):
            observed=[r for r in observed if r[0] in ('quran','hadith')]
        pool=observed[:self.policy['discovery_catalog_limit']]
        references=pool[:self.policy['max_candidates']]
        # Rank navigation metadata before the fetch cap. It does not prove any claim.
        if self.policy.get('discovery_ranking_enabled',True) and any(catalog.get(r,{}).get('title') and catalog.get(r,{}).get('origin')=='publisher_catalog' for r in pool):
            try:
                ranking=AnalysisAgent(min(self.deadline,time.monotonic()+
                    self.policy['discovery_ranking_timeout_seconds'])).request(self.policy['discovery_ranking_instruction'],
                    {'level':analysis.level.value,'question_parts':[p.model_dump(mode='json') for p in analysis.question_parts],
                     'keywords':analysis.keywords,'max_fetches':self.policy['max_candidates'],
                     'catalog':[{'candidate_id':i,'kind':r[0],**catalog.get(r,{'title':'','origin':'navigation'})} for i,r in enumerate(pool)]},DiscoveryRanking)
                ids=ranking.candidate_ids
                if len(set(ids))!=len(ids) or any(i<0 or i>=len(pool) for i in ids):
                    raise SafetyStop('invalid_discovery_ranking')
                references=[pool[i] for i in ids[:self.policy['max_candidates']]]
            except SafetyStop as exc:
                if str(exc)=='quota_finished' or time.monotonic()>=self.deadline:
                    raise
                self.unavailable.append('catalog_ranking_unavailable')
        if references and not self.unavailable:
            self.store.put('search_catalog',search_key,references,self.policy['source_cache_ttl_seconds'])
        return references

    def rank_local_index(self,rows,keywords):
        """Arabic/English keyword ranking of discovery records, never answer evidence."""
        stop=set(self.policy['index_stopwords'])
        aliases=self.policy['index_keyword_aliases']
        def tokens(value):
            raw=re.findall(r'[^\W_]+',normalized(value))
            expanded=list(raw)
            for word in raw:
                for prefix in self.policy['index_arabic_prefixes']:
                    if word.startswith(prefix) and len(word)-len(prefix)>=self.policy['index_minimum_stem_length']:
                        expanded.append(word[len(prefix):])
                        break
            return expanded
        query=Counter()
        for language in ('ar','en'):
            for concept in keywords.get(language,[]):
                for token in tokens(concept):
                    if token not in stop:
                        query[token]+=1
                        query.update(aliases.get(token,[]))
        documents=[Counter(tokens((r[2] or '')+' '+(r[3] or ''))) for r in rows]
        frequency={t:sum(t in d for d in documents) for t in query}
        scores=[]
        for row,doc in zip(rows,documents):
            title=set(tokens((row[4] or '')+' '+(row[5] or '')))-stop
            title_match=sum(t in title for t in query)
            score=title_match*self.policy['index_title_weight']
            length=sum(doc.values())
            for t in query:
                if t in doc:
                    idf=math.log(1+len(rows)/(1+frequency[t]))
                    score+=query[t]*idf*doc[t]/(doc[t]+1.2*(.25+.75*length/40))
            if score: scores.append((score,row))
        scores.sort(key=lambda pair:pair[0],reverse=True)
        return [('quran',f'{r[0]}:{r[1]}') for score,r in scores[:self.policy['local_index_limit']]]

    def shortlist(self, analysis, candidates):
        """Bound full-passage model input without cropping originals or losing a part's queue."""
        limit=self.policy['assessment_candidate_limit']
        budget=self.policy['assessment_character_limit']
        if len(candidates)<=limit and sum(len(t) for c in candidates for t in c.translations.values())<=budget:
            return candidates
        groups=[p.keywords for p in analysis.question_parts] or [analysis.keywords]
        queues=[]
        for keywords in groups:
            for kind in self.policy['assessment_source_order'][analysis.level.value]:
                subset=[(i,c) for i,c in enumerate(candidates) if c.content_kind==kind]
                rows=[(1,i,c.translations.get('en',''),c.translations.get('ar',''),'','') for i,c in subset]
                ranked=self.rank_local_index(rows,keywords)
                indices=[int(ref.split(':')[1]) for _,ref in ranked]
                indices.extend(i for i,_ in subset if i not in indices)
                queues.append(indices)
        chosen=[];size=0
        for batch in zip_longest(*queues):
            for index in batch:
                if index is None or index in chosen:
                    continue
                cost=sum(len(t) for t in candidates[index].translations.values())
                if len(chosen)<limit and size+cost<=budget:
                    chosen.append(index);size+=cost
        return [candidates[i] for i in chosen]

    def retrieve(self,analysis,languages,explicit=None):
        self.search_analysis = analysis
        if not explicit:
            from src.core.chapter_reader import ChapterReader
            chapters=ChapterReader(self).identify(analysis)
            if chapters:
                citations=[self.get(kind,ref,languages) for kind,ref in chapters]
                if all(citations):
                    self.has_deferred_references=False
                    return citations
        if self.policy['local_source_index']['enabled'] and not explicit and not getattr(self,'full_retrieval',False):
            from src.core.source_index import SourceIndex
            local=SourceIndex(self.store).search(analysis,languages)
            # A warm topical passage cannot crowd out the authoritative
            # dictionary on a definition/translation task. Retrieval remains
            # subject to the same independent support and prose review gates.
            if re.search(self.policy['dictionary_retrieval']['request_pattern'],analysis.intent+' '+
                    ' '.join(p.question+' '+' '.join(r.description for r in p.requirements) for p in analysis.question_parts),re.I):
                if not any(c.content_kind=='dictionary' for c in local) and not requires_primary_text(analysis):
                    from src.core.dictionary_reader import DictionaryReader
                    dictionary=DictionaryReader(self)
                    fetched=[]
                    for term in dictionary.plan_terms(analysis):
                        try:
                            for kind,ref in dictionary.search(term):fetched.extend(self.get(kind,ref,languages) or [])
                        except (requests.RequestException,ValueError,SafetyStop) as exc:
                            self.record_failure(exc);self.unavailable.append('dictionary_unavailable')
                    local=fetched+local
            if local:
                self.has_deferred_references=True
                return local
        if not hasattr(self,'network_deadline'):
            self.network_deadline=(self.deadline if math.isinf(self.deadline) else
                min(self.deadline,time.monotonic()+self.policy['local_source_index']['remote_budget_seconds']))
        references = explicit or self.discover(analysis)
        # Start small for a single established explanation. A failed semantic
        # assessment triggers the full observed plan before search refinement.
        config=self.policy['progressive_retrieval']
        progressive=(not explicit and not getattr(self,'full_retrieval',False)
            and analysis.level.value in config['levels'] and len(analysis.question_parts)==1
            and bool(analysis.question_parts[0].requirements)
            and all(r.kind in config['requirement_kinds'] for r in analysis.question_parts[0].requirements))
        self.has_deferred_references=progressive and len(references)>config['initial_candidates']
        if progressive:
            references=references[:config['initial_candidates']]
        candidates=[]
        pool=ThreadPoolExecutor(max_workers=self.policy['source_workers'])
        tasks=[pool.submit(self.get,kind,ref,languages) for kind,ref in references]
        try:
            until=min(self.deadline,getattr(self,'network_deadline',self.deadline))
            done,pending=wait(tasks,timeout=None if math.isinf(until) else max(0,until-time.monotonic()))
            for task in tasks:
                if task not in done:
                    task.cancel()
                    self.unavailable.append('source_timeout')
                    continue
                try:
                    citation=task.result()
                    if citation:
                        candidates.extend(citation if isinstance(citation,list) else [citation])
                except Exception as exc:
                    self.record_failure(exc)
                    self.unavailable.append('source_unavailable_or_invalid')
        finally:
            pool.shutdown(wait=False,cancel_futures=True)
        if time.monotonic() >= self.deadline:
            raise SafetyStop('deadline_exceeded')
        if not candidates:
            if self.transient_failure:
                raise SafetyStop('source_service_unavailable')
            raise SafetyStop('no_approved_passages_in_required_languages')
        if progressive:
            concise=[c for c in candidates if sum(len(t) for t in c.translations.values())
                <=config['initial_passage_characters']]
            if concise and len(concise)<len(candidates):
                self.has_deferred_references=True
                candidates=concise
        return candidates
