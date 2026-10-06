"""Quota scheduling, provider switching and atomic no-partial-publication tests."""
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from src.core.analysis_agent import AnalysisAgent, SafetyStop
from src.core.model_router import QuotaLedger, RoutingStop, routing_config, models_for, error_category
from src.core.schema import QueryAnalysis, DiscoveryRanking, EvidenceSelection, ExplanationReview, ExplanationDraft, PipelineInput, QuestionPart
from tests.test_trust_pipeline import analysis, harness, citation


@pytest.fixture(autouse=True)
def private_ledger(monkeypatch, tmp_path):
    # Preserve generic multi-model scheduler regression scenarios with a test-only
    # historical config; production-only assertions live in test_single_model_routing.
    from pathlib import Path
    legacy=json.loads((Path(__file__).parent/'fixtures/legacy_model_routing.json').read_text())
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:copy.deepcopy(legacy))
    monkeypatch.setattr(__name__+'.routing_config',lambda:copy.deepcopy(legacy))
    monkeypatch.setenv('BAYAN_PRIVATE_STORE', str(tmp_path/'private'))
    monkeypatch.setenv('GEMINI_QUOTA_PROJECT', 'test-project')


def small_config():
    config=copy.deepcopy(routing_config())
    config['max_rate_wait_seconds']=0
    for model in config['models'].values():
        model.update(rpm=100, rpd=1)
    return config


def test_limits_and_supported_language_errors_are_configured():
    from src.core.source_policy import load_policy
    c=routing_config()
    assert sum(v['rpd'] for v in c['models'].values())==1100
    assert c['models']['gemini-3.1-flash-lite']=={'rpm':15,'rpd':500}
    assert set(c['messages'])==set(load_policy()['messages'])
    assert all('Quota finished' in m['quota_finished'] for m in c['messages'].values())


def test_task_routing_preserves_strong_models_for_harder_work():
    c=routing_config()
    assert models_for(QueryAnalysis,{},c)==c['pools']['routine']
    assert models_for(DiscoveryRanking,{'level':'LEVEL_C'},c)==c['pools']['routine']
    assert models_for(ExplanationReview,{},c)==c['pools']['review']
    assert models_for(EvidenceSelection,{'level':'LEVEL_C'},c)==c['pools']['strong']
    assert models_for(ExplanationDraft,{'level':'LEVEL_C'},c,repair=True)==c['pools']['strong']
    assert models_for(ExplanationReview,{'question_parts':[{}, {}, {}]},c)[0]=='gemini-3.8-flash'
    assert models_for(QueryAnalysis,{},c,repair=True)[0]=='gemini-3.8-flash'


def test_daily_limits_persist_and_switch_across_instances(tmp_path):
    c=small_config();pool=c['pools']['routine']
    a=QuotaLedger(tmp_path, c)
    assert a.acquire(pool,'classify',time.monotonic()+1)==pool[0]
    b=QuotaLedger(tmp_path, c)
    assert b.acquire(pool,'classify',time.monotonic()+1)==pool[1]
    assert b.acquire(pool,'classify',time.monotonic()+1)==pool[2]
    with pytest.raises(RoutingStop,match='quota_finished'):
        a.acquire(pool,'classify',time.monotonic()+1)


def test_concurrent_requests_cannot_overspend_a_single_daily_slot(tmp_path):
    c=small_config();model=c['pools']['routine'][0]
    QuotaLedger(tmp_path,c)
    def call(_):
        try:return QuotaLedger(tmp_path,c).acquire([model],'test',time.monotonic()+2)
        except RoutingStop:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(call,range(12)))
    assert results.count(model)==1
    assert QuotaLedger(tmp_path,c).snapshot()['models'][model]['used']==1


def test_minute_limit_switches_model_without_exhausting_daily_quota(tmp_path):
    c=small_config();pool=c['pools']['routine']
    c['models'][pool[0]].update(rpm=1,rpd=100)
    ledger=QuotaLedger(tmp_path,c,clock=lambda:1000000)
    assert ledger.acquire(pool,'test',time.monotonic()+1)==pool[0]
    assert ledger.acquire(pool,'test',time.monotonic()+1)==pool[1]
    with pytest.raises(RoutingStop,match='model_rate_limited'):
        ledger.acquire([pool[0]],'test',time.monotonic()+1)
    assert ledger.snapshot()['models'][pool[0]]['remaining']==99


