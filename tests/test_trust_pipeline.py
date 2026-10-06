"""Mandatory safety gates and receipt integrity; deliberately offline and deterministic."""
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from src.core.analysis_agent import AnalysisAgent, SafetyStop, personal_ruling
from src.core.integrity import AuthenticatedStore
from src.core.official_sources import OfficialSources
from src.core.orchestrator import MasterOrchestrator
from src.core.schema import (PipelineInput, QueryAnalysis, SourceCitation, SourceRepository,
    EvidenceSelection, ResponseLevel, CulturalPersona)
from src.core.source_policy import required_languages, validate_source_url
from src.agents.terminology_preserver import TerminologyPreserverAgent

@pytest.fixture(autouse=True)
def isolated(monkeypatch,tmp_path):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    monkeypatch.setenv('GEMINI_API_KEY','')

def analysis(level='LEVEL_B'):
    return QueryAnalysis(level=level,confidence=.99,knowledge_level='unknown',
        knowledge_reason='No explicit indication.',question_language='en',keywords={'en':['oneness'],'ar':['التوحيد']},
        intent='explain a concept',needs_clarification=False)

def citation(languages=('en','ar')):
    # Live publisher excerpt recorded through the official Quran API for 112:1.
    texts={'en':'Say, "He is Allāh, [who is] One,[2011]','ar':'قُلۡ هُوَ ٱللَّهُ أَحَدٌ'}
    c=SourceCitation(repository=SourceRepository.QURAN_ENC,reference_id='112:1',
        arabic_text=texts['ar'],accredited_translation=texts[languages[0]],source_language=languages[0],
        translations={l:texts[l] for l in languages},translation_urls={l:'https://quranenc.com/en/browse/english_saheeh/112/1' for l in languages},
        source_url='https://quranenc.com/en/browse/english_saheeh/112/1',retrieval_endpoint='https://quranenc.com/api/v1',
        retrieved_at='2026-10-04T21:00:00Z',link_checked_at='2026-10-04T21:00:00Z',content_kind='quran',provenance_verified=True)
    c.source_digest=OfficialSources.digest(c)
    return c

def harness(c=None,a=None):
    analyzer=Mock()
    analyzer.analyze.return_value=a or analysis()
    analyzer.select.side_effect=lambda inp,analysis,candidates:candidates
    sources=Mock()
    sources.unavailable=[]
    sources.retrieve.return_value=[c or citation()]
    from src.core.schema import AnswerVersion
    localizer=Mock()
    localizer.localize.side_effect=lambda inp,analysis,citations,languages,persona:[
        AnswerVersion(language=l,text='\n\n'.join(c.translations[l] for c in citations),
            source_text='\n\n'.join(c.translations[l] for c in citations),
            source_urls=[c.translation_urls[l] for c in citations],
            passages=[p for c in citations for p in c.passages.get(l,[])]) for l in languages]
    pipeline=MasterOrchestrator(lambda _:analyzer,lambda _:sources,localizer_factory=lambda _:localizer)
    return pipeline,analyzer,sources

@pytest.mark.parametrize('url',[
 'https://evil.example/quranenc.com/verse','https://quranenc.com.evil.example/verse',
 'https://quranenc.com@evil.example/verse','http://quranenc.com/verse',
 'https://quranenc.com:1234/verse','javascript:alert(1)','https://quranenc.com/'])
def test_exact_source_host_validation(url):
    with pytest.raises(ValueError):validate_source_url(url)

@pytest.mark.parametrize('q',[
 'Is my prayer accepted if I made a mistake in Surah Al-Fatiha?',
 'نسيت التشهد في صلاتي هل صلاتي صحيحة؟',
 'J’ai divorcé de ma femme, suis-je encore marié ?',
 'Ist meine Ehe gültig?', '¿Es válido mi matrimonio?',
 'Apakah nikah saya sah?', 'Evliliğim geçerli mi?',
 'کیا میری نماز درست ہے؟','Je, ndoa yangu ni halali?','我的婚姻有效吗？'])
def test_personal_ruling_multilingual(q):
    assert personal_ruling(q)
    pipe,a,s=harness()
    out=pipe.run_pipeline(PipelineInput(query=q))
    assert out.answer_status=='REFERRED' and out.response_level==ResponseLevel.LEVEL_D
    a.analyze.assert_not_called();s.retrieve.assert_not_called()
    assert not out.citations and out.linter_status=='CONTROL_NOTICE_CHECKED'

@pytest.mark.parametrize('q',['What does talaq mean?','ما معنى الفتوى؟','What is a fatwa?'])
def test_definition_is_not_a_personal_fatwa(q):
    assert not personal_ruling(q)

