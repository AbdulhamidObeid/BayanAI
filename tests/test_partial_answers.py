"""Useful partial publication retains claim-level trust and explicit omissions."""
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent, SafetyStop, provider_schema
from src.core.integrity import AuthenticatedStore
from src.core.orchestrator import MasterOrchestrator
from src.core.partial_answers import recover_partial
from src.core.schema import (PipelineInput, QuestionPart, PartialEvidencePlan,
    PartialExplanationReview, AnswerCoverage, CulturalPersona)
from src.core.source_policy import load_policy
from src.agents.cultural_localizer import CulturalLocalizerAgent
from tests.test_answer_requirements import selection
from tests.test_personalized_explanation import draft, review
from tests.test_trust_pipeline import analysis, citation


def original_plan(level='LEVEL_B'):
    item=analysis(level)
    item.question_parts=[QuestionPart(question='Explain this passage and compare it with another text.',
        keywords=item.keywords,requirements=[
            {'kind':'explanation','description':'Explain what this passage says.'},
            {'kind':'comparison','description':'Compare with the requested other text.'}])]
    return item


def partial_plan():
    return PartialEvidencePlan(supported_parts=[QuestionPart(question='What does this passage say?',
        keywords=analysis().keywords,
        requirements=[{'kind':'explanation','description':'Explain this passage.'}])],
        unanswered_aspects={'en':['Comparison with the other requested text.'],
            'ar':['المقارنة مع النص الآخر المطلوب.']},selection=selection())


def partial_review():
    result=PartialExplanationReview(**review().model_dump(),partial_scope_valid=True,limitations_accurate=True)
    result.coverage=[AnswerCoverage(part_id=0,language=l,segment_ids=[0],answered=True,
        reason='Supported aspect explained.',requirements=[{'requirement_id':0,'segment_ids':[0]}])
        for l in ('en','ar')]
    return result


def pipeline(plan=None, assessment=None):
    analyzer=AnalysisAgent(time.monotonic()+60)
    analyzer.check_quota=Mock()
    analyzer.analyze=Mock(return_value=original_plan())
    analyzer.select=Mock(side_effect=SafetyStop('no_direct_answer'))
    analyzer.request=Mock(return_value=plan or partial_plan())
    sources=Mock();sources.unavailable=[];sources.retrieve.return_value=[citation()]
    writer=CulturalLocalizerAgent(time.monotonic()+60)
    writer.policy={**writer.policy,'explanation_attempts':1}
    writer.client.request=Mock(side_effect=[draft(),assessment or partial_review()])
    pipe=MasterOrchestrator(lambda _:analyzer,lambda _:sources,localizer_factory=lambda _:writer)
    pipe.config={**pipe.config,'retrieval_attempts':1}
    return pipe,analyzer,sources,writer


def test_missing_comparison_publishes_reviewed_explanation_and_clear_gaps():
    pipe,analyzer,sources,writer=pipeline()
    result=pipe.run_pipeline(PipelineInput(query='Explain this passage and compare it with another text.'))
    assert result.answer_status=='PARTIAL' and result.source_verified
    assert result.citations==[citation()] and result.stop_reason is None
    assert result.analysis.question_parts==original_plan().question_parts
    assert [v.language for v in result.versions]==['en','ar']
    for version in result.versions:
        assert version.scope_notice.startswith(load_policy()['messages'][version.language]['partial_notice'])
        assert version.text.startswith(version.scope_notice)
        assert version.source_text==citation().text_for(version.language)
        assert version.explanation_segments
    request=writer.client.request.call_args_list[1]
    assert request.args[2] is PartialExplanationReview
    assert request.args[1]['answer_scope']['original_question_parts']==[
        p.model_dump(mode='json') for p in original_plan().question_parts]
    assert result.linter_status=='APPROVED_SAFE'
    assert result.provenance_hash
    store=AuthenticatedStore()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='decision'").fetchone()[0]==0


@pytest.mark.parametrize('failure',['invented_witness','low_confidence','no_useful_scope',
    'missing_language','blank_gap','unsupported_scope','missing_witness','invalid_candidate'])