def test_daily_reset_uses_pacific_midnight_and_handles_dst(tmp_path):
    c=small_config();model=c['pools']['routine'][0]
    stamp=[datetime(2026,10,5,6,59,59,tzinfo=timezone.utc).timestamp()]
    ledger=QuotaLedger(tmp_path,c,clock=lambda:stamp[0])
    ledger.mark_exhausted(model)
    assert ledger.reset_at()=='2026-10-05T07:00:00+00:00'
    stamp[0]+=1
    assert ledger.snapshot()['models'][model]['remaining']==1
    stamp[0]=datetime(2026,11,2,12,tzinfo=timezone.utc).timestamp()
    assert ledger.reset_at()=='2026-11-03T08:00:00+00:00'


class ProviderError(Exception):
    def __init__(self, message, code=429, details=None):
        super().__init__(message)
        self.code=code
        self.details=details


@pytest.mark.parametrize('error,kind',[
    (ProviderError('RESOURCE_EXHAUSTED',details=[{'quotaId':'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]),'daily'),
    (ProviderError('RESOURCE_EXHAUSTED',details=[{'quotaId':'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]),'rate'),
    (ProviderError('Quota exceeded'),'rate'),
    (ProviderError('TokensPerMinute exhausted'),'rate'),
    (ProviderError('model not found',404),'unavailable'),
    (ProviderError('response schema constraint is not supported',400),'service'),
    (ProviderError('model is not supported',400),'unavailable'),
    (ProviderError('Unavailable',503),'service')])
def test_provider_failure_types_do_not_confuse_daily_and_minute_limits(error,kind):
    assert error_category(error)[0]==kind


def test_retry_info_and_unavailable_models_are_obeyed(tmp_path):
    c=small_config();model=c['pools']['routine'][0]
    ledger=QuotaLedger(tmp_path,c,clock=lambda:1000000)
    ledger.failure(model,ProviderError('RESOURCE_EXHAUSTED',details=[{'retryDelay':'12s'}]))
    assert ledger.snapshot()['models'][model]['wait_seconds']==12
    assert ledger.snapshot()['models'][model]['remaining']==1
    ledger.failure(model,ProviderError('model not found',404))
    with pytest.raises(RoutingStop,match='model_service_unavailable'):
        ledger.acquire([model],'test',time.monotonic()+1)


def test_failure_diagnostics_persist_only_category_and_numeric_code(tmp_path):
    ledger=QuotaLedger(tmp_path);model=ledger.config['pools']['routine'][0]
    ledger.failure(model,ProviderError('secret-key and private-question',503))
    restored=QuotaLedger(tmp_path)
    with restored.connect() as db:
        rows=db.execute('SELECT model,kind,code FROM failures').fetchall()
    assert rows==[(model,'service',503)]
    assert b'secret-key' not in restored.path.read_bytes()
    assert b'private-question' not in restored.path.read_bytes()


def fake_provider(monkeypatch, outcomes):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    calls=[]
    def generate(**kwargs):
        calls.append(kwargs['model'])
        outcome=outcomes.pop(0)
        if isinstance(outcome,Exception):raise outcome
        return Mock(text=outcome.model_dump_json())
    client=Mock();client.models.generate_content.side_effect=generate
    monkeypatch.setattr(genai,'Client',Mock(return_value=client))
    return calls


def test_known_daily_exhaustion_skips_main_without_calling_it(monkeypatch):
    ledger=QuotaLedger();ledger.mark_exhausted('gemini-3.5-flash-lite')
    calls=fake_provider(monkeypatch,[DiscoveryRanking(candidate_ids=[0])])
    result=AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert result.candidate_ids==[0]
    assert calls==['gemini-3.1-flash-lite']


def test_provider_daily_error_switches_and_remembers_exhaustion(monkeypatch):
    calls=fake_provider(monkeypatch,[ProviderError('RequestsPerDay exhausted'),DiscoveryRanking(candidate_ids=[0])])
    AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==['gemini-3.5-flash-lite','gemini-3.1-flash-lite']
    assert QuotaLedger().snapshot()['models']['gemini-3.5-flash-lite']['remaining']==0


def test_all_provider_minute_limits_return_temporary_error_not_daily_exhaustion(monkeypatch):
    pool=routing_config()['pools']['routine']
    calls=fake_provider(monkeypatch,[ProviderError('RequestsPerMinute exhausted') for _ in pool])
    with pytest.raises(SafetyStop,match='model_rate_limited'):
        AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==pool
    assert all(QuotaLedger().snapshot()['models'][m]['remaining']>0 for m in pool)


def test_provider_daily_errors_exhaust_every_fallback_and_stop(monkeypatch):
    c=routing_config();order=c['pools']['routine']+c['pools']['strong']
    calls=fake_provider(monkeypatch,[ProviderError('RequestsPerDay exhausted') for _ in order])
    with pytest.raises(SafetyStop,match='quota_finished'):
        AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==order
    assert all(m['remaining']==0 for m in QuotaLedger().snapshot()['models'].values())


def test_routine_exhaustion_can_use_remaining_strong_emergency_pool(monkeypatch):
    ledger=QuotaLedger()
    for model in ledger.config['pools']['routine']:ledger.mark_exhausted(model)
    calls=fake_provider(monkeypatch,[DiscoveryRanking(candidate_ids=[0])])
    AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==['gemini-3.8-flash']


def test_mixed_daily_service_and_unavailable_failures_reach_flash(monkeypatch):
    c=routing_config();c['service_cooldown_seconds']=0
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:c)
    QuotaLedger().mark_exhausted('gemini-3.5-flash-lite')
    calls=fake_provider(monkeypatch,[ProviderError('Unavailable',503),
        ProviderError('model not found',404),ProviderError('Unavailable',503),DiscoveryRanking(candidate_ids=[0])])
    result=AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert result.candidate_ids==[0]
    assert calls==['gemini-3.1-flash-lite','gemini-2.5-flash-lite','gemini-3.1-flash-lite','gemini-3.8-flash']


def test_all_service_failures_retry_each_eligible_model_boundedly(monkeypatch):
    c=routing_config();c['service_cooldown_seconds']=0
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:c)
    order=c['pools']['routine']+c['pools']['strong']
    calls=fake_provider(monkeypatch,[ProviderError('Unavailable',503) for _ in order*2])
    with pytest.raises(SafetyStop,match='model_service_unavailable'):
        AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==c['pools']['routine']*2+c['pools']['strong']*2


def test_sensitive_work_never_silently_downgrades_when_flash_quota_finishes(monkeypatch):
    ledger=QuotaLedger()
    for model in ledger.config['pools']['strong']:ledger.mark_exhausted(model)
    calls=fake_provider(monkeypatch,[])
    with pytest.raises(SafetyStop,match='quota_finished'):
        AnalysisAgent(time.monotonic()+20).request('Review',{'level':'LEVEL_C'},ExplanationReview)
    assert calls==[]


@pytest.mark.parametrize('stage',['analysis','selection','writer'])
def test_mid_run_quota_failure_discards_every_output_field(stage):
    pipe, agent, sources=harness()
    if stage=='analysis':agent.analyze.side_effect=SafetyStop('quota_finished')
    if stage=='selection':agent.select.side_effect=SafetyStop('quota_finished')
    if stage=='writer':pipe.localizer_factory(0).localize.side_effect=SafetyStop('quota_finished')
    result=pipe.run_pipeline(PipelineInput(query='Explain a general topic'))
    assert result.answer_status=='QUOTA_EXHAUSTED'
    assert result.stop_reason=='quota_finished'
    assert not result.citations and result.citation is None
    assert not result.versions and not result.localized_text and not result.localized_content
    assert not result.source_verified and result.linter_status=='NOT_PUBLISHED'


def test_all_quotas_exhausted_stops_before_even_cached_retrieval():
    ledger=QuotaLedger()
    for model in ledger.config['models']:ledger.mark_exhausted(model)
    pipe, agent, sources=harness()
    agent.check_quota=AnalysisAgent(time.monotonic()+20).check_quota
    result=pipe.run_pipeline(PipelineInput(query='17:23'))
    assert result.answer_status=='QUOTA_EXHAUSTED'
    sources.retrieve.assert_not_called()
    agent.analyze.assert_not_called()


def test_quota_api_returns_only_error_and_never_shares_partial_results(monkeypatch):
    from src.web import app as web
    pipe, agent, _=harness(c=citation(('ar',)));agent.select.side_effect=SafetyStop('quota_finished')
    monkeypatch.setattr(web,'get_pipeline',lambda:pipe.run_pipeline)
    monkeypatch.setattr(web,'AuthenticatedStore',Mock(side_effect=AssertionError('No receipt should be created')))
    with TestClient(web.app) as client:
        response=client.post('/api/query',json={'query':'Explain topic','target_language':'ar','share_response':True})
    assert response.status_code==429
    body=response.json()
    assert body['code']=='quota_finished' and 'Quota finished' in body['error']
    assert body['success'] is False and body['retry_at']
    assert not any(k in body for k in ('citations','versions','localized_text','source_cards','response_id'))


def test_catalog_ranker_quota_cannot_be_swallowed_as_optional_failure(monkeypatch,tmp_path):
    from src.core.official_sources import OfficialSources
    from src.core.publication_reader import PublicationReader
    from src.core.integrity import AuthenticatedStore
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path/'source'))
    source.policy={**source.policy,'discovery_ranking_enabled':True}
    source.rank_local_index=Mock(return_value=[])
    source.call=Mock(return_value={'structuredContent':{'results':[{'id':'hadith:24:ar','title':'Observed',
        'url':'https://hadeethenc.com/ar/browse/hadith/24'}]}})
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    monkeypatch.setattr(AnalysisAgent,'request',Mock(side_effect=SafetyStop('quota_finished')))
    with pytest.raises(SafetyStop,match='quota_finished'):source.discover(analysis())


def test_broken_contract_escalates_but_is_never_published(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    expected=analysis();expected.question_parts=[QuestionPart(question='Explain',keywords=expected.keywords,
        requirements=[{'kind':'explanation','description':'Explain'}])]
    calls=[]
    def generate(**kwargs):
        calls.append(kwargs['model'])
        return Mock(text='{}' if len(calls)==1 else expected.model_dump_json())
    client=Mock();client.models.generate_content.side_effect=generate
    monkeypatch.setattr(genai,'Client',Mock(return_value=client))
    assert AnalysisAgent(time.monotonic()+20).request('Plan',{},QueryAnalysis)==expected
    assert calls==['gemini-3.5-flash-lite','gemini-3.8-flash']


def test_transient_failure_recovers_using_same_model_with_capacity(monkeypatch):
    c=routing_config();c['service_cooldown_seconds']=0
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:c)
    ledger=QuotaLedger()
    for m in c['models']:
        if m!='gemini-3.1-flash-lite':ledger.mark_exhausted(m)
    calls=fake_provider(monkeypatch,[ProviderError('Unavailable',503),DiscoveryRanking(candidate_ids=[0])])
    result=AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert result.candidate_ids==[0]
    assert calls==['gemini-3.1-flash-lite']*2
    assert QuotaLedger().snapshot()['models']['gemini-3.1-flash-lite']['used']==2


def test_expired_unavailable_exclusion_restores_capacity_without_resetting_usage(tmp_path):
    stamp=[1000000]
    c=small_config();model=c['pools']['routine'][0];c['models'][model]['rpd']=10
    ledger=QuotaLedger(tmp_path,c,clock=lambda:stamp[0])
    ledger.acquire([model],'test',time.monotonic()+1)
    ledger.failure(model,ProviderError('model not found',404))
    assert ledger.snapshot()['models'][model]['disabled'] is True
    stamp[0]+=c['unavailable_cooldown_seconds']+1
    assert ledger.acquire([model],'test',time.monotonic()+1)==model
    assert ledger.snapshot()['models'][model]['used']==2


@pytest.mark.parametrize('reason',['model_service_unavailable','model_rate_limited','deadline_exceeded'])
def test_optional_catalog_failure_keeps_observed_candidates(monkeypatch,tmp_path,reason):
    from src.core.official_sources import OfficialSources
    from src.core.publication_reader import PublicationReader
    from src.core.integrity import AuthenticatedStore
    source=OfficialSources(time.monotonic()+60,AuthenticatedStore(tmp_path/'source'))
    source.policy={**source.policy,'discovery_ranking_enabled':True}
    source.rank_local_index=Mock(return_value=[])
    source.call=Mock(return_value={'structuredContent':{'results':[{'id':'hadith:24:ar','title':'Observed',
        'url':'https://hadeethenc.com/ar/browse/hadith/24'}]}})
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    deadlines=[]
    def fail(self,*args):
        deadlines.append(self.deadline)
        raise SafetyStop(reason)
    monkeypatch.setattr(AnalysisAgent,'request',fail)
    assert ('hadith','24') in source.discover(analysis())
    assert 'catalog_ranking_unavailable' in source.unavailable
    assert max(deadlines)<source.deadline-30


def test_normal_pool_recovers_before_emergency_flash(monkeypatch):
    c=routing_config();c['service_cooldown_seconds']=0
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:c)
    ledger=QuotaLedger();ledger.mark_exhausted('gemini-3.5-flash-lite')
    ledger.failure('gemini-2.5-flash-lite',ProviderError('model not found',404))
    calls=fake_provider(monkeypatch,[ProviderError('Unavailable',503),DiscoveryRanking(candidate_ids=[0])])
    assert AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking).candidate_ids==[0]
    assert calls==['gemini-3.1-flash-lite']*2
    assert all(ledger.snapshot()['models'][m]['used']==0 for m in c['pools']['strong'])


def test_provider_minimum_timeout_does_not_prematurely_abort_remaining_budget(monkeypatch):
    from google import genai
    calls=fake_provider(monkeypatch,[DiscoveryRanking(candidate_ids=[0])])
    client=genai.Client.return_value
    result=AnalysisAgent(time.monotonic()+2).request('Rank',{},DiscoveryRanking)
    assert result.candidate_ids==[0] and len(calls)==1
    assert genai.Client.call_args.kwargs['http_options'].timeout==10000


@pytest.mark.parametrize('code',[402,429])
def test_prepaid_billing_failure_is_not_a_rate_or_daily_limit(code):
    assert error_category(ProviderError('RESOURCE_EXHAUSTED: Your prepayment credits are depleted.',code))[0]=='billing'


def test_billing_rejection_stops_project_retries_and_preserves_daily_capacity(monkeypatch):
    calls=fake_provider(monkeypatch,[ProviderError('Your prepayment credits are depleted.',402)])
    with pytest.raises(SafetyStop,match='model_billing_unavailable'):
        AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert calls==[routing_config()['pools']['routine'][0]]
    ledger=QuotaLedger()
    assert ledger.snapshot()['models'][calls[0]]['remaining']==499
    with pytest.raises(RoutingStop,match='model_billing_unavailable'):ledger.ensure_available()
    with pytest.raises(RoutingStop,match='model_billing_unavailable'):
        ledger.acquire(routing_config()['pools']['strong'],'review',time.monotonic()+1)


def test_billing_guard_expires_for_recovery_probe(tmp_path):
    stamp=[1000000];ledger=QuotaLedger(tmp_path,clock=lambda:stamp[0])
    model=ledger.config['pools']['routine'][0]
    ledger.failure(model,ProviderError('Your prepayment credits are depleted.',402))
    stamp[0]+=ledger.config['billing_probe_interval_seconds']+1
    ledger.ensure_available()
    assert ledger.acquire([model],'probe',time.monotonic()+1)==model


@pytest.mark.parametrize('language',['ar','en','fr'])
def test_billing_api_returns_specific_error_without_partial_receipt(monkeypatch,language):
    from src.web import app as web
    pipe,agent,_=harness();agent.analyze.side_effect=SafetyStop('model_billing_unavailable')
    monkeypatch.setattr(web,'get_pipeline',lambda:pipe.run_pipeline)
    with TestClient(web.app) as client:
        response=client.post('/api/query',json={'query':'Explain topic','target_language':language,'share_response':True})
    assert response.status_code==402
    assert response.json()['code']=='model_billing_unavailable'
    assert not any(k in response.json() for k in ('citations','versions','response_id'))


def test_project_access_denial_stops_without_switching_or_exhausting_quota(monkeypatch):
    calls=fake_provider(monkeypatch,[ProviderError('Your project has been denied access. Please contact support.',403)])
    with pytest.raises(SafetyStop,match='model_access_denied'):
        AnalysisAgent(time.monotonic()+20).request('Rank',{},DiscoveryRanking)
    assert len(calls)==1
    assert QuotaLedger().snapshot()['models'][calls[0]]['remaining']==499
    with pytest.raises(RoutingStop,match='model_access_denied'):QuotaLedger().ensure_available()


def test_access_denial_api_returns_specific_403(monkeypatch):
    from src.web import app as web
    pipe,agent,_=harness();agent.analyze.side_effect=SafetyStop('model_access_denied')
    monkeypatch.setattr(web,'get_pipeline',lambda:pipe.run_pipeline)
    with TestClient(web.app) as client:
        response=client.post('/api/query',json={'query':'Explain topic','target_language':'ar'})
    assert response.status_code==403 and response.json()['code']=='model_access_denied'
    assert 'citations' not in response.json()
