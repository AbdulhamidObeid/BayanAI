"""Exact Jamharah dictionary definitions and observed publisher translations.

Source law: docs/guides/10_immutability_constitution_and_source_lock.md.
Protected glossary words discover entries; glossary definitions are never
substituted for published evidence. Homonyms still need semantic assessment.
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime,timezone
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
import requests
from src.core.analysis_agent import normalized,SafetyStop
from src.core.integrity import fingerprint
from src.core.publication_reader import PublicationReader
from src.core.schema import SourceCitation,SourcePassage,SourceRepository
from src.core.source_policy import ROOT,validate_source_url


def term_normalized(text):
    return normalized(text).replace('ـ','')


def catalog_word_key(text):
    word=term_normalized(text)
    return word[2:] if word.startswith('ال') else word


class DictionaryReader:
    def __init__(self,sources):
        self.sources=sources
        self.config=sources.policy['dictionary_retrieval']
        self.reader=PublicationReader(sources)

    def route(self,url):
        validate_source_url(url)
        match=re.fullmatch(self.config['route_pattern'],urlsplit(url).path)
        if urlsplit(url).hostname!='islamic-content.com' or not match:
            raise SafetyStop('wrong_dictionary_route')
        return match

    def plan_terms(self,analysis):
        """Bound navigation by exact protected aliases, not benchmark queries."""
        terms=json.loads((ROOT/'configs/sharia_lexicon.json').read_text())['terms']
        groups=[analysis.keywords]+[p.keywords for p in analysis.question_parts]
        found=[]
        for group in groups:
            text=term_normalized(' '.join(c for values in group.values() for c in values))
            count=0
            for key,term in terms.items():
                aliases=[key,term.get('transliteration',''),term['arabic'],catalog_word_key(term['arabic'])]
                if any(alias and re.search(r'(?<!\w)'+re.escape(term_normalized(alias))+r'(?!\w)',text) for alias in aliases):
                    if term['arabic'] not in found:found.append(term['arabic'])
                    count+=1
                    if count>=self.config['terms_per_part']:break
        return found

    def search(self,term):
        key=fingerprint({'dictionary_search':term_normalized(term),'version':2})
        cached=self.sources.store.get('dictionary_catalog',key)
        if cached is not None:return [tuple(item) for item in cached]
        # Preserve Arabic hamza spelling in publisher requests. Normalized
        # strings are for local matching only. Full-word search can be more
        # precise than the UI's article-stripped fallback (e.g. الوحي).
        query=re.sub(r'[\u064b-\u065f\u0670]','',unicodedata.normalize('NFC',term)).replace('ـ','')
        queries=[query]+([query[2:]] if query.startswith('ال') else [])
        entries=[]
        for query in queries:
            response=requests.get(self.config['search_url'],params={'query':query},
                headers=self.config['headers'],timeout=self.sources.timeout(),allow_redirects=False)
            response.raise_for_status()
            soup=BeautifulSoup(response.json()['table_data'],'html.parser')
            for anchor in soup.select('a[href]'):
                url=urljoin(self.config['search_url'],anchor['href'])
                try:route=self.route(url)
                except (ValueError,SafetyStop):continue
                if route.group('language'):continue
                title=anchor.get_text(' ',strip=True)
                if url not in [u for _,u in entries]:entries.append((title,url))
            if any(catalog_word_key(title)==catalog_word_key(term) for title,_ in entries):break
        # Exact title matches precede compounds and homonyms; title is not proof.
        entries.sort(key=lambda item:catalog_word_key(item[0])!=catalog_word_key(term))
        refs=[('dictionary',url) for _,url in entries[:self.config['entries_per_term']]]
        if refs:self.sources.store.put('dictionary_catalog',key,refs,self.sources.policy['source_cache_ttl_seconds'])
        return refs

    def document(self,url):
        expected=self.route(url)
        key=fingerprint({'dictionary_document':url,'version':1})
        saved=None if getattr(self.sources,'force_refresh',False) else self.sources.store.get('publication_html',key)
        if saved:
            import base64
            body=base64.b64decode(saved['body']);canonical=saved['url']
        else:
            body,canonical=self.reader.download(url,self.sources.policy['max_source_bytes'])
            import base64
            self.sources.store.put('publication_html',key,{'body':base64.b64encode(body).decode(),'url':canonical},self.sources.policy['source_cache_ttl_seconds'])
        actual=self.route(canonical)
        if (actual.group('id'),actual.group('language'))!=(expected.group('id'),expected.group('language')):
            raise SafetyStop('wrong_dictionary_entry_returned')
        soup=BeautifulSoup(body,'html.parser')
        title=soup.select_one('main h1')
        if title is None:raise SafetyStop('dictionary_original_missing')
        language=actual.group('language') or 'ar'
        if language!='ar':
            breadcrumb=soup.select_one('main .breadcrumbs')
            label=breadcrumb.get_text(' ',strip=True) if breadcrumb else ''
            if not any(word in label for word in self.config['language_labels'].get(language,[])):
                raise SafetyStop('wrong_dictionary_language')
        sections={}
        scripture_fields=set()
        for position,section in enumerate(soup.select(self.config['section_selector'])):
            # Skip the whole marked scripture field; never delete words or
            # translate an embedded verse under the guise of commentary.
            contains_scripture=bool(section.select_one(self.config['scripture_selector']))
            for control in section.select('script,style,form,button'):control.decompose()
            text=section.get_text(' ',strip=True)
            contains_scripture=contains_scripture or bool(re.search(self.config['scripture_intro_pattern'],term_normalized(text)))
            if not self.config['minimum_characters']<=len(text)<=self.config['maximum_characters']:continue
            if any('\ue000'<=c<='\uf8ff' or c=='\ufffd' for c in text):continue
            letters=[c for c in text if c.isalpha()]
            if language=='ar' and (not letters or sum('\u0600'<=c<='\u06ff' for c in letters)/len(letters)<.6):continue
            # A field's heading aligns only that same publisher field across
            # languages. Different definitions/homonyms are not merged.
            heading=section.find_previous_sibling('h2')
            label=heading.get_text(' ',strip=True) if heading else ('publisher_definition' if section.name=='blockquote' else 'definition')
            if heading is None and section.name!='blockquote':
                container=section.find_parent(class_='p-20')
                publisher=container.select_one('h5') if container else None
                if publisher:label=publisher.get_text(' ',strip=True)
            field=' '.join(term_normalized(label).replace(':','').split())
            if field in sections:field+=' '+str(position)
            sections[field]=text
            if contains_scripture:scripture_fields.add(field)
        links={}
        for a in soup.select('main #related a[href]'):
            linked=urljoin(canonical,a['href'])
            try:route=self.route(linked)
            except (ValueError,SafetyStop):continue
            if route.group('id')==actual.group('id'):
                links[route.group('language') or 'ar']=linked
        return {'url':canonical,'language':language,'title':title.get_text(' ',strip=True),
            'sections':sections,'scripture_fields':scripture_fields,'links':links,'sha256':hashlib.sha256(body).hexdigest()}

    def read(self,url,languages):
        base=self.document(url)
        if base['language']!='ar':raise SafetyStop('dictionary_arabic_entry_required')
        documents={'ar':base}
        for language in languages:
            if language=='ar' or language not in base['links']:continue
            try:
                doc=self.document(base['links'][language])
                if doc['language']!=language:raise SafetyStop('wrong_dictionary_language')
                documents[language]=doc
            except (requests.RequestException,ValueError,SafetyStop) as exc:
                self.sources.record_failure(exc);self.sources.unavailable.append('dictionary_translation_unavailable')
        results=[]
        for field,original in base['sections'].items():
            texts={'ar':original};urls={'ar':base['url']};attribution={}
            for language,doc in documents.items():
                if field not in doc['sections']:continue
                texts[language]=doc['sections'][field];urls[language]=doc['url']
                attribution[language]=json.dumps({'title':doc['title'],'field':field,
                    'document_sha256':doc['sha256'],'published_translation':language!='ar',
                    'contains_scripture':field in doc['scripture_fields']},ensure_ascii=False)
            if field in base['scripture_fields']:
                # Preserve the entire publication as a native original, but do
                # not align or translate mixed scripture/definition fields.
                texts={'ar':original};urls={'ar':base['url']};attribution={'ar':attribution['ar']}
            from src.agents.terminology_preserver import TerminologyPreserverAgent
            preserver=TerminologyPreserverAgent()
            # A prohibited published gloss is held as its whole original field,
            # never rewritten or silently passed to the answer writer.
            held=[l for l,t in texts.items() if preserver.audit_published_text(t,l)[1]['status']!='APPROVED_SAFE']
            if held:
                self.sources.unavailable.append('dictionary_field_terminology_review_required')
                if 'ar' in held:continue
                texts={'ar':original};urls={'ar':base['url']};attribution={'ar':attribution['ar']}
            main=languages[0] if languages[0] in texts else 'ar'
            ref=base['url']+'|field='+fingerprint(field)[:16]
            results.append(SourceCitation(repository=SourceRepository.AL_JAMHARAH,reference_id=ref,
                arabic_text=texts['ar'],accredited_translation=texts[main],source_language=main,
                source_url=urls[main],translations=texts,translation_urls=urls,content_kind='dictionary',
                passages={l:[SourcePassage(kind='commentary',text=t,source_url=urls[l],reference_id=ref)] for l,t in texts.items()},
                published_attribution=attribution,retrieval_endpoint=base['url'],
                retrieved_at=datetime.now(timezone.utc).isoformat()))
            if len(texts)>1:
                # A shorter publisher translation cannot erase qualifications
                # in the full native definition. Assess this original separately;
                # it never masquerades as a published target-language text.
                native=results[-1].model_copy(deep=True)
                native.reference_id=ref+'|language=ar'
                native.source_language='ar';native.source_url=base['url']
                native.accredited_translation=original;native.translations={'ar':original}
                native.translation_urls={'ar':base['url']};native.published_attribution={'ar':attribution['ar']}
                native.passages={'ar':[SourcePassage(kind='commentary',text=original,source_url=base['url'],reference_id=native.reference_id)]}
                results.append(native)
        return results
