"""Real publisher fixtures: exact fields, translations and fail-closed identity."""
import json
import time
from pathlib import Path
from unittest.mock import Mock
import pytest
from bs4 import BeautifulSoup
from src.core.dictionary_reader import DictionaryReader
from src.core.official_sources import OfficialSources
from src.core.integrity import AuthenticatedStore
from src.core.analysis_agent import SafetyStop
from src.core.schema import PipelineInput
from tests.test_trust_pipeline import analysis

FIXTURES=Path(__file__).parent/'fixtures/dictionary'
URL='https://islamic-content.com/legacy-dictionary/word/3529'


@pytest.fixture
def reader(tmp_path):
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path))
    service=DictionaryReader(source)
    service.reader.download=Mock(side_effect=lambda url,limit:
        ((FIXTURES/('tawhid-en.html' if url.endswith('/en') else 'tawhid-ar.html')).read_bytes(),url))
    return service


def test_search_ranks_actual_exact_term_before_compounds(reader,monkeypatch):
    response=Mock()
    response.json.return_value=json.loads((FIXTURES/'tawhid-search.json').read_text())
    get=Mock(return_value=response);monkeypatch.setattr('src.core.dictionary_reader.requests.get',get)
    refs=reader.search('التوحيد')
    assert refs[0]==('dictionary',URL)
    assert len(refs)==reader.config['entries_per_term']
    assert get.call_args.kwargs['headers']['X-Requested-With']=='XMLHttpRequest'
    assert reader.search('التوحيد')==refs and get.call_count==1


def test_search_preserves_hamza_and_tries_full_word_before_article_fallback(reader,monkeypatch):
    response=Mock();response.json.return_value={'table_data':'<a href="/dictionary/word/123">الملائكة</a>'}
    get=Mock(return_value=response);monkeypatch.setattr('src.core.dictionary_reader.requests.get',get)
    assert reader.search('الملائكة')==[('dictionary','https://islamic-content.com/dictionary/word/123')]
    assert get.call_args.kwargs['params']['query']=='الملائكة'
    assert get.call_count==1


def test_article_fallback_matches_bare_same_word_before_compounds(reader,monkeypatch):
    first=Mock();first.json.return_value={'table_data':'<a href="/dictionary/word/1">صدقة الخلطاء</a>'}
    second=Mock();second.json.return_value={'table_data':'<a href="/dictionary/word/2">صدقة</a>'}
    get=Mock(side_effect=[first,second]);monkeypatch.setattr('src.core.dictionary_reader.requests.get',get)
    assert reader.search('الصدقة')[0]==('dictionary','https://islamic-content.com/dictionary/word/2')
    assert [c.kwargs['params']['query'] for c in get.call_args_list]==['الصدقة','صدقة']


def test_dictionary_navigation_keeps_global_concepts_and_articleless_arabic(reader):
    from src.core.schema import QuestionPart
    a=analysis();a.keywords={'ar':['استقبال القبلة'],'en':[]}
    a.question_parts=[QuestionPart(question='Explain a term',keywords={'ar':['توحيد'],'en':[]})]
    terms=reader.plan_terms(a)
    assert 'القبلة' in terms and 'التوحيد' in terms


def test_published_technical_meaning_aligns_exact_original_fields(reader):
    results=reader.read(URL,['en','ar'])
    item=next(c for c in results if json.loads(c.published_attribution['ar'])['field']=='المعنى الاصطلاحي')
    en=BeautifulSoup((FIXTURES/'tawhid-en.html').read_bytes(),'html.parser')
    original=en.select_one('.encyclopedia > p').get_text(' ',strip=True)
    assert item.translations['en']==original
    assert item.source_url==URL+'/en' and item.translation_urls['ar']==URL
    assert item.source_language=='en' and item.content_kind=='dictionary'
    assert json.loads(item.published_attribution['en'])['published_translation'] is True
    assert json.loads(item.published_attribution['ar'])['document_sha256']
    # No source field with the rejected positive monotheism gloss is rewritten.
    assert all('"Tawheed" (monotheism)' not in c.translations.get('en','') for c in results)


def test_missing_translation_is_not_invented_or_guessed(reader):
    results=reader.read(URL,['zh','ar','en'])
    assert results and all('zh' not in c.translations for c in results)
    assert all(not call.args[0].endswith('/zh') for call in reader.reader.download.call_args_list)


def test_wrong_entry_redirect_is_rejected(reader):
    reader.reader.download=Mock(return_value=((FIXTURES/'tawhid-ar.html').read_bytes(),URL.replace('3529','3530')))
    with pytest.raises(SafetyStop,match='wrong_dictionary_entry_returned'):reader.read(URL,['ar'])


def test_wrong_language_body_at_expected_route_is_rejected(reader):
    reader.reader.download=Mock(return_value=((FIXTURES/'tawhid-en.html').read_bytes(),URL+'/fr'))
    with pytest.raises(SafetyStop,match='wrong_dictionary_language'):reader.document(URL+'/fr')


def test_dictionary_cannot_smuggle_marked_scripture_as_definition(reader):
    doc=reader.document(URL)
    assert 'publisher_definition' in doc['scripture_fields']
    results=reader.read(URL,['en','ar'])
    item=next(c for c in results if json.loads(c.published_attribution['ar'])['field']=='publisher_definition')
    assert set(item.translations)=={'ar'}
    assert json.loads(item.published_attribution['ar'])['contains_scripture'] is True
    assert item.arabic_text==doc['sections']['publisher_definition']
    from src.agents.cultural_localizer import CulturalLocalizerAgent
    agent=CulturalLocalizerAgent(time.monotonic()+10)
    agent.client.request=Mock(side_effect=AssertionError('No scripture translation'))
    assert agent.translate_arabic_only_passages('en',[item])=={}
    agent.client.request.assert_not_called()


def test_live_adapter_signs_every_actual_language_link_and_indexes_originals(reader,monkeypatch):
    read=reader.read
    monkeypatch.setattr('src.core.dictionary_reader.DictionaryReader.read',lambda self,url,languages:read(url,languages))
    source=reader.sources
    source.check_link=Mock(return_value='checked')
    result=source.get('dictionary',URL,['en','ar'])
    assert result and all(c.provenance_verified and c.source_digest==source.digest(c) for c in result)
    assert {call.args[0] for call in source.check_link.call_args_list}=={URL,URL+'/en'}
    from src.core.orchestrator import MasterOrchestrator
    MasterOrchestrator().validate_candidates(result,['en','ar'])


def test_dictionary_identity_metadata_reaches_selection_without_invented_prose(reader):
    from src.core.analysis_agent import AnalysisAgent
    citations=reader.read(URL,['en','ar'])
    payload=AnalysisAgent(time.monotonic()+10).selection_payload(PipelineInput(query='Translate a protected term'),analysis(),citations)
    assert payload['candidates'][0]['dictionary_entry']['ar']['document_sha256']
    assert all('witness_spans' in candidate for candidate in payload['candidates'])
