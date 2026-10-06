"""A complete recitation uses exact API verses and matching publisher page text."""
import copy
import json
import time
from pathlib import Path
from unittest.mock import Mock
import pytest
from src.core.chapter_reader import ChapterReader
from src.core.official_sources import OfficialSources
from src.core.integrity import AuthenticatedStore
from src.core.analysis_agent import SafetyStop
from tests.test_trust_pipeline import citation,analysis

FIXTURES=Path(__file__).parent/'fixtures/quran-chapter'


@pytest.fixture
def chapter(tmp_path,monkeypatch):
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path))
    data=json.loads((FIXTURES/'chapter-1-en.json').read_text())
    first=citation();first.arabic_text=data['result'][0]['arabic_text']
    first.translation_urls={'en':'https://quranenc.com/en/browse/english_saheeh/1/1',
        'ar':'https://quranenc.com/en/browse/english_saheeh/1/1'}
    source.quran=Mock(return_value=first)
    def get(url,**kwargs):
        response=Mock()
        if '/api/' in url:response.json.return_value=copy.deepcopy(data)
        else:response.content=(FIXTURES/'chapter-1-en.html').read_bytes()
        return response
    monkeypatch.setattr('src.core.chapter_reader.requests.get',get)
    return ChapterReader(source),data


def test_every_published_verse_is_preserved_in_complete_quote(chapter):
    reader,data=chapter
    c=reader.read(1,['en','ar'])
    assert c.reference_id=='1:1-7'
    assert c.passages['en'][0].text=='\n'.join(v['translation'] for v in data['result'])
    assert c.passages['ar'][0].text=='\n'.join(v['arabic_text'] for v in data['result'])
    assert c.passages['en'][1].kind=='commentary'
    assert c.passages['en'][0].text not in c.passages['en'][1].text
    assert json.loads(c.published_attribution['en'])['verse_ids']==list(range(1,8))


@pytest.mark.parametrize('problem',['missing','duplicate','wrong_chapter','changed_arabic','changed_translation'])
def test_incomplete_or_changed_scripture_never_publishes(chapter,monkeypatch,problem):
    reader,data=chapter;changed=copy.deepcopy(data)
    if problem=='missing':changed['result'].pop()
    if problem=='duplicate':changed['result'][1]['aya']='1'
    if problem=='wrong_chapter':changed['result'][1]['sura']='2'
    if problem=='changed_arabic':changed['result'][1]['arabic_text']='Changed text'
    if problem=='changed_translation':changed['result'][1]['translation']='Changed text'
    def get(url,**kwargs):
        r=Mock();r.json.return_value=changed;r.content=(FIXTURES/'chapter-1-en.html').read_bytes();return r
    monkeypatch.setattr('src.core.chapter_reader.requests.get',get)
    with pytest.raises(SafetyStop):reader.read(1,['en','ar'])


def test_wrong_cached_range_cannot_resolve_to_a_different_complete_chapter(chapter):
    reader,_=chapter
    with pytest.raises(SafetyStop,match='wrong_surah_sequence'):reader.read(1,['en','ar'],expected_last=8)


def test_quote_assembly_does_not_select_only_the_first_verse(chapter):
    from tests.test_personalized_explanation import localizer,draft
    reader,data=chapter;c=reader.read(1,['en','ar'])
    result=localizer().render_draft(draft(),[c],['en','ar'])
    assert result[0].explanation_segments[1].text.endswith(data['result'][-1]['translation'])
    assert result[1].explanation_segments[1].text.endswith(data['result'][-1]['arabic_text'])


def test_general_supplication_does_not_guess_a_chapter(chapter):
    from src.core.schema import QuestionPart
    reader,_=chapter;a=analysis('LEVEL_A')
    a.intent='recite a supplication'
    a.question_parts=[QuestionPart(question='Recite a prayer for faith',keywords=a.keywords,
        requirements=[{'kind':'exact_quote','description':'Provide the requested supplication.'}])]
    assert reader.identify(a)==[]
