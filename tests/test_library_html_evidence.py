"""Observed full publisher articles remain distinct from catalog metadata."""
import json
import time
from pathlib import Path
from unittest.mock import Mock
from bs4 import BeautifulSoup
import pytest
from src.core.official_sources import OfficialSources
from src.core.publication_reader import PublicationReader
from src.core.integrity import AuthenticatedStore
from src.core.analysis_agent import SafetyStop
from src.agents.cultural_localizer import CulturalLocalizerAgent
from tests.test_trust_pipeline import analysis

URL='https://islamcontent.com/ar/content/1442'
TITLE='التدرج في دعوة المسلم الجديد'
FIXTURE=Path(__file__).parent/'fixtures/library-html/intro-ar.html'


@pytest.fixture
def reader(tmp_path):
    sources=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path))
    sources.search_analysis=analysis()
    service=PublicationReader(sources)
    sources.call=Mock(return_value={'structuredContent':{'id':'library:1442:ar','url':URL,
        'title':TITLE,'metadata':{'language':'ar'}}})
    service.download=Mock(return_value=(FIXTURE.read_bytes(),URL))
    return service


def test_complete_original_body_and_qualifications_are_preserved(reader):
    found=reader.library('library:1442:ar')
    assert len(found)==1 and found[0].content_kind=='article'
    soup=BeautifulSoup(FIXTURE.read_bytes(),'html.parser')
    assert found[0].arabic_text=='\n\n'.join(p.get_text(' ',strip=True) for p in soup.select('p[id]'))
    metadata=json.loads(found[0].published_attribution['ar'])
    assert metadata['paragraph_ids']==['p'+str(i) for i in range(1,12)]
    assert metadata['contains_scripture'] is True
    assert metadata['document_sha256'] and set(found[0].translations)=={'ar'}
    agent=CulturalLocalizerAgent(time.monotonic()+60)
    agent.client.request=Mock(side_effect=AssertionError('No AI scripture translation'))
    assert agent.translate_arabic_only_passages('en',found)=={}
    agent.client.request.assert_not_called()


@pytest.mark.parametrize('problem',['missing_paragraph','wrong_title','summary_label','wrong_language'])
def test_incomplete_or_misidentified_body_cannot_become_evidence(reader,problem):
    soup=BeautifulSoup(FIXTURE.read_bytes(),'html.parser')
    if problem=='missing_paragraph':soup.select_one('#p4').decompose()
    if problem=='wrong_title':soup.select_one('#p1').string='Different item'
    if problem=='summary_label':soup.select_one('button span.hover_black_bold').string='نبذة مختصرة'
    if problem=='wrong_language':soup.select_one(reader.sources.policy['library_html']['language_selector']).string='Display in French'
    reader.download=Mock(return_value=(str(soup).encode(),URL))
    assert reader.library('library:1442:ar')==[]


def test_library_redirect_cannot_supply_another_items_article(reader):
    reader.download=Mock(return_value=(FIXTURE.read_bytes(),URL.replace('1442','1443')))
    with pytest.raises(SafetyStop,match='wrong_library_document'):
        reader.library('library:1442:ar')
