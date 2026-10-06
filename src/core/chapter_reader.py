"""Complete publisher Quran chapters; no AI scripture or guessed verse lists.

Source law: docs/guides/10_immutability_constitution_and_source_lock.md.
The legacy names are navigation only. Actual content comes from QuranEnc, with
each publisher verse preserved and the complete sequence independently checked.
"""
import json
import re
import sqlite3
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
import requests
from src.core.analysis_agent import normalized,requires_primary_text,SafetyStop
from src.core.integrity import fingerprint
from src.core.schema import SourcePassage,SourceRepository
from src.core.source_policy import ROOT


class ChapterReader:
    def __init__(self,sources):
        self.sources=sources
        self.config=sources.policy['chapter_retrieval']

    @staticmethod
    def name_key(value):
        value=normalized(value)
        value=re.sub(r'^al[\s-]*','',value)
        value=re.sub(r'([aeiou])\1+',r'\1',value)
        value=re.sub(r'[^a-zء-ي]','',value)
        return value[:-1] if value.endswith('h') else value

    def identify(self,analysis):
        if not requires_primary_text(analysis):return []
        requested=' '.join(p.question+' '+' '.join(r.description for r in p.requirements) for p in analysis.question_parts)
        if not re.search(self.config['request_pattern'],analysis.intent+' '+requested,re.I):return []
        if not re.search(self.config['chapter_scope_pattern'],requested,re.I):return []
        path=ROOT/'data/cache/sharia_sources_cache.db'
        if not path.exists():return []
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT DISTINCT surah_number,surah_name_ar,surah_name_en FROM quran_cache').fetchall()
        chunks=re.findall(r'[\w-]+',requested)
        chunks += [c for values in analysis.keywords.values() for c in values]
        forms=[self.name_key(c) for c in chunks]
        matches={}
        for number,ar,en in rows:
            for title in (ar,en):
                alias=self.name_key(title or '')
                if len(alias)<3:continue
                score=max((SequenceMatcher(None,alias,text).ratio() for text in forms if len(text)>=3),default=0)
                if score>=self.config['name_match_ratio']:
                    matches[number]=max(score,matches.get(number,0))
        if not matches:return []
        best=max(matches.values())
        # A fuzzy identification cannot silently choose among tied homonyms.
        selected=[number for number,score in matches.items() if score==best]
        if len(selected)>self.config['max_chapters']:return []
        return [('quran_surah',str(number)) for number in selected]

    def read(self,surah,languages,expected_last=None):
        surah=int(surah)
        if not 1<=surah<=114:raise SafetyStop('invalid_surah_reference')
        first=self.sources.quran(f'{surah}:1',languages)
        if first is None:return None
        primary={};footnotes={};urls={};attribution={};arabic=None;sequence=None
        for language in [l for l in languages if l!='ar'] or ['en']:
            key=first.translation_urls.get(language,next(iter(first.translation_urls.values()))).split('/')[-3]
            if not re.fullmatch(r'[a-z0-9_]+',key):raise SafetyStop('invalid_translation_key')
            api=self.config['api_template'].format(api=self.sources.policy['quran_api'],translation_key=key,surah=surah)
            response=requests.get(api,timeout=self.sources.timeout(),allow_redirects=False)
            response.raise_for_status();verses=response.json()['result']
            if not isinstance(verses,list) or not verses:raise SafetyStop('incomplete_surah_returned')
            ids=[int(v['aya']) for v in verses]
            if ids!=list(range(1,len(verses)+1)) or any(int(v['sura'])!=surah for v in verses):
                raise SafetyStop('wrong_surah_sequence')
            if sequence is not None and sequence!=ids:raise SafetyStop('scripture_versions_disagree')
            if expected_last is not None and ids[-1]!=expected_last:raise SafetyStop('wrong_surah_sequence')
            page=first.translation_urls.get(language,next(iter(first.translation_urls.values()))).rsplit('/',1)[0]
            web=requests.get(page,timeout=self.sources.timeout(),allow_redirects=False)
            web.raise_for_status()
            published=BeautifulSoup(web.content,'html.parser')
            nodes=published.select(self.config['verse_selector'])
            page_ids=[int(node['data-aya']) for node in nodes]
            if page_ids!=ids:raise SafetyStop('incomplete_surah_returned')
            original=[v['arabic_text'] for v in verses]
            for node,verse in zip(nodes,verses):
                ar=node.select_one(self.config['arabic_selector'])
                translated=node.select_one(self.config['translation_selector'])
                if (ar is None or translated is None or ar.get_text().strip()!=verse['arabic_text']
                        or translated.get_text().strip()!=verse['translation']):
                    raise SafetyStop('surah_page_content_mismatch')
            if original[0]!=first.arabic_text:raise SafetyStop('scripture_versions_disagree')
            if any(not v['arabic_text'].strip() or not v['translation'].strip() for v in verses):
                raise SafetyStop('empty_published_passage')
            if arabic is not None and arabic!=original:raise SafetyStop('scripture_versions_disagree')
            arabic=original;sequence=ids
            primary[language]='\n'.join(v['translation'] for v in verses)
            footnotes[language]='\n\n'.join(v['footnotes'] for v in verses if v.get('footnotes'))
            urls[language]=page
            attribution[language]=json.dumps({'scope':'complete_surah','surah':surah,'verse_ids':ids,
                'translation_key':key,'api_url':api,'api_content_sha256':fingerprint(verses)},ensure_ascii=False)
        primary['ar']='\n'.join(arabic);footnotes['ar']='';urls['ar']=next(iter(urls.values()))
        attribution['ar']=json.dumps({'scope':'complete_surah','surah':surah,'verse_ids':sequence},ensure_ascii=False)
        ref=f'{surah}:1-{sequence[-1]}'
        texts={l:primary[l]+('\n\n'+footnotes[l] if footnotes[l] else '') for l in languages}
        c=self.sources.citation(SourceRepository.QURAN_ENC_PUBLISHED,ref,texts,urls,languages,'quran')
        c.passages={l:[SourcePassage(kind='quran',text=primary[l],reference_id=ref,source_url=urls[l])]+(
            [SourcePassage(kind='commentary',text=footnotes[l],reference_id=ref,source_url=urls[l])] if footnotes[l] else []) for l in languages}
        c.published_attribution={l:attribution[l] for l in languages}
        c.retrieval_endpoint=self.sources.policy['quran_api']
        return c
