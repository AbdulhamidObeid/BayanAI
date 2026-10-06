"""Regressions for informal questions, keyword discovery and bounded recovery."""
import json
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent, SafetyStop, interpreted_question, provider_schema
from src.core.schema import PipelineInput, SearchExpansion, ExplanationDraft, CulturalPersona
from src.core.official_sources import OfficialSources
from src.core.source_policy import ROOT
from tests.test_trust_pipeline import analysis, citation, harness
from tests.test_personalized_explanation import localizer


@pytest.mark.parametrize('query,expected',[
    ('من الله','من هو الله؟'),('من محمد؟','من هو محمد؟'),
    ('من أين','من أين'),('من خلق','من خلق'),
    ('من هو الله؟','من هو الله؟'),('Who is Allah?','Who is Allah?')])
def test_informal_question_preserves_ordinary_meaning(query,expected):
    assert interpreted_question(query)==expected


def test_analysis_reconsiders_false_ambiguity_without_assuming_reader_belief():
    agent=AnalysisAgent(time.monotonic()+60)
    uncertain=analysis('LEVEL_A');uncertain.needs_clarification=True
    clear=analysis('LEVEL_A');clear.confidence=.85
    agent.request=Mock(side_effect=[uncertain,clear])
    result=agent.analyze(PipelineInput(query='من الله',target_language='ar'))
    assert result.level.value=='LEVEL_A' and result.knowledge_level.value=='unknown'
    assert agent.request.call_count==2
    assert agent.request.call_args_list[0].args[1]['query']=='من هو الله؟'
    assert agent.request.call_args_list[0].args[1]['original_question']=='من الله'


def test_failed_classification_does_not_invent_a_default_level(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    pipe,agent,sources=harness()
    agent.analyze.side_effect=SafetyStop('classification_uncertain')
    result=pipe.run_pipeline(PipelineInput(query='Unclear question'))
    assert result.response_level is None and result.answer_status=='ABSTAINED'
    sources.retrieve.assert_not_called()


def test_search_recovery_keeps_level_and_requires_short_concepts():
    agent=AnalysisAgent(time.monotonic()+60)
    agent.request=Mock(return_value=SearchExpansion(keywords={'ar':['الإخلاص'],'en':['Surah Ikhlas']}))
    original=analysis('LEVEL_A')
    recovered=agent.expand_search(PipelineInput(query='من الله'),original,[])
    assert recovered.level==original.level and recovered.knowledge_level==original.knowledge_level
    agent.request.return_value=SearchExpansion(keywords={'ar':['الله'],'en':['the concept of God in Islam']})
    with pytest.raises(SafetyStop,match='invalid_keywords'):
        agent.expand_search(PipelineInput(query='من الله'),original,[])


def test_failed_relevance_gets_new_verified_candidates_before_answer(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    pipe,agent,sources=harness(a=analysis('LEVEL_A'))
    agent.select.side_effect=[SafetyStop('no_direct_answer'),[citation()]]
    agent.expand_search.return_value=analysis('LEVEL_A')
    result=pipe.run_pipeline(PipelineInput(query='Who is Allah?'))
    assert result.answer_status=='ANSWERED' and result.response_level.value=='LEVEL_A'
    assert sources.retrieve.call_count==2 and agent.select.call_count==2
    agent.expand_search.assert_called_once()


def test_local_index_uses_arabic_and_chapter_names_without_returning_index_text():
    source=OfficialSources(time.monotonic()+30)
    rows=[(2,1,'Irrelevant words','نص غير مرتبط','البقرة','Al-Baqarah'),
          (112,1,'One','الله أحد','الإخلاص','Al-Ikhlaas')]
    assert source.rank_local_index(rows,{'ar':['الله'],'en':[]})==[('quran','112:1')]
    assert source.rank_local_index(rows,{'ar':[],'en':['Surah Ikhlas']})==[('quran','112:1')]


def test_writer_payload_keeps_all_terms_and_only_required_language_rules():
    agent=localizer()
    agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],
                   CulturalPersona.GENERAL_GLOBAL)
    terms=agent.client.request.call_args_list[0].args[1]['canonical_terminology']['terms']
    canonical=json.loads((ROOT/'configs/sharia_lexicon.json').read_text())['terms']
    assert set(terms)==set(canonical)
    for name,term in terms.items():
        assert term['arabic']==canonical[name]['arabic']
        assert set(term['translations'])<= {'en','ar'}
        for language,translation in term['translations'].items():
            assert translation['canonical']==canonical[name]['translations'][language]['canonical']
            assert translation.get('forbidden_substitutes')==canonical[name]['translations'][language].get('forbidden_substitutes')


