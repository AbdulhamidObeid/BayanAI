"""Server span references avoid copying errors without approving absent evidence."""
import time
import pytest
from src.core.analysis_agent import AnalysisAgent, SafetyStop
from src.core.schema import EvidenceSelection, PipelineInput, QueryAnalysis
from tests.test_trust_pipeline import analysis, citation


def selection(witness):
    return EvidenceSelection(decisions=[dict(candidate_id=0,direct_answer=True,
        confidence=.99,reason='Supports requested explanation.')],
        fully_answers_question=True,explains_disagreement=True,
        coverage=[dict(part_id=0,language='ar',candidate_ids=[0],supported=True,
            confidence=.99,reason='Original text.',witnesses=[dict(
                requirement_id=0,candidate_id=0,**witness)])])


def case():
    agent=AnalysisAgent(time.monotonic()+30)
    a=analysis('LEVEL_C').model_dump()
    a['question_parts']=[dict(question='Explain',keywords=a['keywords'],
        requirements=[dict(kind='explanation',description='Explain the published position.')])]
    c=citation(('ar',))
    c.translations['ar']='نص (أصلي)؛\n شرحٌ منشور، لا يجوز تغييره.\n'
    return agent,PipelineInput(query='Explain',target_language='ar'),QueryAnalysis.model_validate(a),[c]


@pytest.mark.parametrize('original',['نص (أصلي)؛\n شرحٌ منشور.\n','\n\n  ',
    'word '*400,'حرف'*400,'Original? Next.\nFinal without punctuation'])
def test_navigation_spans_preserve_every_original_character(original):
    agent=AnalysisAgent(time.monotonic()+30)
    spans=agent.witness_spans(original)
    assert ''.join(spans)==original
    assert all(len(span)<=agent.policy['witness_span_characters'] for span in spans)


def test_span_id_restores_parentheses_diacritics_and_newlines_unchanged():
    agent,inp,a,c=case();result=selection(dict(source_span_id=1))
    before=c[0].translations.copy()
    assert agent.validate_selection(inp,a,c,result)==c
    witness=result.coverage[0].witnesses[0]
    assert witness.source_text==agent.witness_spans(before['ar'])[1]
    assert c[0].translations==before


def test_selection_payload_supplies_complete_original_once_with_explicit_ids():
    agent,inp,a,c=case()
    payload=agent.selection_payload(inp,a,c)['candidates'][0]
    spans=payload['witness_spans']['ar']
    assert [span['span_id'] for span in spans]==list(range(len(spans)))
    assert ''.join(span['text'] for span in spans)==c[0].translations['ar']
    assert 'passages' not in payload


@pytest.mark.parametrize('witness',[dict(source_span_id=999),
    dict(source_span_id=0,source_text='نص أصلي؛'),dict(source_text=''),
    dict(source_text='نص أصلي؛')])
def test_missing_invented_or_mismatched_evidence_is_still_rejected(witness):
    agent,inp,a,c=case()
    with pytest.raises(SafetyStop,match='invented_evidence_witness'):
        agent.validate_selection(inp,a,c,selection(witness))


def test_valid_span_id_does_not_replace_the_scholarly_qualification_gate():
    agent,inp,a,c=case();result=selection(dict(source_span_id=0))
    result.explains_disagreement=False
    with pytest.raises(SafetyStop,match='missing_scholarly_qualification'):
        agent.validate_selection(inp,a,c,result)
