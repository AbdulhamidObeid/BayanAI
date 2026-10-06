"""Deliverable coverage cannot be replaced by adjacent-topic prose or foreign sources."""
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent,SafetyStop,provider_schema
from src.core.schema import QuestionPart,QueryAnalysis,EvidenceSelection,AnswerCoverage,PipelineInput,CulturalPersona
from src.core.official_sources import OfficialSources
from tests.test_trust_pipeline import analysis,citation,harness
from tests.test_personalized_explanation import localizer,review,draft


def plan(kind='exact_quote'):
    a=analysis('LEVEL_A')
    a.question_parts=[QuestionPart(question='Provide the requested original text.',keywords=a.keywords,
        requirements=[{'kind':kind,'description':'Include the actual requested text.'}])]
    return a


def selection(witness=True):
    c=citation();return EvidenceSelection(decisions=[dict(candidate_id=0,direct_answer=True,confidence=.99,reason='Direct source text.')],
        fully_answers_question=True,explains_disagreement=False,coverage=[dict(part_id=0,language=l,candidate_ids=[0],supported=True,confidence=.99,reason='Source text supplied.',
            witnesses=[{'requirement_id':0,'candidate_id':0,'source_text':c.text_for(l)}] if witness else []) for l in ('en','ar')])


def test_aggregate_approval_cannot_replace_missing_deliverable_evidence():
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=selection(False))
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        agent.select(PipelineInput(query='Provide the text'),plan(),[citation()])
    assert agent.missing_parts==[0]


def test_invented_evidence_span_is_rejected():
    result=selection();result.coverage[0].witnesses[0].source_text='This does not exist in the source.'
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=result)
    with pytest.raises(SafetyStop,match='invented_evidence_witness'):
        agent.select(PipelineInput(query='Provide the text'),plan(),[citation()])
    assert agent.request.call_count==agent.policy['analysis_attempts']


def test_inexact_witness_gets_bounded_reassessment_with_exact_source_still_required():
    invalid=selection();invalid.coverage[0].witnesses[0].source_text='Paraphrased witness'
    agent=AnalysisAgent(time.monotonic()+60)
    agent.request=Mock(side_effect=[invalid,selection()])
    assert agent.select(PipelineInput(query='Provide the text'),plan(),[citation()])==[citation()]
    assert 'exact substring' in agent.request.call_args.args[1]['repair_instruction']


def test_requirement_evidence_is_exact_and_forwarded_to_the_checker():
    agent=AnalysisAgent(time.monotonic()+60);agent.request=Mock(return_value=selection())
    assert agent.select(PipelineInput(query='Provide the text'),plan(),[citation()])==[citation()]
    assert agent.request.call_args.args[1]['question_parts'][0]['requirements'][0]['kind']=='exact_quote'