def test_partial_recovery_never_approves_bad_evidence_or_hides_missing_coverage(failure):
    plan=partial_plan()
    if failure=='invented_witness':plan.selection.coverage[0].witnesses[0].source_text='Absent from original.'
    if failure=='low_confidence':plan.selection.decisions[0].confidence=.1
    if failure=='no_useful_scope':plan.supported_parts=[]
    if failure=='missing_language':del plan.unanswered_aspects['ar']
    if failure=='blank_gap':plan.unanswered_aspects['en']=[' ']
    if failure=='unsupported_scope':plan.selection.fully_answers_question=False
    if failure=='missing_witness':plan.selection.coverage[0].witnesses=[]
    if failure=='invalid_candidate':plan.selection.coverage[0].candidate_ids=[9]
    analyzer=AnalysisAgent(time.monotonic()+60);analyzer.request=Mock(return_value=plan)
    with pytest.raises(SafetyStop):
        recover_partial(analyzer,PipelineInput(query='Explain and compare'),original_plan(),[citation()],['en','ar'])


@pytest.mark.parametrize('failure',['partial_scope_valid','limitations_accurate','versions_agree',
    'preserves_qualifications','unsupported_prose','missing_requirement'])
def test_partial_output_still_requires_independent_review(failure):
    assessment=partial_review()
    if failure=='unsupported_prose':assessment.checks[0].supported=False
    elif failure=='missing_requirement':assessment.coverage[0].requirements=[]
    else:setattr(assessment,failure,False)
    pipe,_,_,_=pipeline(assessment=assessment)
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    assert result.answer_status=='ABSTAINED' and not result.citations
    assert not result.source_verified
    assert all(not v.scope_notice for v in result.versions)


def test_no_evidence_does_not_show_irrelevant_sources():
    pipe,analyzer,_,_=pipeline()
    analyzer.request.return_value=partial_plan()
    analyzer.request.return_value.supported_parts=[]
    result=pipe.run_pipeline(PipelineInput(query='Unsupported question'))
    assert result.answer_status=='ABSTAINED' and not result.citations


def test_personal_ruling_cannot_enter_partial_recovery():
    pipe,analyzer,sources,_=pipeline()
    result=pipe.run_pipeline(PipelineInput(query='Is my marriage valid?'))
    assert result.answer_status=='REFERRED' and not result.citations
    analyzer.request.assert_not_called();sources.retrieve.assert_not_called()


@pytest.mark.parametrize('level',['LEVEL_A','LEVEL_B','LEVEL_C'])
def test_unmet_single_primary_quote_cannot_be_replaced_by_another_text(level):
    analyzer=AnalysisAgent(time.monotonic()+60);analyzer.request=Mock(return_value=partial_plan())
    item=original_plan(level)
    item.question_parts=[QuestionPart(question=item.question_parts[0].question,
        keywords=item.keywords,requirements=[{'kind':'exact_quote',
            'description':'The exact requested authenticated narration.'}])]
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        recover_partial(analyzer,PipelineInput(query='Give the requested narration'),item,[citation()],['en','ar'])
    analyzer.request.assert_not_called()


def test_level_c_qualifications_cannot_be_removed_by_reduced_scope():
    analyzer=AnalysisAgent(time.monotonic()+60);analyzer.request=Mock(return_value=partial_plan())
    with pytest.raises(SafetyStop,match='missing_scholarly_qualification'):
        recover_partial(analyzer,PipelineInput(query='A disputed comparison'),original_plan('LEVEL_C'),[citation()],['en','ar'])


@pytest.mark.parametrize('qualified',[True,False])
def test_exhausted_level_c_selection_attempts_qualified_partial_recovery(qualified):
    plan=partial_plan();plan.selection.explains_disagreement=qualified
    pipe,analyzer,_,writer=pipeline(plan=plan)
    analyzer.analyze.return_value=original_plan('LEVEL_C')
    analyzer.select.side_effect=SafetyStop('missing_scholarly_qualification')
    result=pipe.run_pipeline(PipelineInput(query='A disputed comparison'))
    assert analyzer.request.called
    if qualified:
        assert result.answer_status=='PARTIAL' and result.citations
        assert result.response_level.value=='LEVEL_C'
        assert writer.client.request.call_count==2
    else:
        assert result.answer_status=='ABSTAINED' and not result.citations
        assert result.stop_reason=='missing_scholarly_qualification'
        writer.client.request.assert_not_called()


