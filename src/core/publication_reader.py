"""Read exact publisher HTML sections/PDF pages, never catalog descriptions.

Source lock: docs/guides/10_immutability_constitution_and_source_lock.md.
Only observed publisher links are followed; source text is never AI repaired.
"""
import hashlib
import base64
import json
import re
import math
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit,parse_qs
from bs4 import BeautifulSoup
import requests
from src.core.analysis_agent import SafetyStop,normalized
from datetime import datetime, timezone
from src.core.integrity import fingerprint
from src.core.schema import SourceCitation, SourcePassage, SourceRepository
from src.core.source_policy import validate_source_url,load_policy


class PublicationReader:
    def __init__(self, sources):
        self.sources = sources
        self.policy = sources.policy['publication_retrieval']

    def download(self, url, limit):
        for _ in range(4):
            validate_source_url(url)
            with requests.get(url, timeout=self.sources.timeout(), allow_redirects=False, stream=True) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                response.raise_for_status()
                parts, size = [], 0
                for chunk in response.iter_content(65536):
                    self.sources.timeout()
                    size += len(chunk)
                    if size > limit:
                        raise SafetyStop('publication_size_limit')
                    parts.append(chunk)
                return b''.join(parts), url
        raise SafetyStop('citation_redirect_loop')

    def search_articles(self, keyword):
        response = requests.get(self.policy['article_search_url'], params={'query': keyword, 'type': 'all'},
            timeout=self.sources.timeout(), allow_redirects=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        # Search result titles only: exclude navigation, sidebar and breadcrumbs.
        urls = []
        titles = []
        for anchor in soup.select(self.policy['article_result_selector']):
            url = urljoin(response.url, anchor.get('href', ''))
            if urlsplit(url).hostname == 'islamic-content.com' and re.fullmatch(self.policy['article_route_pattern'], urlsplit(url).path):
                if url not in urls:
                    urls.append(url)
                    titles.append(anchor.get_text(' ', strip=True))
        # The publisher can return fuzzy word matches before exact topic matches.
        # Rank every observed result before applying our download budget.
        rows = [(1, i, title, title, title, title) for i, title in enumerate(titles)]
        ranked = self.sources.rank_local_index(rows, {'ar': [keyword], 'en': []})
        order = [int(ref.split(':')[1]) for _, ref in ranked]
        order.extend(i for i in range(len(urls)) if i not in order)
        return [('article', urls[i]) for i in order[:self.policy['articles_per_keyword']]]

    def article(self, url, follow_topic=True):
        if urlsplit(url).hostname != 'islamic-content.com' or not re.fullmatch(self.policy['article_route_pattern'], urlsplit(url).path):
            raise SafetyStop('unsupported_publication_route')
        document_key=fingerprint({'article':url,'format':1})
        saved=None if getattr(self.sources,'force_refresh',False) else self.sources.store.get('publication_html',document_key)
        if saved:
            body=base64.b64decode(saved['body']);url=saved['url']
        else:
            body, url = self.download(url, self.sources.policy['max_source_bytes'])
            self.sources.store.put('publication_html',document_key,
                {'body':base64.b64encode(body).decode('ascii'),'url':url},self.sources.policy['source_cache_ttl_seconds'])
        soup = BeautifulSoup(body, 'html.parser')
        # Topic-only pages and hadith collections are NOT accepted as articles.
        # Hadiths go through the authenticated grade-aware HadeethEnc adapter.
        if soup.select_one('main .hadeeth'):
            return []
        sections = soup.select(self.policy['article_body_selector'])
        if not sections and follow_topic:
            # A subject page is navigation, not evidence. Follow only its
            # observed publisher result cards, with a single bounded hop.
            results=[]
            links=[]
            for anchor in soup.select('.entry-main-content .post-title a[href], main .post-title a[href]'):
                linked=urljoin(url,anchor['href'])
                if (urlsplit(linked).hostname=='islamic-content.com' and
                        re.fullmatch(self.policy['article_route_pattern'],urlsplit(linked).path) and linked!=url and linked not in links):
                    links.append(linked)
            for linked in links[:self.policy['articles_per_keyword']]:
                try:
                    results.extend(self.article(linked,follow_topic=False))
                except (requests.RequestException,ValueError,SafetyStop):
                    self.sources.unavailable.append('topic_article_unavailable')
            return results
        parts = []
        for position, section in enumerate(sections):
            for unwanted in section.select('script,style,form,button'):
                unwanted.decompose()
            text = section.get_text(' ', strip=True)
            if len(text) < self.policy['minimum_passage_characters']:
                continue
            # Never crop a section to fit a prompt: oversized sections are skipped.
            if len(text) > self.policy['maximum_passage_characters']:
                continue
            parts.append((position + 1, text))
        return self.rank_and_cite(parts, url, 'ar', SourceRepository.AL_JAMHARAH,
            'article', {'document_sha256': hashlib.sha256(body).hexdigest(), 'title': soup.title.get_text() if soup.title else ''})

    def search_quranpedia(self,keyword):
        """Navigate published verse labels; fetch scripture via QuranEnc afterward."""
        config=self.sources.policy['quranpedia_retrieval']
        response=requests.get(config['search_url'],params={'query':keyword,'locale':'ar'},
            timeout=self.sources.timeout(),allow_redirects=False)
        response.raise_for_status()
        soup=BeautifulSoup(response.json()['html'],'html.parser');refs=[]
        for anchor in soup.select('a.result-item[href]'):
            url=urljoin(config['search_url'],anchor['href'])
            route=re.fullmatch(config['surah_route_pattern'],urlsplit(url).path)
            # ayah_id is a global database ID, not a verse number.
            label=anchor.select_one('div span')
            number=re.search(r':\s*([0-9٠-٩]+)\s*$',label.get_text(' ',strip=True)) if label else None
            if urlsplit(url).hostname!='quranpedia.net' or not route or not number:continue
            verse=int(number.group(1));surah=int(route.group(1))
            if 1<=surah<=114 and 1<=verse<=286 and ('quran',f'{surah}:{verse}') not in refs:
                refs.append(('quran',f'{surah}:{verse}'))
        return refs[:config['verses_per_keyword']]

    def search_dawa(self,keyword):
        config=self.sources.policy['dawa_retrieval']
        response=requests.get(config['search_url'],params={'query':keyword},
            timeout=self.sources.timeout(),allow_redirects=False)
        response.raise_for_status()
        soup=BeautifulSoup(response.content,'html.parser');refs=[]
        for anchor in soup.select('a[href]'):
            url=urljoin(config['search_url'],anchor['href'])
            if urlsplit(url).hostname=='dawa.center' and re.fullmatch(config['file_route_pattern'],urlsplit(url).path):
                if ('dawa',url) not in refs:refs.append(('dawa',url))
        return refs[:config['files_per_keyword']]

    def dawa(self,url):
        config=self.sources.policy['dawa_retrieval']
        if urlsplit(url).hostname!='dawa.center' or not re.fullmatch(config['file_route_pattern'],urlsplit(url).path):
            raise SafetyStop('unsupported_dawa_route')
        body,canonical=self.download(url,self.sources.policy['max_source_bytes'])
        soup=BeautifulSoup(body,'html.parser');pdfs=[]
        for anchor in soup.select('a[href]'):
            linked=urljoin(canonical,anchor['href'])
            if urlsplit(linked).path.lower().endswith('.pdf'):
                validate_source_url(linked)
                if linked not in pdfs:pdfs.append(linked)
        # Only exact readable PDF pages are evidence; descriptions never qualify.
        found=[]
        for pdf in pdfs[:self.policy['documents_per_item']]:
            found.extend(self.pdf(pdf,'ar',SourceRepository.DAWA_CENTER,
                soup.title.get_text(' ',strip=True) if soup.title else ''))
        return found

    def search_shamela(self,keyword):
        """Use the publisher's observed search API; snippets remain navigation."""
        config=self.sources.policy['shamela_retrieval']
        response=requests.post(config['search_url'],data={'term':
            ' '.join('+'+word for word in keyword.split()),'page':1},
            timeout=self.sources.timeout(),allow_redirects=False)
        response.raise_for_status()
        soup=BeautifulSoup(response.content,'html.parser')
        refs=[]
        for anchor in soup.select('a[href]'):
            url=urljoin(config['search_url'],anchor['href'])
            if urlsplit(url).hostname=='shamela.ws' and re.fullmatch(config['route_pattern'],urlsplit(url).path):
                if ('shamela',url) not in refs:refs.append(('shamela',url))
        return refs[:config['pages_per_keyword']]

    def shamela(self,url):
        config=self.sources.policy['shamela_retrieval']
        if urlsplit(url).hostname!='shamela.ws' or not re.fullmatch(config['route_pattern'],urlsplit(url).path):
            raise SafetyStop('unsupported_shamela_route')
        body,canonical=self.download(url,self.sources.policy['max_source_bytes'])
        if urlsplit(canonical).path!=urlsplit(url).path:
            raise SafetyStop('wrong_shamela_page')
        soup=BeautifulSoup(body,'html.parser')
        section=soup.select_one(config['body_selector'])
        if section is None:raise SafetyStop('shamela_original_missing')
        # UI copy controls are not publisher prose. Preserve body AND footnotes.
        for control in section.select('script,style,button,.btn_tag'):control.decompose()
        text=section.get_text(' ',strip=True)
        if not self.readable(text,'ar'):raise SafetyStop('shamela_original_unreadable')
        page=int(section.get('data-page-num','0'))
        page_id=urlsplit(url).path.split('/')[-1]
        if section.get('data-page-id')!=page_id or page<=0:
            raise SafetyStop('wrong_shamela_page')
        return [self.citation(text,canonical,'ar',SourceRepository.SHAMELA,'book_excerpt',page,
            {'title':soup.title.get_text(' ',strip=True) if soup.title else '',
             'document_sha256':hashlib.sha256(body).hexdigest(),'publisher_page_id':page_id})]

    def library(self, opaque_id):
        fetched = self.sources.call('fetch', {'id': opaque_id})
        data = fetched.get('structuredContent')
        if not data:
            data = json.loads(next(c['text'] for c in fetched.get('content', []) if c.get('type') == 'text'))
        if data.get('id') != opaque_id:
            raise SafetyStop('wrong_library_document')
        url = validate_source_url(data['url'])
        language = data.get('metadata', {}).get('language')
        if language not in self.sources.policy['messages']:
            return []
        # Fetch the actual publisher page even if MCP omitted attachments.
        body, canonical = self.download(url, self.sources.policy['max_source_bytes'])
        if urlsplit(canonical).path!=urlsplit(url).path:
            raise SafetyStop('wrong_library_document')
        soup = BeautifulSoup(body, 'html.parser')
        original=self.library_html(soup,canonical,language,data.get('title',''),body)
        if original:return original
        pdfs = []
        for anchor in soup.select('a[href]'):
            candidate = urljoin(canonical, anchor['href'])
            if urlsplit(candidate).path.lower().endswith('.pdf'):
                try:
                    validate_source_url(candidate)
                except ValueError:
                    continue
                if candidate not in pdfs:
                    pdfs.append(candidate)
        results = []
        for pdf in pdfs[:self.policy['documents_per_item']]:
            results.extend(self.pdf(pdf, language, SourceRepository.ISLAMIC_LIBRARY, data.get('title', '')))
        if not results:
            self.sources.unavailable.append('library_original_text_unavailable')
        return results

    def library_html(self,soup,url,language,title,body):
        """Read a complete numbered publisher article, not its summary."""
        config=self.sources.policy['library_html']
        route=re.fullmatch(config['route_pattern'],urlsplit(url).path)
        if (urlsplit(url).hostname!='islamcontent.com' or not route
                or route.group('language')!=language or language!=config['language']):return []
        # The publisher's generic HTML root says en-US even on Arabic articles.
        # Verify the actual displayed-content language link and original script.
        locale=soup.select_one(config['language_selector'])
        if locale is None or locale.get_text(' ',strip=True)!=config['language_label']:return []
        linked=urlsplit(urljoin(url,locale['href']));query=parse_qs(linked.query)
        if (linked.hostname!=urlsplit(url).hostname or linked.path!=urlsplit(url).path
                or query.get('lang')!=[language] or query.get('id')!=[route.group('id')]):return []
        for container in soup.select(config['container_selector']):
            label=container.select_one(config['label_selector'])
            if label is None or label.get_text(' ',strip=True)!=config['original_label']:continue
            paragraphs=container.select(config['paragraph_selector'])
            ids=[re.fullmatch(config['paragraph_id_pattern'],p['id']) for p in paragraphs]
            if len(ids)<config['minimum_paragraphs'] or any(i is None for i in ids):continue
            if [int(i.group(1)) for i in ids]!=list(range(1,len(ids)+1)):continue
            if paragraphs[0].get_text(' ',strip=True)!=title:continue
            original='\n\n'.join(p.get_text(' ',strip=True) for p in paragraphs)
            if not self.readable(original,language):continue
            return [self.citation(original,url,language,SourceRepository.ISLAMIC_LIBRARY,'article',1,
                {'title':title,'document_sha256':hashlib.sha256(body).hexdigest(),
                 'format':'complete_numbered_publisher_article',
                 'paragraph_ids':[p['id'] for p in paragraphs]})]
        return []

    def pdf(self, url, language, repository, title):
        # Process isolation changes execution safety, not exact extraction bytes.
        # Existing signed complete pages still receive current readability gates.
        cache_key = fingerprint({'pdf': url, 'extractor_version': 1})
        document = None if getattr(self.sources,'force_refresh',False) else self.sources.store.get('publication_document', cache_key)
        if document is None:
            body, url = self.download(url, self.policy['maximum_document_bytes'])
            if not body.startswith(b'%PDF-'):
                raise SafetyStop('not_a_published_pdf')
            # MuPDF does not support concurrent threads. Each extraction runs
            # in its own process; a native failure cannot kill the web worker.
            remaining=self.sources.deadline-time.monotonic()
            try:
                extraction=subprocess.run([sys.executable,str(Path(__file__).with_name('pdf_extractor.py')),
                    str(self.policy['maximum_document_pages'])],input=body,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    timeout=None if math.isinf(remaining) else max(.001,remaining))
            except subprocess.TimeoutExpired as exc:
                raise SafetyStop('deadline_exceeded') from exc
            if extraction.returncode==2:raise SafetyStop('publication_page_limit')
            if extraction.returncode:raise SafetyStop('publication_extraction_failed')
            try:
                pages=json.loads(extraction.stdout)
            except (ValueError,UnicodeError) as exc:
                raise SafetyStop('publication_extraction_failed') from exc
            if [p[0] for p in pages]!=list(range(1,len(pages)+1)):
                raise SafetyStop('publication_extraction_failed')
            document = {'url': url, 'pages': pages, 'sha256': hashlib.sha256(body).hexdigest()}
            self.sources.store.put('publication_document', cache_key, document, self.sources.policy['source_cache_ttl_seconds'])
        pages = [(number, text) for number, text in document['pages'] if self.readable(text, language)]
        if not pages:
            self.sources.unavailable.append('publication_text_extraction_unreadable')
            return []
        return self.rank_and_cite(pages, document['url'], language, repository, 'book_excerpt',
            {'document_sha256': document['sha256'], 'title': title})

    def readable(self, text, language):
        if not self.policy['minimum_passage_characters'] <= len(text) <= self.policy['maximum_passage_characters']:
            return False
        if any('\ue000' <= c <= '\uf8ff' or c == '\ufffd' for c in text):
            return False
        # Legacy font maps can output Arabic-looking but incorrect words.
        # Reject the page instead of guessing/reordering/replacing letters.
        if language == 'ar' and any(re.search(pattern,text) for pattern in self.policy['unreadable_arabic_patterns']):
            return False
        # Broken PDF maps sometimes replace letters with detached vowel marks.
        # Arabic-looking code points alone do not establish readable originals.
        if language=='ar' and len(re.findall(r'(?:^|\s)[\u064b-\u065f]+(?=\s|$)',text))>=3:
            return False
        letters = [c for c in text if c.isalpha()]
        if language == 'ar' and (not letters or sum('\u0600' <= c <= '\u06ff' for c in letters) / len(letters) < .6):
            return False
        return True

    def rank_and_cite(self, sections, url, language, repository, kind, attribution):
        groups = [p.keywords for p in self.sources.search_analysis.question_parts] or [self.sources.search_analysis.keywords]
        chosen = [n for n,_ in sections] if getattr(self.sources,'ingest_all',False) else []
        for keywords in groups:
            rows = [(1, number, text if language != 'ar' else '', text if language == 'ar' else '', '', '') for number, text in sections]
            ranked = self.sources.rank_local_index(rows, keywords)
            for _, ref in ranked[:self.policy['passages_per_part']]:
                number = int(ref.split(':')[1])
                # Include the next complete section/page to preserve nearby exceptions.
                for position in (number, number + 1):
                    if position not in chosen and any(n == position for n, _ in sections):
                        chosen.append(position)
        texts = dict(sections)
        return [self.citation(texts[number], url, language, repository, kind, number, attribution) for number in chosen]

    @staticmethod
    def citation(text, url, language, repository, kind, number, attribution):
        attribution={**attribution,'contains_scripture':bool(re.search(
            load_policy()['dictionary_retrieval']['scripture_intro_pattern'],normalized(text).replace('ـ','')))}
        ref = url + ('|page=' if kind == 'book_excerpt' else '|section=') + str(number)
        return SourceCitation(repository=repository, reference_id=ref, arabic_text=text if language == 'ar' else '',
            accredited_translation=text, source_language=language, translations={language: text},
            translation_urls={language: url}, source_url=url, content_kind=kind, is_offline_cached=False,
            passages={language: [SourcePassage(kind='commentary', text=text, reference_id=ref, source_url=url)]},
            published_attribution={language: json.dumps({**attribution, 'locator': number}, ensure_ascii=False)},
            retrieval_endpoint=url, retrieved_at=datetime.now(timezone.utc).isoformat())