@pytest.mark.parametrize('stage',['analysis','retrieval','relevance','linter'])
def test_failed_stage_stops_every_downstream_stage(stage):
    pipe,a,s=harness()
    if stage=='analysis':a.analyze.side_effect=RuntimeError('private provider error')
    if stage=='retrieval':s.retrieve.side_effect=RuntimeError('private provider error')
    if stage=='relevance':a.select.side_effect=RuntimeError('private provider error')
    if stage=='linter':pipe.preserver_factory=Mock(side_effect=RuntimeError('private provider error'))
    out=pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    assert out.answer_status in ('ABSTAINED','BLOCKED')
    assert not out.citations and not out.source_verified and not out.is_hallucination_free
    assert 'private provider error' not in (out.localized_text or '')
    blocked=next(t for t in out.telemetry if t.status=='blocked')
    assert not any(t.agent_id=='provenance_verifier' for t in out.telemetry[out.telemetry.index(blocked)+1:])
    if stage=='analysis':s.retrieve.assert_not_called()
    if stage in ('analysis','retrieval'):a.select.assert_not_called()

@pytest.mark.parametrize('invalid',['provenance','digest','language'])
def test_unverified_or_incomplete_source_cannot_be_published(invalid):
    c=citation()
    if invalid=='provenance':c.provenance_verified=False
    if invalid=='digest':c.translations['en']='Tampered passage'
    if invalid=='language':del c.translations['ar']
    pipe,a,s=harness(c)
    out=pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    assert out.answer_status=='ABSTAINED' and not out.citations
    a.select.assert_not_called()

def test_exact_reference_is_not_matched_to_another_verse():
    c=citation();c.reference_id='112:2';c.source_digest=OfficialSources.digest(c)
    pipe,a,s=harness(c)
    out=pipe.run_pipeline(PipelineInput(query='112:1'))
    assert out.answer_status=='ABSTAINED'

def test_answer_is_exact_source_and_english_then_arabic():
    c=citation();pipe,a,s=harness(c)
    out=pipe.run_pipeline(PipelineInput(query='What is this concept?',cultural_context='european'))
    assert out.answer_status=='ANSWERED' and out.source_verified
    assert out.localized_text==c.translations['en']
    assert [v.language for v in out.versions]==['en','ar']
    assert out.versions[1].text==c.arabic_text
    assert out.cultural_persona==CulturalPersona.EUROPEAN and out.knowledge_level.value=='unknown'

def test_arabic_has_no_english_appendix():
    pipe,a,s=harness(citation(('ar',)))
    out=pipe.run_pipeline(PipelineInput(query='112:1',target_language='ar'))
    assert out.answer_status=='ANSWERED'
    assert [v.language for v in out.versions]==['ar']
    assert out.localized_text==citation().arabic_text

def test_other_language_order_and_no_silent_english_substitution():
    assert required_languages('fr')==['fr','ar','en']
    pipe,a,s=harness()
    out=pipe.run_pipeline(PipelineInput(query='Explain this concept',target_language='fr'))
    assert out.answer_status=='ABSTAINED'
    assert [v.language for v in out.versions]==['fr','ar','en']
    assert out.localized_text==pipe.config['messages']['fr']['verification_failed']
    assert out.stop_reason=='unverified_source_contract'

def test_uncertain_analysis_never_retrieves():
    item=analysis();item.confidence=.5
    pipe,a,s=harness(a=item)
    assert pipe.run_pipeline(PipelineInput(query='An ambiguous query')).answer_status=='ABSTAINED'
    s.retrieve.assert_not_called()

@pytest.mark.parametrize('decisions',[
 [],[{'candidate_id':0,'direct_answer':True,'confidence':.99,'reason':'x'}]*2,
 [{'candidate_id':9,'direct_answer':True,'confidence':.99,'reason':'x'}]])
def test_incomplete_relevance_contract_rejected(decisions):
    agent=AnalysisAgent(time.monotonic()+10)
    agent.request=Mock(return_value=EvidenceSelection(decisions=decisions,fully_answers_question=True,explains_disagreement=True))
    with pytest.raises(SafetyStop):agent.select(PipelineInput(query='What is this concept?'),analysis(),[citation()])

def test_tangential_relevance_is_not_an_answer():
    agent=AnalysisAgent(time.monotonic()+10)
    agent.request=Mock(return_value=EvidenceSelection(decisions=[{'candidate_id':0,'direct_answer':False,'confidence':1,'reason':'Only background.'}],fully_answers_question=True,explains_disagreement=False))
    with pytest.raises(SafetyStop):agent.select(PipelineInput(query='What is this concept?'),analysis(),[citation()])