@pytest.mark.parametrize('problem',['prose_only','missing_requirement','wrong_requirement'])
def test_supported_prose_cannot_fulfill_an_actual_text_request(problem):
    r=review();r.coverage=[AnswerCoverage(part_id=0,language=l,segment_ids=[0,1],answered=True,reason='All requested content supplied.',
        requirements=[{'requirement_id':0,'segment_ids':[0] if problem=='prose_only' else [1]}]) for l in ('en','ar')]
    if problem=='missing_requirement':r.coverage[0].requirements=[]
    if problem=='wrong_requirement':r.coverage[0].requirements[0].requirement_id=8
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        localizer(r=r).localize(PipelineInput(query='Provide the text'),plan(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)


def test_complete_server_inserted_quote_fulfills_the_explicit_requirement():
    r=review();r.coverage=[AnswerCoverage(part_id=0,language=l,segment_ids=[0,1],answered=True,reason='Requested original included.',
        requirements=[{'requirement_id':0,'segment_ids':[1]}]) for l in ('en','ar')]
    result=localizer(r=r).localize(PipelineInput(query='Provide the text'),plan(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert result[1].explanation_segments[1].text==citation().arabic_text


def test_foreign_book_cannot_enter_arabic_answer_even_if_selector_approves(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    c=citation(('en',));c.content_kind='book_excerpt';c.arabic_text='';c.source_digest=OfficialSources.digest(c)
    pipe,agent,sources=harness(c=c,a=analysis('LEVEL_B'));agent.expand_search.return_value=analysis('LEVEL_B')
    result=pipe.run_pipeline(PipelineInput(query='General question',target_language='ar'))
    assert result.answer_status=='ABSTAINED' and not result.citations
    assert sources.retrieve.call_count==pipe.config['retrieval_attempts']
    agent.select.assert_not_called()


def test_level_a_cannot_use_an_article_as_its_primary_text(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    c=citation();c.content_kind='article';c.source_digest=OfficialSources.digest(c)
    pipe,agent,sources=harness(c=c,a=plan());agent.expand_search.return_value=plan()
    result=pipe.run_pipeline(PipelineInput(query='Retrieve original text'))
    assert result.answer_status=='ABSTAINED' and not result.citations
    agent.select.assert_not_called()


def test_provider_requires_deliverables_and_retains_local_bounds():
    schema=provider_schema(QueryAnalysis);part=schema['$defs']['QuestionPart']
    assert 'requirements' in part['required']
    assert part['properties']['requirements']['minItems']==1
    with pytest.raises(ValueError):QuestionPart(question='x',keywords={},requirements=[{'kind':'exact_quote','description':'x'}]*9)


def test_catalog_ranking_can_rescue_a_result_beyond_the_old_fetch_cap(monkeypatch,tmp_path):
    from src.core.integrity import AuthenticatedStore
    from src.core.publication_reader import PublicationReader
    from src.core.schema import DiscoveryRanking
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path/'discovery'))
    source.policy={**source.policy,'discovery_ranking_enabled':True}
    source.rank_local_index=Mock(return_value=[('quran',f'2:{i}') for i in range(1,41)])
    records=[{'id':f'hadith:{i}:ar','title':'Target text' if i==24 else 'Adjacent background',
        'url':f'https://hadeethenc.com/ar/browse/hadith/{i}'} for i in range(1,25)]
    source.call=Mock(return_value={'structuredContent':{'results':records}})
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    observed=[]
    def rank(self,instruction,payload,contract):
        observed.extend(payload['catalog'])
        return DiscoveryRanking(candidate_ids=[row['candidate_id'] for row in payload['catalog'] if row['title']=='Target text'])
    monkeypatch.setattr(AnalysisAgent,'request',rank)
    assert source.discover(plan())==[('hadith','24')]
    assert next(r['candidate_id'] for r in observed if r['title']=='Target text')>=32
    assert source.call.call_args.args[1]['limit']==24


def test_catalog_ranker_cannot_invent_a_reference(monkeypatch,tmp_path):
    from src.core.integrity import AuthenticatedStore
    from src.core.publication_reader import PublicationReader
    from src.core.schema import DiscoveryRanking
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path/'discovery'))
    source.policy={**source.policy,'discovery_ranking_enabled':True}
    source.rank_local_index=Mock(return_value=[])
    source.call=Mock(return_value={'structuredContent':{'results':[{'id':'hadith:24:ar','title':'Observed title','url':'https://hadeethenc.com/ar/browse/hadith/24'}]}})
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    monkeypatch.setattr(AnalysisAgent,'request',Mock(return_value=DiscoveryRanking(candidate_ids=[999])))
    assert source.discover(plan())==[('hadith','24')]
    assert 'catalog_ranking_unavailable' in source.unavailable


def test_primary_quotation_cannot_be_satisfied_by_commentary_at_level_b():
    item=plan();item.level='LEVEL_B';item=QueryAnalysis.model_validate(item.model_dump())
    item.question_parts[0].requirements[0].description='Provide the requested authenticated hadith.'
    c=citation();c.content_kind='book_excerpt'
    agent=AnalysisAgent(time.monotonic()+60)
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        agent.validate_selection(PipelineInput(query='Give a hadith'),item,[c],selection())
