"""Multipart completeness, fair discovery and retained evidence regressions."""
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent, SafetyStop, provider_schema
from src.core.schema import (QuestionPart, EvidenceCoverage, EvidenceSelection,
    AnswerCoverage, QueryAnalysis, PipelineInput, CulturalPersona)
from src.core.official_sources import OfficialSources
from src.core.integrity import AuthenticatedStore
from tests.test_trust_pipeline import analysis, citation, harness
from tests.test_personalized_explanation import localizer, review


def multipart():
    result=analysis()
    result.question_parts=[QuestionPart(question=q,keywords={'ar':[ar],'en':[en]})
        for q,ar,en in [('Why honor parents?','بر الوالدين','honoring parents'),
            ('Does it include non-Muslim parents?','الوالدين الكافرين','non-Muslim parents')]]
    return result


def selection():
    return EvidenceSelection(decisions=[dict(candidate_id=i,direct_answer=True,confidence=.99,reason='Supports one aspect.') for i in range(2)],
        fully_answers_question=True,explains_disagreement=False,
        coverage=[EvidenceCoverage(part_id=i,language=l,candidate_ids=[i],supported=True,confidence=.99,reason='Direct aspect support.')
            for i in range(2) for l in ('en','ar')])


def test_separate_sources_can_collectively_answer_all_parts():
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=selection())
    candidates=[citation(),citation()];candidates[1].reference_id='112:2'
    assert agent.select_part(PipelineInput(query='Two questions'),multipart(),candidates)==candidates


@pytest.mark.parametrize('failure',['missing','duplicate','unknown_part','unknown_source','low_confidence','not_supported','empty'])
def test_aggregate_success_cannot_hide_bad_part_coverage(failure):
    result=selection()
    if failure=='missing':result.coverage.pop()
    if failure=='duplicate':result.coverage[-1]=result.coverage[0]
    if failure=='unknown_part':result.coverage[-1].part_id=9
    if failure=='unknown_source':result.coverage[-1].candidate_ids=[9]
    if failure=='low_confidence':result.coverage[-1].confidence=.1
    if failure=='not_supported':result.coverage[-1].supported=False
    if failure=='empty':result.coverage[-1].candidate_ids=[]
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=result)
    with pytest.raises(SafetyStop):agent.select_part(PipelineInput(query='Two questions'),multipart(),[citation(),citation()])


def test_refinement_preserves_first_parts_evidence(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    first=citation();second=citation();second.reference_id='112:2';second.source_digest=OfficialSources.digest(second)
    pipe,agent,sources=harness(a=multipart())
    sources.retrieve.side_effect=[[first],[second]]
    agent.select.side_effect=[SafetyStop('no_direct_answer'),[first,second]]
    agent.expand_search.return_value=multipart()
    result=pipe.run_pipeline(PipelineInput(query='Two questions'))
    assert result.answer_status=='ANSWERED'
    assert agent.select.call_args_list[1].args[2]==[first,second]
    assert result.citations==[first,second]


@pytest.mark.parametrize('failure',['missing_language','missing_part','unanswered','quote_only','unknown_segment'])
def test_supported_written_paragraphs_cannot_hide_an_unanswered_question_part(failure):
    result=review()
    result.coverage=[AnswerCoverage(part_id=i,language=l,segment_ids=[0],answered=True,reason='The paragraph answers the aspect.')
        for i in range(2) for l in ('en','ar')]
    if failure=='missing_language':result.coverage=[c for c in result.coverage if c.language=='en']
    if failure=='missing_part':result.coverage=[c for c in result.coverage if c.part_id==0]
    if failure=='unanswered':result.coverage[-1].answered=False
    if failure=='quote_only':result.coverage[-1].segment_ids=[1]
    if failure=='unknown_segment':result.coverage[-1].segment_ids=[9]
    with pytest.raises(SafetyStop):
        localizer(r=result).localize(PipelineInput(query='Two questions'),multipart(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)


def test_classifier_repairs_invalid_keywords_before_retrieval():
    invalid=multipart();invalid.keywords={'ar':['هذا مثال طويل جدا يحتوي على اكثر من ثماني كلمات متتالية'],'en':['parents']}
    valid=multipart()
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(side_effect=[invalid,valid])
    assert agent.analyze(PipelineInput(query='Two questions'))==valid
    assert agent.request.call_count==2


def test_provider_schema_bounds_concepts_in_question_parts():
    schema=provider_schema(QueryAnalysis)
    for node in (schema,schema['$defs']['QuestionPart']):
        concepts=node['properties']['keywords']['additionalProperties']
        from src.core.source_policy import load_policy
        assert concepts['maxItems']==load_policy()['max_keywords'] and concepts['items']['maxLength']==80
        import re
        assert not re.fullmatch(concepts['items']['pattern'],'بر الوالد غير المسلم')
        assert re.fullmatch(concepts['items']['pattern'],'الوالدين غير المسلمين')


def test_discovery_budget_does_not_starve_a_later_aspect(tmp_path):
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path/'discovery'))
    source.policy={**source.policy,'max_candidates':4}
    source.rank_local_index=Mock(side_effect=[[('quran',f'2:{i}') for i in range(1,9)],[('quran','31:15')]])
    source.call=Mock(return_value={'structuredContent':{'results':[]}})
    result=source.discover(multipart())
    assert ('quran','31:15') in result
    assert len(result)==4


