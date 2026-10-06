"""Progressive retrieval must widen on missing coverage and preserve complete originals."""
import time
from unittest.mock import Mock
from fastapi.testclient import TestClient
from src.core.analysis_agent import SafetyStop
from src.core.official_sources import OfficialSources
from src.core.integrity import AuthenticatedStore
from src.core.schema import PipelineInput, PipelineOutput, AnswerVersion, ExplanationSegment, QuestionPart, SourcePassage, ResponseLevel
from src.web.presentation import source_cards, presentation_config
from tests.test_trust_pipeline import citation, analysis, harness


def simple_plan():
    plan=analysis('LEVEL_A')
    plan.question_parts=[QuestionPart(question='Who is this person?',keywords=plan.keywords,
        requirements=[{'kind':'explanation','description':'Identify the person.'}])]
    return plan


def test_simple_retrieval_defers_long_story_and_widens_without_cropping(tmp_path):
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path))
    short=citation();story=citation();story.reference_id='story'
    story.translations={l:'Original narrative. '*1000 for l in ('en','ar')}
    references=[('quran',str(i)) for i in range(9)]
    source.discover=Mock(return_value=references)
    source.get=Mock(side_effect=lambda kind,ref,languages:short if ref=='0' else story)
    assert source.retrieve(simple_plan(),['en','ar'])==[short]
    assert source.get.call_count==source.policy['progressive_retrieval']['initial_candidates']
    assert source.has_deferred_references is True
    source.full_retrieval=True;source.get.reset_mock()
    result=source.retrieve(simple_plan(),['en','ar'])
    assert source.get.call_count==9 and story in result
    assert result[1].translations['ar']=='Original narrative. '*1000
    assert source.has_deferred_references is False


def test_insufficient_initial_batch_widens_before_keyword_refinement(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path))
    pipe,agent,sources=harness(a=simple_plan())
    sources.has_deferred_references=True
    def retrieve(*args,**kwargs):
        if sources.full_retrieval is True:sources.has_deferred_references=False
        return [citation()]
    sources.retrieve.side_effect=retrieve
    agent.select.side_effect=[SafetyStop('no_direct_answer'),[citation()]]
    result=pipe.run_pipeline(PipelineInput(query='Who is this person?'))
    assert result.answer_status=='ANSWERED'
    assert sources.full_retrieval is True and sources.retrieve.call_count==2
    agent.expand_search.assert_not_called()


def test_exact_quote_and_sensitive_requests_keep_full_discovery(tmp_path):
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path))
    source.discover=Mock(return_value=[('quran',str(i)) for i in range(9)])
    source.get=Mock(return_value=citation())
    for level,kind in [('LEVEL_A','exact_quote'),('LEVEL_C','explanation')]:
        plan=simple_plan();plan.level=ResponseLevel(level)
        plan.question_parts[0].requirements[0].kind=kind
        source.get.reset_mock();source.retrieve(plan,['en','ar'])
        assert source.get.call_count==9


def test_story_preview_starts_at_opening_and_full_original_is_collapsed():
    c=citation(('ar',));c.content_kind='hadith'
    narration='Complete published narration. '*100
    opening='The story introduces its people and setting in a complete sentence.'
    commentary=opening+' '+('Then he continued the journey with his son. '*100)
    c.passages={'ar':[SourcePassage(kind='hadith',text=narration,reference_id=c.reference_id,source_url=c.source_url),
        SourcePassage(kind='commentary',text=commentary,reference_id=c.reference_id,source_url=c.source_url)]}
    out=PipelineOutput(query='journey son',target_language='ar',citations=[c],versions=[
        AnswerVersion(language='ar',text='Answer.',explanation_segments=[ExplanationSegment(kind='explanation',text='Answer.',citation_ids=[0])])])
    parts=source_cards(out)['ar'][0]['parts']
    assert parts[0]['text']==narration and parts[0]['collapsed'] is True
    preview=next(p for p in parts if p['kind']=='commentary' and p['is_excerpt'])
    assert preview['text'].startswith(opening)
    assert len(preview['text'])<=presentation_config()['excerpt_characters']
    full=next(p for p in parts if p['kind']=='commentary' and p.get('collapsed'))
    assert full['text']==commentary


def test_verification_box_describes_the_issued_128_bit_id():
    from src.web.app import app
    with TestClient(app) as client:
        html=client.get('/').text
    assert 'BYN-XXXX-XXXX' not in html
    assert presentation_config()['verification_placeholder'] in html
    assert 'BYN- يتبعه 32' in html


def test_completed_answer_survives_old_pipeline_deadline(monkeypatch):
    from types import SimpleNamespace
    from src.web import app as web
    import src.core.orchestrator as orchestrator
    clock=[0]
    monkeypatch.setattr(orchestrator,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    pipe,agent,_=harness(c=citation(('ar',)))
    def delayed_selection(inp,analysis,candidates):
        clock[0]=300  # Verification finishes after the former 28-second cutoff.
        return candidates
    agent.select.side_effect=delayed_selection
    monkeypatch.setattr(web,'get_pipeline',lambda:pipe.run_pipeline)
    with TestClient(web.app) as client:
        response=client.post('/api/query',json={'query':'General sensitive question','target_language':'ar','share_response':True})
    assert response.status_code==200
    body=response.json()
    assert body['answer_status']=='ANSWERED' and body['citations'] and body['response_id']
