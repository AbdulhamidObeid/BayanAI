"""Display excerpts/citations and independent pipeline work must retain evidence integrity."""
import json
import time
import threading
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent,SafetyStop
from src.core.official_sources import OfficialSources
from src.core.integrity import AuthenticatedStore
from src.core.schema import PipelineInput,PipelineOutput,AnswerVersion,ExplanationSegment,EvidenceCoverage
from src.web.presentation import exact_excerpts,source_cards
from tests.test_trust_pipeline import citation
from tests.test_multipart_coverage import multipart,selection


def test_display_omits_unused_evidence_and_remaps_without_changing_originals():
    citations=[citation(),citation(),citation()]
    out=PipelineOutput(query='question',target_language='ar',citations=citations,
        versions=[AnswerVersion(language='ar',text='explanation',explanation_segments=[
            ExplanationSegment(kind='explanation',text='explanation.',citation_ids=[2])])])
    before=out.model_dump_json();cards=source_cards(out)['ar']
    assert [(c['citation_id'],c['number']) for c in cards]==[(2,1)]
    assert out.model_dump_json()==before


def test_display_numbering_accounts_for_all_language_versions():
    out=PipelineOutput(query='question',target_language='en',citations=[citation(),citation()],versions=[
        AnswerVersion(language=l,text='explanation',explanation_segments=[
            ExplanationSegment(kind='explanation',text='explanation.',citation_ids=[i])]) for l,i in [('en',1),('ar',0)]])
    result=source_cards(out)
    assert [(c['citation_id'],c['number']) for c in result['ar']]==[(0,1),(1,2)]
    assert result['en'][1]['number']==2


def test_scripture_is_never_shortened():
    original='Published scripture. '*200
    assert exact_excerpts(original,'question',scripture=True)==[original]


def test_word_metadata_is_excluded_as_separate_spans_without_rewriting():
    first='هذا نص المصدر الأول دون أي تغيير.'
    last='هذا نص المصدر الثاني دون أي تغيير.'
    original=first+' Normal 0 false false false EN-US X-NONE AR-SA '+last
    assert exact_excerpts(original,'question')==[first,last]
    assert all(piece in original for piece in exact_excerpts(original,'question'))


def test_long_excerpts_preserve_complete_original_sentences_and_bound_size():
    original='Published opening sentence. '+('Unrelated long background sentence. '*150)+'The requested subject has this complete published statement.'
    pieces=exact_excerpts(original,'requested subject')
    assert any('requested subject' in p for p in pieces)
    assert all(p in original and p.endswith('.') for p in pieces)
    assert sum(map(len,pieces))<=1100


def test_independent_part_assessments_overlap_but_preserve_coverage():
    agent=AnalysisAgent(time.monotonic()+10);barrier=threading.Barrier(2)
    candidates=[citation(),citation()];candidates[1].reference_id='112:2'
    def request(instruction,payload,contract):
        barrier.wait(timeout=3)
        i=0 if payload['question']==multipart().question_parts[0].question else 1
        item=selection();item.coverage=[EvidenceCoverage(part_id=0,language=l,candidate_ids=[i],supported=True,confidence=.99,reason='Focused support.') for l in ('en','ar')]
        return item
    agent.request=Mock(side_effect=request)
    assert agent.select(PipelineInput(query='Original question'),multipart(),candidates)==candidates
    assert [f['part_id'] for f in agent.selection_feedback]==[0,1]


def test_one_failed_parallel_part_prevents_an_answer():
    agent=AnalysisAgent(time.monotonic()+10)
    def request(instruction,payload,contract):
        item=selection();good=payload['question']==multipart().question_parts[0].question
        item.coverage=[EvidenceCoverage(part_id=0,language=l,candidate_ids=[0] if good else [],supported=good,confidence=.99,reason='Aspect check.') for l in ('en','ar')]
        return item
    agent.request=Mock(side_effect=request)
    with pytest.raises(SafetyStop,match='no_direct_answer'):
        agent.select(PipelineInput(query='Original question'),multipart(),[citation(),citation()])
    assert agent.missing_parts==[1]


def test_shared_search_concepts_are_requested_once_without_losing_parts(tmp_path,monkeypatch):
    from src.core.publication_reader import PublicationReader
    agent=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path/'private'))
    a=multipart();a.question_parts[1].keywords=a.question_parts[0].keywords
    agent.call=Mock(return_value={'structuredContent':{'results':[]}})
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    agent.discover(a)
    assert agent.call.call_count==6


def test_positive_catalog_is_reused_but_empty_search_is_not(tmp_path,monkeypatch):
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path/'private'))
    result={'structuredContent':{'results':[{'id':'library:1:ar','url':'https://islamcontent.com/ar/content/1'}]}}
    response=Mock(headers={'content-type':'application/json'},content=json.dumps({'id':1,'result':result}).encode())
    post=Mock(return_value=response);monkeypatch.setattr('src.core.official_sources.requests.post',post)
    assert source.call('search',{'query':'topic'})==source.call('search',{'query':'topic'})
    assert post.call_count==1
    response.content=json.dumps({'id':1,'result':{'structuredContent':{'results':[]}}}).encode()
    source.call('search',{'query':'missing'});source.call('search',{'query':'missing'})
    assert post.call_count==3


def test_arabic_diacritics_are_not_mistaken_for_isolated_letter_debris():
    relevant='بِرُّ الوَالِدَيْنِ وَاجِبٌ عَلَى كُلِّ مُسْلِمٍ وَإِنْ كَانَا غَيْرَ مُسْلِمَيْنِ، وَيُصَاحِبُهُمَا بِالمَعْرُوفِ.'
    original=relevant+' '+('قَالَ: «أُمُّكَ» قَالَ: ثُمَّ مَنْ؟ '*40)
    assert relevant in exact_excerpts(original,'بر الوالدين واجب غير مسلمين المعروف')