def test_partial_qualification_repair_retains_gate_and_failure_feedback():
    analyzer=AnalysisAgent(time.monotonic()+60)
    rejected=partial_plan();qualified=partial_plan()
    qualified.selection.explains_disagreement=True
    analyzer.selection_feedback={'explains_disagreement':False}
    analyzer.request=Mock(side_effect=[rejected,qualified])
    selected,scoped,_=recover_partial(analyzer,PipelineInput(query='A disputed comparison'),
        original_plan('LEVEL_C'),[citation()],['en','ar'])
    assert selected==[citation()] and scoped.level.value=='LEVEL_C'
    first=analyzer.request.call_args_list[0].args[1]
    last=analyzer.request.call_args.args[1]
    assert first['previous_selection_feedback']=={'explains_disagreement':False}
    assert last['validation_failure']=='missing_scholarly_qualification'


def test_partial_disclosure_is_inside_content_fingerprint():
    pipe,_,_,_=pipeline()
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    before=result.provenance_hash
    result.versions[0].scope_notice='Changed disclosure'
    MasterOrchestrator.seal(result)
    assert result.provenance_hash!=before


def test_partial_contracts_use_configured_model_and_localized_labels():
    from src.core.model_router import models_for, routing_config
    for contract in (PartialEvidencePlan,PartialExplanationReview):
        assert models_for(contract,{'level':'LEVEL_C'},routing_config())
        assert provider_schema(contract)
    assert all(labels.get('partial') and labels.get('partial_notice') for labels in load_policy()['messages'].values())


def test_supported_prose_with_incomplete_final_review_can_be_redrafted_as_partial():
    pipe,analyzer,_,writer=pipeline()
    analyzer.select.side_effect=lambda inp,plan,candidates:candidates
    incomplete=partial_review().model_dump(exclude={'partial_scope_valid','limitations_accurate'})
    incomplete['fully_answers_question']=False
    incomplete['coverage'][0]['answered']=False
    from src.core.schema import ExplanationReview
    writer.client.request=Mock(side_effect=[draft(),ExplanationReview(**incomplete),draft(),partial_review()])
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    assert result.answer_status=='PARTIAL'
    assert writer.client.request.call_count==4
    assert result.versions[0].scope_notice


def test_unsupported_complete_prose_cannot_enter_coverage_recovery():
    pipe,analyzer,_,writer=pipeline()
    analyzer.select.side_effect=lambda inp,plan,candidates:candidates
    incomplete=partial_review().model_dump(exclude={'partial_scope_valid','limitations_accurate'})
    incomplete['fully_answers_question']=False
    incomplete['checks'][0]['supported']=False
    from src.core.schema import ExplanationReview
    writer.client.request=Mock(side_effect=[draft(),ExplanationReview(**incomplete)])
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    assert result.answer_status=='ABSTAINED'
    analyzer.request.assert_not_called()


def test_repeated_search_plan_preserves_useful_partial_evidence():
    pipe,analyzer,_,_=pipeline()
    pipe.config={**pipe.config,'retrieval_attempts':2}
    analyzer.expand_search=Mock(side_effect=SafetyStop('repeated_search_plan'))
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    assert result.answer_status=='PARTIAL'


def test_failed_later_fetch_does_not_discard_earlier_useful_evidence():
    pipe,analyzer,sources,_=pipeline()
    pipe.config={**pipe.config,'retrieval_attempts':2}
    analyzer.expand_search=Mock(return_value=original_plan())
    sources.retrieve.side_effect=[[citation()],SafetyStop('no_approved_passages_in_required_languages')]
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    assert result.answer_status=='PARTIAL'
    assert result.citations==[citation()]


def test_partial_answer_api_keeps_status_disclosure_and_source_cards(monkeypatch):
    from fastapi.testclient import TestClient
    from src.web import app as web
    pipe,_,_,_=pipeline()
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare'))
    monkeypatch.setattr(web,'get_pipeline',lambda:lambda req:result)
    with TestClient(web.app) as client:
        response=client.post('/api/query',json={'query':'Explain and compare'})
        page=client.get('/').text
    assert response.status_code==200
    payload=response.json()
    assert payload['answer_status']=='PARTIAL'
    assert payload['versions'][0]['scope_notice']
    assert payload['source_cards']['en']
    assert 'version.scope_notice' in page and "data.answer_status === 'PARTIAL'" in page
    assert payload['labels']['partial'] in page