def test_search_only_phrase_splitting_preserves_all_words_and_bounds():
    agent=AnalysisAgent(time.monotonic()+60)
    original={'ar':['بر الوالد غير المسلم'],'en':['honoring non Muslim parents']}
    short=agent.prepare_keywords(original)
    agent.validate_keywords(short)
    for language in original:
        assert set(' '.join(original[language]).split())==set(' '.join(short[language]).split())
        assert all(len(concept.split())<=3 for concept in short[language])


def test_each_aspect_is_assessed_with_its_own_question_and_original_context():
    agent=AnalysisAgent(time.monotonic()+60)
    results=[]
    for source_id in range(2):
        item=selection()
        item.coverage=[EvidenceCoverage(part_id=0,language=l,candidate_ids=[source_id],supported=True,confidence=.99,reason='Focused support.') for l in ('en','ar')]
        results.append(item)
    agent.request=Mock(side_effect=results)
    candidates=[citation(),citation()];candidates[1].reference_id='112:2'
    result=agent.select(PipelineInput(query='Original compound question'),multipart(),candidates)
    assert result==candidates
    for i,call in enumerate(agent.request.call_args_list):
        assert call.args[1]['question']==multipart().question_parts[i].question
        assert call.args[1]['original_question']=='Original compound question'
        assert len(call.args[1]['question_parts'])==1


def test_arabic_prefix_discovery_keeps_original_record_unmodified():
    source=OfficialSources(time.monotonic()+60)
    rows=[(17,23,'','وبالوالدين إحسانا','',''),(2,1,'unrelated','كلمة اخرى','','')]
    assert source.rank_local_index(rows,{'ar':['الوالدين'],'en':['parents']})==[('quran','17:23')]
    assert rows[0][3]=='وبالوالدين إحسانا'


def test_cached_decision_omits_subquestion_wording_and_changed_evidence_reanalyzes(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    pipe,agent,sources=harness(a=multipart())
    inp=PipelineInput(query='Original uniquely worded compound inquiry')
    assert pipe.run_pipeline(inp).answer_status=='ANSWERED'
    store=AuthenticatedStore(tmp_path/'private')
    import json
    with store.connect() as db:
        payload=json.loads(db.execute("SELECT payload FROM records WHERE kind='decision'").fetchone()[0])
    assert payload['analysis']['question_parts']==[]
    assert inp.query not in json.dumps(payload)
    assert pipe.run_pipeline(inp).answer_status=='ANSWERED'
    assert agent.analyze.call_count==1
    changed=citation();changed.reference_id='112:2';changed.source_digest=OfficialSources.digest(changed)
    sources.retrieve.return_value=[changed]
    assert pipe.run_pipeline(inp).answer_status=='ANSWERED'
    assert agent.analyze.call_count==2
    assert agent.select.call_args.args[1].question_parts==multipart().question_parts
