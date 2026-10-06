"""Completion, retries, privacy and restart recovery for durable questions."""
import asyncio
import threading
import time
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from src.core.query_jobs import QueryJobs
from src.core.schema import PipelineInput, PipelineOutput, QuestionPart


def request():
    return PipelineInput(query='Explain this concept',target_language='ar').model_dump(mode='json')


def test_idempotency_and_private_token():
    jobs=QueryJobs();token='a'*64
    key=jobs.submit(token,request())
    assert jobs.submit(token,request())==key and key!=token
    with pytest.raises(ValueError):jobs.submit(token,{**request(),'query':'different'})
    assert jobs.get('b'*64) is None


def test_restart_reclaims_only_abandoned_work_and_rejects_stale_completion():
    jobs=QueryJobs();token='a'*64;key=jobs.submit(token,request())
    assert jobs.claim('first',1)['id']==key
    restarted=QueryJobs()
    assert restarted.claim('second',1) is None
    with restarted.connect() as db:db.execute('UPDATE jobs SET lease=0')
    assert restarted.claim('second',1)['id']==key
    assert not jobs.finish(key,'first',{'text':'stale'},200)
    assert restarted.finish(key,'second',{'text':'complete'},200)
    saved=QueryJobs().get(token)
    assert saved['value']['result']=={'text':'complete'}
    assert 'request' not in saved['value']


def test_cleanup_expires_completed_results_only():
    jobs=QueryJobs();key=jobs.submit('a'*64,request())
    jobs.submit('b'*64,request())
    jobs.claim('worker',1);jobs.finish(key,'worker',{'done':True},200)
    with jobs.connect() as db:db.execute("UPDATE jobs SET expires=0 WHERE status='completed'")
    assert jobs.get('a'*64) is None
    assert jobs.claim('next',1)
    with jobs.connect() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


def test_tampered_request_is_not_executed():
    jobs=QueryJobs();jobs.submit('a'*64,request())
    with jobs.connect() as db:db.execute("UPDATE jobs SET payload='{}'")
    assert jobs.claim('worker',1) is None
    with pytest.raises(ValueError,match='integrity'):jobs.get('a'*64)
    second=jobs.submit('b'*64,request())
    assert jobs.claim('worker',1)['id']==second


def wait_complete(client,headers):
    for _ in range(300):
        response=client.get('/api/query-job',headers=headers)
        assert response.status_code==200
        if response.json()['status']=='completed':return response.json()
        time.sleep(.01)
    pytest.fail('Mock worker did not finish')


@pytest.fixture
def fast_jobs(monkeypatch):
    from src.web import app as web
    monkeypatch.setitem(web.EXECUTION,'poll_seconds',.005)
    monkeypatch.setitem(web.EXECUTION,'heartbeat_seconds',.01)
    monkeypatch.setitem(web.EXECUTION,'retry_initial_seconds',.005)
    monkeypatch.setitem(web.POLICY,'max_concurrent_queries',1)
    return web


def test_slow_job_reconnects_without_duplicate_generation_or_partial_results(fast_jobs,monkeypatch):
    web=fast_jobs
    started=threading.Event();finish=threading.Event()
    def run(req):
        started.set();assert finish.wait(3)
        return PipelineOutput(query=req.query,target_language=req.target_language)
    pipeline=Mock(side_effect=run);monkeypatch.setattr(web,'get_pipeline',lambda:pipeline)
    headers={'X-Query-ID':'a'*64}
    with TestClient(web.app) as client:
        assert client.post('/api/query-job',headers=headers,json=request()).status_code==202
        assert started.wait(2)
        # Lost POST response / reconnect retries reuse the exact same job.
        assert client.post('/api/query-job',headers=headers,json=request()).status_code==202
        pending=client.get('/api/query-job',headers=headers).json()
        assert pending['status']=='running' and 'result' not in pending
        assert client.get('/api/query-job',headers={'X-Query-ID':'b'*64}).status_code==404
        finish.set()
        result=wait_complete(client,headers)
        assert result['result']['success'] and pipeline.call_count==1
        assert wait_complete(client,headers)==result