def test_level_c_requires_specific_scholarly_qualification():
    agent=AnalysisAgent(time.monotonic()+10)
    agent.request=Mock(return_value=EvidenceSelection(decisions=[{'candidate_id':0,'direct_answer':True,'confidence':1,'reason':'x'}],fully_answers_question=True,explains_disagreement=False))
    with pytest.raises(SafetyStop):agent.select(PipelineInput(query='Music dispute'),analysis('LEVEL_C'),[citation()])

@pytest.mark.parametrize('text',['Zakah is a tax.','Jihad is holy war.'])
def test_distorted_published_text_is_blocked_and_never_rewritten(text):
    cleaned,audit=TerminologyPreserverAgent().audit_published_text(text,'en')
    assert cleaned==text and audit['status']=='FLAGGED_FOR_HUMAN_REVIEW'

def test_rejecting_a_mistranslation_is_preserved():
    text='Holy war is an incorrect translation of Jihad.'
    cleaned,audit=TerminologyPreserverAgent().audit_published_text(text,'en')
    assert cleaned==text and audit['status']=='APPROVED_SAFE'

def test_partial_correction_cannot_restore_false_safe_status():
    c=citation();c.translations['en']='Zakah is a tax. Holy war means Jihad.';c.source_digest=OfficialSources.digest(c)
    pipe,a,s=harness(c)
    out=pipe.run_pipeline(PipelineInput(query='Explain these terms'))
    assert out.answer_status=='ABSTAINED' and not out.citations

def test_authenticated_store_detects_edits_and_restarted_instance(tmp_path):
    store=AuthenticatedStore(tmp_path/'store');store.put('test','id',{'text':'original'},60)
    assert AuthenticatedStore(tmp_path/'store').get('test','id')=={'text':'original'}
    with store.connect() as db:db.execute('UPDATE records SET payload=?',('{"text":"forged"}',))
    with pytest.raises(ValueError):store.get('test','id')

def test_receipt_binds_text_citations_versions_and_metadata(monkeypatch):
    import src.web.app as web
    pipe,a,s=harness()
    monkeypatch.setattr(web,'get_pipeline',lambda:pipe.run_pipeline)
    with TestClient(web.app) as client:
        plain=client.post('/api/query',json={'query':'What is this concept?'}).json()
        assert plain['response_id'] is None
        payload=client.post('/api/query',json={'query':'What is this concept?','share_response':True}).json()
        rid=payload['response_id']
        assert client.get('/api/response/'+rid).json()['verified']
        assert client.post('/api/verify',json={'response_id':rid,'text':payload['localized_text'],'hash':payload['provenance_hash']}).json()['match']
        assert not client.post('/api/verify',json={'text':'made up','hash':'a'*64}).json()['match']
        assert not client.post('/api/verify',json={'response_id':rid,'text':'forged','hash':payload['provenance_hash']}).json()['match']
        invalid_hash=client.post('/api/verify',json={'response_id':rid,'text':payload['localized_text'],'hash':'غير صحيح'})
        assert invalid_hash.status_code==200 and not invalid_hash.json()['match']
        store=AuthenticatedStore()
        with store.connect() as db:
            row=db.execute("SELECT payload FROM records WHERE kind='receipt' AND id=?",(rid,)).fetchone()
            record=json.loads(row[0]);record['output']['citations'][0]['arabic_text']='forged'
            db.execute("UPDATE records SET payload=? WHERE kind='receipt' AND id=?",(json.dumps(record),rid))
        assert client.get('/api/response/'+rid).status_code==409

def test_worker_credentials_enforced_by_server(monkeypatch):
    import src.web.app as web
    monkeypatch.setenv('WORKER_ACCESS_CODE','test-worker-secret')
    with TestClient(web.app) as client:
        body={'arabic_term':'مصطلح','forbidden_translations':'x','canonical_form':'y'}
        assert client.post('/api/terms/submit',json=body).status_code==403
        assert not client.post('/api/terms/check-access',json={'code':'ENV:WORKER_ACCESS_CODE'}).json()['valid']
        token=client.post('/api/terms/check-access',json={'code':'test-worker-secret'}).json()['token']
        assert AuthenticatedStore().get('worker',__import__('src.core.integrity',fromlist=['fingerprint']).fingerprint(token))

def test_no_admin_fallback_when_env_is_missing(monkeypatch):
    import src.web.app as web
    monkeypatch.delenv('ADMIN_PASSWORD',raising=False)
    with TestClient(web.app) as client:
        assert client.post('/api/admin/terms/list',json={'admin_password':'ENV:ADMIN_PASSWORD'}).status_code==403

def test_invalid_inputs_rejected_before_pipeline(monkeypatch):
    import src.web.app as web
    run=Mock();monkeypatch.setattr(web,'get_pipeline',lambda:run)
    with TestClient(web.app) as client:
        for payload in [{'query':' '},{'query':'x'*5001},{'query':'x','target_language':'../../en'}]:
            assert client.post('/api/query',json=payload).status_code==422
    run.assert_not_called()