def test_empty_scope_notice_preserves_legacy_receipt_fingerprint():
    from src.core.schema import PipelineOutput, AnswerVersion
    from src.core.integrity import fingerprint
    result=PipelineOutput(query='112:1',target_language='ar',answer_status='ANSWERED',
        versions=[AnswerVersion(language='ar',text=citation().arabic_text)],citations=[citation()],
        localized_text=citation().arabic_text)
    legacy=fingerprint({'query':result.query,'language':result.target_language,
        'persona':result.cultural_persona.value,'level':None,'status':result.answer_status,
        'text':result.localized_text,'versions':[v.model_dump(mode='json',exclude={'scope_notice'}) for v in result.versions],
        'citations':[c.model_dump(mode='json') for c in result.citations]})
    MasterOrchestrator.seal(result)
    assert result.provenance_hash==legacy


def test_partial_request_cannot_demand_original_whole_question_coverage():
    analyzer=AnalysisAgent(time.monotonic()+60);analyzer.request=Mock(return_value=partial_plan())
    recover_partial(analyzer,PipelineInput(query='Explain and compare',cultural_persona=CulturalPersona.WESTERN_SECULAR),
        original_plan(),[citation()],['en','ar'])
    payload=analyzer.request.call_args.args[1]
    assert 'question_parts' not in payload
    assert len(payload['original_question_parts'][0]['requirements'])==2
    assert 'ONLY those proposed supported_parts' in payload['assessment_scope']


def test_partial_selector_repairs_whole_question_rejection_without_relaxing_witnesses():
    analyzer=AnalysisAgent(time.monotonic()+60)
    rejected=partial_plan();rejected.selection.fully_answers_question=False
    analyzer.request=Mock(side_effect=[rejected,partial_plan()])
    selected,scoped,context=recover_partial(analyzer,PipelineInput(query='Explain and compare'),
        original_plan(),[citation()],['en','ar'])
    assert selected==[citation()] and len(scoped.question_parts[0].requirements)==1
    assert context['mode']=='partial_answer' and analyzer.request.call_count==2
    assert 'repair_instruction' in analyzer.request.call_args.args[1]


@pytest.mark.parametrize('persona',[CulturalPersona.GENERAL_GLOBAL,CulturalPersona.WESTERN_SECULAR])
def test_partial_answer_survives_slow_review_without_restarting_work(monkeypatch,persona):
    from types import SimpleNamespace
    import src.core.orchestrator as orchestrator
    clock=[0];monkeypatch.setattr(orchestrator,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    pipe,analyzer,_,writer=pipeline()
    original_review=writer.review
    def delayed_review(*args):
        clock[0]=300
        return original_review(*args)
    writer.review=delayed_review
    result=pipe.run_pipeline(PipelineInput(query='Explain and compare',cultural_persona=persona))
    assert result.answer_status=='PARTIAL' and result.citations
    assert analyzer.request.call_count==1 and result.total_duration_ms==300000


def test_audience_presentation_does_not_change_analysis_or_evidence_payload():
    analyzer=AnalysisAgent(time.monotonic()+60)
    general=PipelineInput(query='Explain and compare')
    western=general.model_copy(update={'cultural_context':'western',
        'cultural_persona':CulturalPersona.WESTERN_SECULAR})
    analyzer.request=Mock(return_value=original_plan())
    analyzer.analyze(general);general_payload=analyzer.request.call_args.args[1]
    analyzer.analyze(western);western_payload=analyzer.request.call_args.args[1]
    assert general_payload==western_payload and 'region' not in western_payload
    assert analyzer.selection_payload(general,original_plan(),[citation()])==analyzer.selection_payload(
        western,original_plan(),[citation()])
    writer=CulturalLocalizerAgent(time.monotonic()+60)
    assert writer.draft_payload(western,original_plan(),[citation()],['en','ar'],
        CulturalPersona.WESTERN_SECULAR)['audience']=='WESTERN_SECULAR'