@pytest.mark.parametrize('reason',['model_service_unavailable','source_service_unavailable'])
def test_transient_provider_failure_retries_without_resubmission(fast_jobs,monkeypatch,reason):
    web=fast_jobs
    pipeline=Mock(side_effect=[
        PipelineOutput(query='x',target_language='ar',answer_status='SERVICE_UNAVAILABLE' if reason.startswith('model_') else 'ABSTAINED',stop_reason=reason),
        PipelineOutput(query='x',target_language='ar',answer_status='SYSTEM_NOTICE')])
    monkeypatch.setattr(web,'get_pipeline',lambda:pipeline)
    headers={'X-Query-ID':'c'*64}
    with TestClient(web.app) as client:
        client.post('/api/query-job',headers=headers,json=request())
        result=wait_complete(client,headers)
    assert result['status_code']==200 and pipeline.call_count==2


def test_restart_worker_resumes_saved_queue(fast_jobs,monkeypatch):
    jobs=QueryJobs();jobs.submit('d'*64,request())
    monkeypatch.setattr(fast_jobs,'get_pipeline',lambda:lambda req:PipelineOutput(query=req.query,target_language=req.target_language))
    with TestClient(fast_jobs.app) as client:
        assert wait_complete(client,{'X-Query-ID':'d'*64})['result']['success']


def test_multipart_selection_accepts_unlimited_deadline():
    from src.core.analysis_agent import AnalysisAgent
    from tests.test_trust_pipeline import analysis,citation
    agent=AnalysisAgent(float('inf'));plan=analysis()
    plan.question_parts=[QuestionPart(question=q,keywords=plan.keywords) for q in ('first','second')]
    agent.select_part=Mock(return_value=[citation()])
    assert agent.select(PipelineInput(query='two parts'),plan,[citation()])==[citation()]


def test_queue_capacity_and_retry_release_workers():
    jobs=QueryJobs();first=jobs.submit('a'*64,request());second=jobs.submit('b'*64,request())
    assert jobs.claim('one',1)['id']==first
    assert jobs.claim('two',1) is None
    jobs.retry(first,'one',60)
    assert jobs.claim('two',1)['id']==second
    assert jobs.get('a'*64)['status']=='retrying'


def test_disconnected_legacy_request_keeps_actual_worker_capacity(fast_jobs,monkeypatch):
    started=threading.Event();finish=threading.Event()
    def run(req):
        started.set();finish.wait(2)
        return PipelineOutput(query=req.query,target_language=req.target_language)
    monkeypatch.setattr(fast_jobs,'get_pipeline',lambda:run)
    async def exercise():
        monkeypatch.setattr(fast_jobs,'query_slots',asyncio.Semaphore(1))
        task=asyncio.create_task(fast_jobs.query_pipeline(fast_jobs.QueryRequest(**request())))
        while not started.is_set():await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert fast_jobs.query_slots.locked()
        finish.set()
        for _ in range(100):
            if not fast_jobs.query_slots.locked():break
            await asyncio.sleep(.005)
        assert not fast_jobs.query_slots.locked()
    asyncio.run(exercise())


@pytest.mark.parametrize('reason',['model_access_denied','model_billing_unavailable'])
def test_action_required_provider_errors_are_visible(fast_jobs,monkeypatch,reason):
    pipeline=Mock(return_value=PipelineOutput(query='x',target_language='ar',
        answer_status='SERVICE_UNAVAILABLE',stop_reason=reason))
    monkeypatch.setattr(fast_jobs,'get_pipeline',lambda:pipeline)
    headers={'X-Query-ID':'e'*64}
    with TestClient(fast_jobs.app) as client:
        assert client.post('/api/query-job',headers=headers,json=request()).status_code==202
        result=wait_complete(client,headers)
    assert result['result']['code']==reason and result['status_code'] in (402,403)
    assert pipeline.call_count==1


def test_slow_original_fetch_finishes_without_shared_network_cutoff(monkeypatch):
    from src.core.official_sources import OfficialSources
    from tests.test_trust_pipeline import analysis,citation
    source=OfficialSources(float('inf'))
    monkeypatch.setitem(source.policy['local_source_index'],'remote_budget_seconds',.001)
    def fetch(*args):
        time.sleep(.02)
        return citation()
    source.get=fetch
    assert source.retrieve(analysis(),['en','ar'],explicit=[('quran','112:1')])==[citation()]


def test_source_connection_failure_remains_retryable():
    import requests
    from src.core.official_sources import OfficialSources
    from src.core.analysis_agent import SafetyStop
    from tests.test_trust_pipeline import analysis
    source=OfficialSources(float('inf'))
    source.get=Mock(side_effect=requests.Timeout())
    with pytest.raises(SafetyStop,match='source_service_unavailable'):
        source.retrieve(analysis(),['ar'],explicit=[('quran','112:1')])