@pytest.mark.parametrize('text',[
    'Zakah is not optional; it is a tax.',
    'Holy war is not obligatory; Jihad is holy war.',
    'Zakah is not a tax, but it is just a donation.'])
def test_unrelated_negation_cannot_hide_a_distortion(text):
    _,report=TerminologyPreserverAgent().audit_published_text(text,'en')
    assert report['status']=='FLAGGED_FOR_HUMAN_REVIEW'


def test_simultaneous_store_initialization_uses_one_complete_key(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys=list(pool.map(lambda _:AuthenticatedStore(tmp_path/'concurrent').key,range(16)))
    assert len(set(keys))==1 and len(keys[0])==32


def test_real_publisher_quran_parsing_and_language_metadata(monkeypatch,tmp_path):
    fixture=Path(__file__).parent/'fixtures'
    def get(url,**kwargs):
        mapping={
            'translations/list/en':'quran_english_catalog.json',
            'translations/list/fr':'quran_french_catalog.json',
            'translation/aya/english_saheeh/112/1':'quran_112_1_en.json',
            'translation/aya/french_rashid/112/1':'quran_112_1_fr.json',
        }
        match=next(name for suffix,name in mapping.items() if url.endswith(suffix))
        response=Mock();response.json.return_value=json.loads((fixture/match).read_text())
        return response
    monkeypatch.setattr('src.core.official_sources.requests.get',get)
    source=OfficialSources(time.monotonic()+30,AuthenticatedStore(tmp_path/'parser'))
    c=source.quran('112:1',['fr','ar','en'])
    assert c.translations['fr']==json.loads((fixture/'quran_112_1_fr.json').read_text())['result']['translation']
    assert '/fr/browse/french_rashid/112/1' in c.source_url
    assert c.translations['fr']!=c.translations['en']
    assert c.passages['en'][0].kind=='quran' and c.passages['en'][1].kind=='commentary'
    assert c.arabic_text==json.loads((fixture/'quran_112_1_en.json').read_text())['result']['arabic_text']


def test_catalog_returning_wrong_language_is_rejected(monkeypatch,tmp_path):
    response=Mock();response.json.return_value={'translations':[{'key':'english_saheeh','language_iso_code':'en'}]}
    monkeypatch.setattr('src.core.official_sources.requests.get',Mock(return_value=response))
    source=OfficialSources(time.monotonic()+30,AuthenticatedStore(tmp_path/'catalog'))
    assert source.quran('112:1',['fr','ar','en']) is None


def test_real_hadith_missing_language_and_exact_explanation(tmp_path):
    fixture=Path(__file__).parent/'fixtures/hadith_2960_en_ar_missing_fr.json'
    result=json.loads(fixture.read_text())['result']
    source=OfficialSources(time.monotonic()+30,AuthenticatedStore(tmp_path/'hadith'))
    source.call=Mock(return_value=result)
    assert source.hadith('2960',['fr','ar','en']) is None
    c=source.hadith('2960',['en','ar'])
    assert c and c.scholarly_grading=='Authentic'
    raw=source.text(result)
    assert c.passages['en'][0].text in raw
    assert c.passages['ar'][0].text in raw
    assert c.passages['en'][1].kind=='commentary'
    assert 'Benefits:' not in c.translations['ar']


def test_cached_successful_decision_is_bound_to_source_content():
    pipe,a,s=harness()
    first=pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    assert first.answer_status=='ANSWERED'
    a.analyze.side_effect=RuntimeError('offline')
    a.select.side_effect=RuntimeError('offline')
    pipe.localizer_factory=lambda _: (_ for _ in ()).throw(AssertionError('Previously reviewed explanation should be reused'))
    second=pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    assert second.answer_status=='ANSWERED'
    c=citation();c.translations['en']='Changed published text';c.accredited_translation=c.translations['en'];c.source_digest=OfficialSources.digest(c)
    s.retrieve.return_value=[c]
    changed=pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    assert changed.answer_status=='ABSTAINED'


def test_reviewed_generated_translation_is_not_rewritten_after_approval():
    from src.core.schema import SourcePassage
    pipe,a,s=harness();writer=pipe.localizer_factory(0)
    inp=PipelineInput(query='Explain a concept')
    versions=writer.localize(inp,analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    translation=SourcePassage(kind='commentary',text='Zakah is a tax.',source_url=citation().source_url,reference_id=citation().reference_id)
    versions[0].translated_passages={'0':[translation]}
    writer.localize.side_effect=None;writer.localize.return_value=versions
    output=pipe.run_pipeline(inp)
    assert output.answer_status=='ABSTAINED' and output.stop_reason=='terminology_review_required'
    assert translation.text=='Zakah is a tax.'