def test_provider_literal_schema_preserves_strict_quote_and_prose_types():
    schema=provider_schema(ExplanationDraft)
    assert schema['$defs']['ExplanationProse']['properties']['kind']['enum']==['explanation']
    assert schema['$defs']['FullQuoteReference']['properties']['kind']['enum']==['quote']
    assert schema['$defs']['FullQuoteReference']['properties']['text']['enum']==['']
    assert schema['$defs']['FullQuoteReference']['properties']['citation_ids']['maxItems']==1
    assert schema['additionalProperties'] is False


def test_provider_failure_tries_fallback_and_still_validates_contract(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    first=Mock();first.models.generate_content.side_effect=TimeoutError()
    from src.core.schema import QuestionPart
    expected=analysis();expected.question_parts=[QuestionPart(question='Explain oneness',keywords=expected.keywords,requirements=[{'kind':'explanation','description':'Explain the requested concept.'}])]
    second=Mock();second.models.generate_content.return_value=Mock(text=expected.model_dump_json())
    factory=Mock(side_effect=[first,second]);monkeypatch.setattr(genai,'Client',factory)
    agent=AnalysisAgent(time.monotonic()+60)
    result=agent.request('Classify only',{'question':'x'},type(analysis()))
    assert result==expected and factory.call_count==2
    first.close.assert_called_once();second.close.assert_called_once()
    config=second.models.generate_content.call_args.kwargs['config']
    assert 'Required JSON output contract' in config.system_instruction


def test_language_reviews_cannot_borrow_other_language_commentary():
    agent=localizer()
    agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    checks=agent.client.request.call_args_list[1].args[1]['required_checks']
    assert checks[1]['language']=='ar'
    assert checks[1]['cited_evidence']==[{'citation_id':0,'published_text':citation().translations['ar']}]
    assert 'another language' in agent.policy['explanation_review_instruction']


def test_single_language_hadith_preserves_narration_and_newline_commentary(tmp_path):
    from pathlib import Path
    from src.core.integrity import AuthenticatedStore
    raw=json.loads((Path(__file__).parent/'fixtures/hadith_65007_ar_single.json').read_text())
    source=OfficialSources(time.monotonic()+30,AuthenticatedStore(tmp_path/'single'))
    source.call=Mock(return_value=raw)
    result=source.hadith('65007',['ar'])
    assert result and result.scholarly_grading=='صحيح'
    assert result.translation_urls['ar']=='https://hadeethenc.com/ar/browse/hadith/65007'
    assert result.passages['ar'][0].text in source.text(raw)
    assert result.passages['ar'][1].text.startswith('يبين النبي صلى الله عليه وسلم')
    assert result.passages['ar'][1].text.split('\n\n')[0] in source.text(raw)
    assert source.hadith('65007',['en']) is None


def test_invalid_search_phrase_can_be_repaired_without_changing_classification():
    agent=AnalysisAgent(time.monotonic()+60)
    invalid=SearchExpansion(keywords={'ar':['لا إله إلا الله'],'en':['oneness']})
    repaired=SearchExpansion(keywords={'ar':['وحدانية الله'],'en':['oneness']})
    agent.request=Mock(side_effect=[invalid,repaired])
    result=agent.expand_search(PipelineInput(query='Explain'),analysis('LEVEL_B'),[])
    assert result.level.value=='LEVEL_B' and result.keywords==repaired.keywords
    assert agent.request.call_count==2


def test_copied_scripture_cannot_be_disguised_as_personalized_prose():
    agent=localizer();c=citation()
    assert agent.copies_scripture('قل هو الله أحد.',[c],'ar')
    c.translations['en']='He is the Creator of the heavens and the earth and everything in them.'
    assert agent.copies_scripture('This states: he is the creator of the heavens and the earth.',[c],'en')
    assert not agent.copies_scripture('This explains that everything has one Creator.',[c],'en')


def test_quote_detection_leaves_full_server_inserted_original_untouched():
    from tests.test_personalized_explanation import draft
    item=draft();item.versions[1].segments[0].text='قُلْ هُوَ اللَّهُ أَحَدٌ.'
    agent=localizer(d=item)
    with pytest.raises(SafetyStop,match='unmarked_published_quote'):
        agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==1


def test_approved_domain_cannot_hide_a_wrong_hadith_reference(tmp_path):
    from pathlib import Path
    from src.core.integrity import AuthenticatedStore
    raw=json.loads((Path(__file__).parent/'fixtures/hadith_65007_ar_single.json').read_text())
    source=OfficialSources(time.monotonic()+30,AuthenticatedStore(tmp_path/'wrong'))
    source.call=Mock(return_value=raw)
    with pytest.raises(SafetyStop,match='wrong_hadith_reference_returned'):
        source.hadith('65008',['ar'])
