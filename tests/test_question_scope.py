"""Interpretation repair cannot invent mandatory comparisons or erase requests."""
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent,SafetyStop
from src.core.schema import PipelineInput,QuestionPart
from tests.test_trust_pipeline import analysis


def plan(kind):
    result=analysis('LEVEL_B')
    result.question_language='en'
    result.question_parts=[QuestionPart(question='Identify the origin of the requested text.',
        keywords=result.keywords,requirements=[{'kind':kind,'description':'Address the requested scope.'}])]
    return result


def test_unrequested_comparison_is_reinterpreted_before_retrieval():
    agent=AnalysisAgent(time.monotonic()+60)
    agent.request=Mock(side_effect=[plan('comparison'),plan('explanation')])
    result=agent.analyze(PipelineInput(query='Who authored this text?'))
    assert result.question_parts[0].requirements[0].kind=='explanation'
    assert agent.request.call_count==2
    assert agent.request.call_args.args[1]['rejection']=='unrequested_comparison'


def test_repeated_unrequested_comparison_is_rejected():
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=plan('comparison'))
    with pytest.raises(SafetyStop,match='unrequested_comparison'):
        agent.analyze(PipelineInput(query='Who authored this text?'))


@pytest.mark.parametrize('language,question',[
    ('en','How do these texts differ?'),('en','Do all scholars agree?'),
    ('en','Is this quotation correct?'),('ar','ما الفرق بين النصين؟'),
    ('fr','Est-ce comme cet autre concept ?'),('es','¿Cuál es la diferencia?'),
    ('de','Was ist der Unterschied?'),('id','Apa bedanya?'),
    ('tr','Bu fark nedir?'),('sw','Kuna tofauti gani?'),
    ('zh','有什么区别？'),('ur','کیا فرق ہے؟')])
def test_actual_comparison_disagreement_or_correction_is_preserved(language,question):
    agent=AnalysisAgent(time.monotonic()+60);item=plan('comparison');item.question_language=language
    agent.validate_question_scope(PipelineInput(query=question),item)
    assert item.question_parts[0].requirements[0].kind=='comparison'


@pytest.mark.parametrize('language,question',[
    ('en','Give me a sahih hadith about a drink.'),
    ('en','Recite the complete chapter.'),
    ('ar','اذكر حديثا صحيحا عن هذا الأمر'),
    ('fr','Donne-moi un hadith authentique.'),
    ('es','Dame un hadiz autentico.')])
def test_explicit_source_request_cannot_be_replaced_by_an_explanation(language,question):
    agent=AnalysisAgent(time.monotonic()+60)
    incomplete=plan('explanation');incomplete.question_language=language
    complete=plan('exact_quote');complete.question_language=language
    agent.request=Mock(side_effect=[incomplete,complete])
    result=agent.analyze(PipelineInput(query=question))
    assert result.question_parts[0].requirements[0].kind=='exact_quote'
    assert agent.request.call_args.args[1]['rejection']=='requested_quotation_missing'


@pytest.mark.parametrize('question',[
    'What does this hadith mean?', 'How does the Quran explain kindness?',
    'Give me an explanation of the concept.'])
def test_general_explanation_does_not_require_a_quotation(question):
    agent=AnalysisAgent(time.monotonic()+60)
    agent.validate_question_scope(PipelineInput(query=question),plan('explanation'))
