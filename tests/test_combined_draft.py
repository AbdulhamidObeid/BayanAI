"""Combining proposal generation never removes evidence or independent review gates."""
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent, SafetyStop
from src.core.schema import PipelineInput, CulturalPersona, SourceBoundDraft, EvidenceSelection
from src.agents.cultural_localizer import CulturalLocalizerAgent
from tests.test_trust_pipeline import analysis, citation
from tests.test_personalized_explanation import draft, review


def proposal(ids=(0,),supported=True):
    return SourceBoundDraft(selection=EvidenceSelection(decisions=[
        {'candidate_id':i,'direct_answer':i in ids,'confidence':.99,'reason':'Exact evidence.'}
        for i in range(2)],fully_answers_question=supported,explains_disagreement=False),draft=draft())


def agents(result):
    analyzer=AnalysisAgent(time.monotonic()+60)
    analyzer.request=Mock(return_value=result)
    writer=CulturalLocalizerAgent(analyzer.deadline)
    writer.policy={**writer.policy,'explanation_attempts':1}
    writer.client.request=Mock(return_value=review())
    return analyzer,writer


def inputs():
    first=citation();second=citation();second.reference_id='112:2'
    return PipelineInput(query='Explain'),analysis(),[first,second],['en','ar'],CulturalPersona.GENERAL_GLOBAL


def test_combined_proposal_still_requires_separate_review_and_inserts_original_quotes():
    analyzer,writer=agents(proposal())
    inp,a,c,langs,persona=inputs()
    selected,prepared=writer.select_and_draft(analyzer,inp,a,c,langs,persona)
    versions=writer.localize(inp,a,selected,langs,persona,prepared_draft=prepared)
    assert analyzer.request.call_count==1 and writer.client.request.call_count==1
    assert writer.client.request.call_args.args[2].__name__=='ExplanationReview'
    assert versions[0].explanation_segments[1].text==c[0].translations['en']


def test_no_direct_evidence_blocks_proposed_answer_before_review():
    analyzer,writer=agents(proposal(supported=False))
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        writer.select_and_draft(analyzer,*inputs())
    writer.client.request.assert_not_called()


def test_draft_cannot_cite_rejected_discovery_candidate():
    result=proposal(ids=(1,))
    analyzer,writer=agents(result)
    with pytest.raises(SafetyStop,match='unselected_explanation_citation'):
        writer.select_and_draft(analyzer,*inputs())
    writer.client.request.assert_not_called()


def test_original_candidate_ids_remap_to_selected_citation_positions():
    result=proposal(ids=(1,))
    for v in result.draft.versions:
        for s in v.segments:s.citation_ids=[1]
    analyzer,writer=agents(result)
    inp,a,c,langs,persona=inputs()
    selected,prepared=writer.select_and_draft(analyzer,inp,a,c,langs,persona)
    assert selected==[c[1]]
    assert all(s.citation_ids==[0] for v in prepared.versions for s in v.segments)
    assert result.draft.versions[0].segments[0].citation_ids==[1]


def test_independent_review_rejection_still_blocks_combined_draft():
    analyzer,writer=agents(proposal())
    rejected=review();rejected.checks[0].supported=False
    writer.client.request.return_value=rejected
    inp,a,c,langs,persona=inputs()
    selected,prepared=writer.select_and_draft(analyzer,inp,a,c,langs,persona)
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        writer.localize(inp,a,selected,langs,persona,prepared_draft=prepared)


def test_combined_selection_rejects_invented_exact_witness():
    result=proposal()
    a=analysis();a.question_parts=[{'question':'Explain','keywords':a.keywords,
        'requirements':[{'kind':'explanation','description':'Explain the source.'}]}]
    a=type(a).model_validate(a.model_dump())
    result.selection.coverage=[{'part_id':0,'language':l,'candidate_ids':[0],
        'witnesses':[{'requirement_id':0,'candidate_id':0,'source_text':'Invented source span'}],
        'supported':True,'confidence':.99,'reason':'x'} for l in ['en','ar']]
    result=SourceBoundDraft.model_validate(result.model_dump())
    analyzer,writer=agents(result)
    inp,_,c,langs,persona=inputs()
    with pytest.raises(SafetyStop,match='invented_evidence_witness'):
        writer.select_and_draft(analyzer,inp,a,c,langs,persona)


@pytest.mark.parametrize('case',['exact','multipart','quotation','sensitive','personal','no_requirements','ordinary'])
def test_combination_is_limited_to_simple_explanations(case):
    from src.core.orchestrator import MasterOrchestrator
    from src.core.schema import QueryAnalysis
    a=analysis();data=a.model_dump()
    data['question_parts']=[{'question':'Explain','keywords':a.keywords,
        'requirements':[{'kind':'explanation','description':'Explain this.'}]}]
    if case=='multipart':data['question_parts']*=2
    if case=='quotation':data['question_parts'][0]['requirements'][0]['kind']='exact_quote'
    if case=='sensitive':data['level']='LEVEL_C'
    if case=='personal':data['level']='LEVEL_D'
    if case=='no_requirements':data['question_parts'][0]['requirements']=[]
    assert MasterOrchestrator().can_combine_draft(QueryAnalysis.model_validate(data),exact=case=='exact') == (case in ('ordinary','sensitive','multipart'))


def test_combination_still_rejects_oversized_or_mixed_quotation_parts():
    from src.core.orchestrator import MasterOrchestrator
    from src.core.schema import QuestionPart
    a=analysis();part=QuestionPart(question='Explain',keywords=a.keywords,
        requirements=[{'kind':'comparison','description':'Compare both aspects.'}])
    a.question_parts=[part]*4
    assert not MasterOrchestrator().can_combine_draft(a)
    a.question_parts=[part,part.model_copy(deep=True)]
    assert MasterOrchestrator().can_combine_draft(a)
    a.question_parts[1].requirements[0].kind='exact_quote'
    assert not MasterOrchestrator().can_combine_draft(a)


def test_sensitive_combined_proposal_cannot_skip_scholarly_qualification():
    analyzer,writer=agents(proposal())
    inp,_,c,langs,persona=inputs()
    a=analysis('LEVEL_C')
    with pytest.raises(SafetyStop,match='missing_scholarly_qualification'):
        writer.select_and_draft(analyzer,inp,a,c,langs,persona)
    writer.client.request.assert_not_called()


def test_qualified_sensitive_combined_proposal_still_needs_independent_review():
    result=proposal();result.selection.explains_disagreement=True
    analyzer,writer=agents(result)
    inp,_,c,langs,persona=inputs();a=analysis('LEVEL_C')
    selected,prepared=writer.select_and_draft(analyzer,inp,a,c,langs,persona)
    rejected=review();rejected.preserves_qualifications=False
    writer.client.request.return_value=rejected
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        writer.localize(inp,a,selected,langs,persona,prepared_draft=prepared)
    assert writer.client.request.call_count==1
