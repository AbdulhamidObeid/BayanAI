"""Learned concept navigation saves retrieval work without transferring approval."""
import time
from unittest.mock import Mock
import pytest
from src.core.evidence_routes import EvidenceRoutes
from src.core.integrity import AuthenticatedStore
from src.core.source_index import SourceIndex
from src.core.schema import PipelineInput, QuestionPart, SourceBoundDraft, EvidenceSelection
from src.core.analysis_agent import AnalysisAgent, SafetyStop
from src.agents.cultural_localizer import CulturalLocalizerAgent
from tests.test_source_index import original
from tests.test_trust_pipeline import analysis, citation
from tests.test_answer_requirements import selection
from tests.test_personalized_explanation import draft, review


def topic_plan():
    a=analysis();a.keywords={'ar':['تصنيف تجريبي'],'en':['hypothetical network']}
    return a


def setup(tmp_path):
    store=AuthenticatedStore(tmp_path);index=SourceIndex(store);c=original();index.put(c)
    return store,index,c,EvidenceRoutes(store)


def test_reviewed_concepts_find_original_with_no_literal_topic_overlap(tmp_path):
    store,index,c,routes=setup(tmp_path);a=topic_plan()
    assert not index.search(a,['ar'])
    with store.connect() as db:
        before=db.execute("SELECT payload,signature,expires FROM records WHERE kind='indexed_source'").fetchall()
    assert routes.remember(a,[c])
    found=index.search(a,['ar'])
    assert found[0].arabic_text==c.arabic_text and found[0].source_digest==c.source_digest
    with store.connect() as db:
        assert db.execute("SELECT payload,signature,expires FROM records WHERE kind='indexed_source'").fetchall()==before
        payload=db.execute("SELECT payload FROM records WHERE kind='evidence_route'").fetchone()[0]
    assert 'explanation' not in payload and c.arabic_text not in payload


@pytest.mark.parametrize('failure',['expired_route','tampered_route','expired_source','changed_digest'])
def test_navigation_never_supplies_expired_or_forged_evidence(tmp_path,failure):
    store,index,c,routes=setup(tmp_path);a=topic_plan();routes.remember(a,[c])
    if failure=='changed_digest':
        c.translations['ar']=c.arabic_text=c.accredited_translation='تغير النص المنشور.'
        c.passages['ar'][0].text=c.arabic_text
        from src.core.official_sources import OfficialSources
        c.source_digest=OfficialSources.digest(c);index.put(c)
    else:
        with store.connect() as db:
            if failure=='expired_route':db.execute("UPDATE records SET expires=0 WHERE kind='evidence_route'")
            if failure=='tampered_route':db.execute("UPDATE records SET signature='fake' WHERE kind='evidence_route'")
            if failure=='expired_source':db.execute("UPDATE records SET expires=0 WHERE kind='indexed_source'")
    assert index.search(a,['ar'])==[]


def test_routes_cannot_add_a_missing_scripture_translation(tmp_path):
    store,index,_,routes=setup(tmp_path);c=citation(('ar',));index.put(c)
    routes.remember(topic_plan(),[c])
    assert index.search(topic_plan(),['en','ar'])==[]


def test_generic_single_word_cannot_crowd_out_another_subject(tmp_path):
    _,_,c,routes=setup(tmp_path);routes.remember(topic_plan(),[c])
    assert routes.search({'en':['concept']})==[]


def test_unavailable_optional_route_index_keeps_normal_authenticated_lookup(tmp_path):
    from pathlib import Path
    store,index,c,_=setup(tmp_path)
    Path(store.directory/'evidence_routes.db').write_bytes(b'invalid optional navigation database')
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    assert index.search(a,['ar'])[0].source_digest==c.source_digest


def test_expired_navigation_cannot_hide_a_live_route_at_the_result_limit(tmp_path):
    store,index,c,routes=setup(tmp_path)
    for n in range(4):
        a=topic_plan();a.keywords['en']+=['old '+str(n)]
        routes.remember(a,[c])
    with store.connect() as db:db.execute("UPDATE records SET expires=0 WHERE kind='evidence_route'")
    routes.remember(topic_plan(),[c])
    assert index.search(topic_plan(),['ar'])[0].source_digest==c.source_digest


