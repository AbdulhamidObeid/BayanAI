"""Review feedback cannot approve itself or become source evidence."""
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent,SafetyStop
from src.core.explanation_recovery import reselect_reviewed_answer
from src.core.schema import PipelineInput,CulturalPersona,EvidenceSelection
from tests.test_trust_pipeline import analysis,citation


def test_reselection_uses_larger_pool_and_feedback_but_still_needs_fresh_review():
    agent=AnalysisAgent(float('inf'));first=citation();second=first.model_copy(update={'reference_id':'112:2'})
    agent.request=Mock(return_value=EvidenceSelection(decisions=[
        {'candidate_id':i,'direct_answer':i==1,'confidence':.99,'reason':'Actual support.'} for i in range(2)],fully_answers_question=True,explains_disagreement=False))
    writer=Mock();writer.localize.return_value=['fresh independently reviewed version']
    feedback={'fully_answers_question':False,'missing':'second aspect'}
    selected,versions=reselect_reviewed_answer(agent,lambda _:writer,float('inf'),PipelineInput(query='Explain'),analysis(),[first,second],['en','ar'],CulturalPersona.GENERAL_GLOBAL,feedback)
    assert selected==[second] and versions==writer.localize.return_value
    payload=agent.request.call_args.args[1]
    assert payload['previous_explanation_review']==feedback and len(payload['candidates'])==2
    writer.localize.assert_called_once()


def test_rejected_reselection_cannot_publish_the_previous_draft():
    agent=AnalysisAgent(float('inf'));agent.request=Mock(return_value=EvidenceSelection(decisions=[
        {'candidate_id':0,'direct_answer':False,'confidence':.99,'reason':'Missing evidence.'}],fully_answers_question=False,explains_disagreement=False))
    writer=Mock()
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        reselect_reviewed_answer(agent,lambda _:writer,float('inf'),PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL,{})
    writer.localize.assert_not_called()


def test_reselected_prose_must_pass_the_same_independent_review_gate():
    agent=AnalysisAgent(float('inf'));agent.request=Mock(return_value=EvidenceSelection(decisions=[
        {'candidate_id':0,'direct_answer':True,'confidence':.99,'reason':'Actual support.'}],fully_answers_question=True,explains_disagreement=False))
    writer=Mock();writer.localize.side_effect=SafetyStop('unsupported_personalized_explanation')
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        reselect_reviewed_answer(agent,lambda _:writer,float('inf'),PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL,{})