def test_route_ttl_never_renews_the_original(tmp_path):
    store,index,_,routes=setup(tmp_path);c=original();index.put(c,ttl=30)
    routes.remember(topic_plan(),[c],ttl=3600)
    with store.connect() as db:
        route_expiry=db.execute("SELECT expires FROM records WHERE kind='evidence_route'").fetchone()[0]
        source_expiry=db.execute("SELECT expires FROM records WHERE kind='indexed_source'").fetchone()[0]
    assert route_expiry<=source_expiry


def test_authenticated_old_decision_is_only_discovery_and_invalid_decision_is_skipped(tmp_path):
    store,index,c,routes=setup(tmp_path)
    saved={'analysis':topic_plan().model_dump(mode='json'),'references':[['article',c.reference_id]],
        'digests':[c.source_digest],'versions':[{'text':'Never reuse this old answer.'}]}
    store.put('decision','old',saved,3600)
    store.put('decision','tampered',saved,3600)
    with store.connect() as db:db.execute("UPDATE records SET signature='fake' WHERE kind='decision' AND id='tampered'")
    report=routes.backfill()
    assert report=={'remembered':1,'skipped':1}
    assert index.search(topic_plan(),['ar'])[0].source_digest==c.source_digest


def test_local_complete_concepts_cover_distinct_aspects_before_loose_overlap(tmp_path):
    from src.core.official_sources import OfficialSources
    store,index,_,_=setup(tmp_path)
    for n,text in enumerate(['alpha only','alpha only','alpha only','alpha only','alpha beta','gamma delta']):
        c=original();c.reference_id='concept-'+str(n)
        c.translations={'ar':text};c.arabic_text=c.accredited_translation=text;c.passages['ar'][0].text=text
        c.source_digest=OfficialSources.digest(c);index.put(c)
    a=analysis();a.keywords={'en':['alpha beta','gamma delta'],'ar':[]}
    found=index.search(a,['ar'])
    assert {'concept-4','concept-5'}<=set(c.reference_id for c in found[:2])


def multipart_selection():
    chosen=selection()
    second=[c.model_copy(deep=True) for c in chosen.coverage]
    for c in second:c.part_id=1
    chosen.coverage+=second
    return chosen


@pytest.mark.parametrize('failure',['none','missing_part','invented_witness','failed_review'])
def test_multipart_combined_request_keeps_all_evidence_and_review_checks(failure):
    from src.core.schema import AnswerCoverage, CulturalPersona
    a=analysis();part=QuestionPart(question='Explain',keywords=a.keywords,
        requirements=[{'kind':'explanation','description':'Explain this source.'}])
    a.question_parts=[part,part.model_copy(deep=True)]
    selected=multipart_selection()
    if failure=='missing_part':selected.coverage=[c for c in selected.coverage if c.part_id==0]
    if failure=='invented_witness':selected.coverage[2].witnesses[0].source_text='Not a published sentence.'
    analyzer=AnalysisAgent(time.monotonic()+60)
    analyzer.request=Mock(return_value=SourceBoundDraft(selection=selected,draft=draft()))
    writer=CulturalLocalizerAgent(analyzer.deadline);writer.policy={**writer.policy,'explanation_attempts':1}
    assessment=review();assessment.coverage=[]
    for p in (0,1):
        for lang in ('en','ar'):
            assessment.coverage.append(AnswerCoverage(part_id=p,language=lang,segment_ids=[0],answered=True,
                reason='Supported explanation.',requirements=[{'requirement_id':0,'segment_ids':[0]}]))
    if failure=='failed_review':assessment.coverage[-1].answered=False
    writer.client.request=Mock(return_value=assessment)
    def run():
        citations,prepared=writer.select_and_draft(analyzer,PipelineInput(query='Explain both aspects'),
            a,[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
        return writer.localize(PipelineInput(query='Explain both aspects'),a,citations,['en','ar'],
            CulturalPersona.GENERAL_GLOBAL,prepared_draft=prepared)
    if failure=='none':
        assert run() and analyzer.request.call_count==writer.client.request.call_count==1
        assert analyzer.request.call_args.args[1]['writing']['max_explanation_words']==240
    else:
        with pytest.raises(SafetyStop):run()
        if failure!='failed_review':writer.client.request.assert_not_called()
