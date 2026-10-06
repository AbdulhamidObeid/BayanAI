"""Bayan web API: recoverable question jobs, explicit sharing, authenticated receipts."""
import asyncio
import hmac
import json
import os
import secrets
import threading
import time
import uuid
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from src.core.integrity import AuthenticatedStore, fingerprint
from src.core.schema import PipelineInput
from src.core.source_policy import load_policy, validate_source_url
from src.web.presentation import presentation_config, source_cards
from src.core.query_jobs import QueryJobs, execution_config

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
GLOBAL_CONFIG=json.loads((PROJECT_ROOT / 'configs/global_config.json').read_text(encoding='utf-8'))
POLICY=load_policy()
EXECUTION=execution_config()
app=FastAPI(title='Bayan-AI — منصة بَيَان',version='1.1.0')
static_path=PROJECT_ROOT / 'src/web/static'
if static_path.is_dir():
    app.mount('/static',StaticFiles(directory=str(static_path)),name='static')
templates=Jinja2Templates(directory=str(PROJECT_ROOT / 'src/web/templates'))
query_slots=None
term_lock=threading.RLock()
rate_hits=defaultdict(deque)

@app.on_event('startup')
async def start_source_refresh():
    global query_slots
    query_slots=asyncio.Semaphore(POLICY['max_concurrent_queries'])
    from src.core.source_refresh import refresh_sources
    app.state.source_refresh=asyncio.create_task(refresh_sources())
    app.state.job_workers=[asyncio.create_task(question_worker())
        for _ in range(POLICY['max_concurrent_queries'])]

@app.on_event('shutdown')
async def stop_source_refresh():
    workers=getattr(app.state,'job_workers',[])
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers,return_exceptions=True)
    task=getattr(app.state,'source_refresh',None)
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass

@app.middleware('http')
async def limit_requests(request,call_next):
    if request.method=='POST' or request.url.path=='/api/benchmarks':
        ip=request.client.host if request.client else 'local'
        key=(ip,request.url.path)
        hits=rate_hits[key]
        now=time.monotonic()
        while hits and now-hits[0]>60:
            hits.popleft()
        if len(hits)>=POLICY['requests_per_minute']:
            return JSONResponse(status_code=429,content={'error':'Request limit reached'})
        hits.append(now)
        if len(rate_hits)>4096:
            for old in list(rate_hits)[:2048]:
                rate_hits.pop(old,None)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

def get_pipeline():
    from src.core.orchestrator import run_pipeline
    return run_pipeline

@app.get('/',response_class=HTMLResponse)
async def index(request:Request):
    return templates.TemplateResponse(request=request,name='index.html',context={'request':request,'config':GLOBAL_CONFIG,'messages':POLICY['messages'],
        'presentation':presentation_config(),'execution':execution_config()})

class QueryRequest(PipelineInput):
    pass

@app.post('/api/query')
async def query_pipeline(req:QueryRequest):
    global query_slots
    if query_slots is None:
        query_slots=asyncio.Semaphore(POLICY['max_concurrent_queries'])
    slots=query_slots
    await slots.acquire()
    started=time.monotonic()
    task=asyncio.create_task(run_in_threadpool(get_pipeline(),req))
    # A cancelled response must not release capacity while its worker is still
    # running. Retain the slot until completion and never expose partial output.
    def release_worker(finished):
        slots.release()
        if not finished.cancelled():
            finished.exception()  # Observe failures even after disconnection.
    task.add_done_callback(release_worker)
    output=await asyncio.shield(task)
    if output.stop_reason in ('source_service_unavailable','deadline_exceeded'):
        labels=EXECUTION['messages'].get(req.target_language,EXECUTION['messages']['en'])
        return JSONResponse(status_code=503,content={'success':False,'code':output.stop_reason,
            'error':labels['retrying']})
    if output.answer_status in ('QUOTA_EXHAUSTED','SERVICE_UNAVAILABLE'):
        from src.core.model_router import QuotaLedger, routing_config
        routing=routing_config()
        labels=routing['messages'].get(req.target_language,routing['messages']['en'])
        return JSONResponse(status_code=403 if output.stop_reason=='model_access_denied' else 402 if output.stop_reason=='model_billing_unavailable' else (429 if output.answer_status=='QUOTA_EXHAUSTED' else 503),
            content={'success':False,'error':labels.get(output.stop_reason,routing['messages']['en'][output.stop_reason]),
                     'code':output.stop_reason,'retry_at':QuotaLedger().reset_at() if output.stop_reason=='quota_finished' else None})
    payload=output.model_dump(mode='json')
    payload.update(success=True,elapsed_ms=round((time.monotonic()-started)*1000,2),response_id=None)
    payload['labels']=POLICY['messages'][req.target_language]
    payload['source_cards']=source_cards(output)
    if req.share_response:
        response_id='BYN-'+secrets.token_hex(16).upper()
        record={'response_id':response_id,'output':output.model_dump(mode='json'),
                'generated_at':datetime.now(timezone.utc).isoformat()}
        AuthenticatedStore().put('receipt',response_id,record,POLICY['share_retention_seconds'])
        payload['response_id']=response_id
        payload['verification_qr_data']='/api/response/'+response_id
    return payload


@app.post('/api/query-job',status_code=202)
async def submit_question(req:QueryRequest, x_query_id:str=Header(...,pattern=r'^[a-f0-9]{64}$')):
    try:
        QueryJobs().submit(x_query_id,req.model_dump(mode='json'))
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {'accepted':True,'poll_seconds':EXECUTION['poll_seconds']}


@app.get('/api/query-job')
async def question_status(x_query_id:str=Header(...,pattern=r'^[a-f0-9]{64}$')):
    try:
        job=QueryJobs().get(x_query_id)
    except ValueError:
        raise HTTPException(409,'Stored request failed integrity verification')
    if job is None:
        raise HTTPException(404,'Question job not found or result retention expired')
    if job['status']=='completed':
        return {'status':'completed',**job['value']}
    return {'status':job['status'],'elapsed_ms':round((time.time()-job['created'])*1000),
            'poll_seconds':EXECUTION['poll_seconds']}


async def question_worker():
    store=QueryJobs()
    owner=secrets.token_hex(32)
    while True:
        try:
            job=store.claim(owner,POLICY['max_concurrent_queries'])
        except Exception:
            logging.getLogger(__name__).error('Question queue unavailable; worker will reconnect')
            await asyncio.sleep(EXECUTION['retry_max_seconds'])
            continue
        if job is None:
            await asyncio.sleep(EXECUTION['poll_seconds'])
            continue
        task=asyncio.create_task(query_pipeline(QueryRequest.model_validate(job['value']['request'])))
        try:
            # Polling is a heartbeat, not a generation deadline. Shield the
            # worker so an HTTP disconnect or renewal never cancels generation.
            while not task.done():
                await asyncio.wait({task},timeout=EXECUTION['heartbeat_seconds'])
                if not store.heartbeat(job['id'],owner):
                    # Another process recovered this job; this result cannot publish.
                    await asyncio.shield(task)
                    break
            response=task.result()
            status=response.status_code if isinstance(response,JSONResponse) else 200
            payload=json.loads(response.body) if isinstance(response,JSONResponse) else response
            reason=payload.get('code') or payload.get('stop_reason')
            if reason in ('model_rate_limited','model_service_unavailable','source_service_unavailable','deadline_exceeded','quota_finished'):
                delay=min(EXECUTION['retry_max_seconds'],
                    EXECUTION['retry_initial_seconds'] * 2**min(job['attempts']-1,10))
                if reason=='quota_finished':
                    from src.core.model_router import QuotaLedger
                    delay=max(delay,datetime.fromisoformat(QuotaLedger().reset_at()).timestamp()-time.time())
                store.retry(job['id'],owner,delay)
            else:
                payload['elapsed_ms']=round((time.time()-job['created'])*1000,2)
                store.finish(job['id'],owner,payload,status)
        except asyncio.CancelledError:
            # The lease remains owned until expiry on restart. Observe any
            # in-process completion without releasing its actual worker slot.
            task.add_done_callback(lambda done:done.exception() if not done.cancelled() else None)
            raise
        except Exception:
            # Infrastructure failures retain the original request for recovery.
            # Never store exception strings, which may contain private content.
            logging.getLogger(__name__).error('Question execution interrupted; retained for automatic recovery')
            try:
                store.retry(job['id'],owner,EXECUTION['retry_max_seconds'])
            except Exception:
                pass  # The persisted lease also recovers database interruptions.

def receipt(response_id):
    try:
        record=AuthenticatedStore().get('receipt',response_id.upper())
    except (ValueError,json.JSONDecodeError):
        raise HTTPException(409,'Stored receipt failed integrity verification')
    if not record:
        raise HTTPException(404,'No shared receipt found, or it has expired')
    from src.core.orchestrator import MasterOrchestrator
    from src.core.schema import PipelineOutput
    output=PipelineOutput.model_validate(record['output'])
    claimed=output.provenance_hash
    MasterOrchestrator.seal(output)
    if not claimed or not hmac.compare_digest(claimed,output.provenance_hash):
        raise HTTPException(409,'Content fingerprint does not match the signed receipt')
    return record

@app.get('/api/response/{response_id}')
async def get_response_by_id(response_id:str):
    record=receipt(response_id)
    return {**record['output'],'found':True,'response_id':record['response_id'],
            'generated_at':record['generated_at'],'verified':True,
            'verification_note':'Receipt signature and complete content fingerprint match. This confirms receipt integrity, not a theological guarantee.'}

class VerifyRequest(BaseModel):
    response_id:str=Field(default='',max_length=80)
    text:str=Field(max_length=100000)
    hash:str=Field(max_length=128)

@app.post('/api/verify')
async def verify_provenance(req:VerifyRequest):
    if not req.response_id:
        return {'match':False,'verified':False,'reason':'An issued shared receipt ID is required; self-computed hashes do not prove origin.'}
    record=receipt(req.response_id)
    output=record['output']
    match=(hmac.compare_digest((output['localized_text'] or '').encode(),req.text.encode())
           and hmac.compare_digest(output['provenance_hash'].encode(),req.hash.strip().lower().encode()))
    return {'match':match,'verified':match,'scope':'Receipt origin and full content integrity; no absolute theological guarantee.'}

_health_cache={'checked_at':0.0,'data':None}
_health_lock=threading.Lock()

def check_gemini_health(force:bool=False):
    now=time.monotonic()
    with _health_lock:
        if not force and _health_cache['data'] is not None and (now - _health_cache['checked_at'] < 45.0):
            return _health_cache['data']
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key or key in ('paste_your_key_here','your_api_key_here','your_key_here'):
        result={
            'status':'missing_key',
            'configured':False,
            'connected':False,
            'dot':'error',
            'system':'Bayan-AI',
            'message_en':'API Key Missing',
            'message_ar':'مفتاح API غير متوفر',
            'tooltip_en':'GEMINI_API_KEY is not configured in .env',
            'tooltip_ar':'مفتاح GEMINI_API_KEY غير مهيأ في ملف .env',
            'help_en':'Add your Google Gemini API key to the .env file (GEMINI_API_KEY=...) to enable AI reasoning and cultural localization.',
            'help_ar':'يرجى إضافة مفتاح Google Gemini في ملف .env (GEMINI_API_KEY=...) لتفعيل التحليل والتوطين الثقافي بالذكاء الاصطناعي.',
            'remote_connectivity':'unconfigured',
            'absolute_accuracy_guarantee':False,
            'privacy':EXECUTION['messages']['en']['privacy']
        }
        with _health_lock:
            _health_cache['checked_at']=now
            _health_cache['data']=result
        return result

    try:
        from google import genai
        client=genai.Client(api_key=key)
        pager=client.models.list(config={'page_size':1})
        next(iter(pager),None)
        result={
            'status':'configured',
            'configured':True,
            'connected':True,
            'dot':'active',
            'system':'Bayan-AI',
            'message_en':'System Active',
            'message_ar':'نظام نشط',
            'tooltip_en':'API Connected: Google Gemini models ready',
            'tooltip_ar':'تم الاتصال: نماذج Google Gemini جاهزة للعمل',
            'help_en':'All systems operational. Google Gemini API is authenticated and connected.',
            'help_ar':'النظام نشط بالكامل. تم التحقق من مفتاح Google Gemini والاتصال بنجاح.',
            'remote_connectivity':'connected',
            'absolute_accuracy_guarantee':False,
            'privacy':EXECUTION['messages']['en']['privacy']
        }
    except Exception as exc:
        err_str=str(exc).lower()
        if any(w in err_str for w in ('not valid','invalid_argument','api_key_invalid','400','401','403')):
            st='invalid_key'
            msg_en='Invalid API Key'
            msg_ar='مفتاح API غير صالح'
            tip_en='Upstream Google API rejected the key. Check .env'
            tip_ar='مفتاح Google API مرفوض. تحقق من ملف .env'
            hlp_en='The provided GEMINI_API_KEY in .env is invalid or rejected by Google.'
            hlp_ar='مفتاح GEMINI_API_KEY في ملف .env غير صالح أو غير معتمد من Google.'
            dot='error'
        elif any(w in err_str for w in ('resource_exhausted','429','quota')):
            st='quota_depleted'
            msg_en='Quota Depleted'
            msg_ar='الحصة مستنفدة'
            tip_en='Gemini API quota or rate limit reached'
            tip_ar='تم استنفاد حصة Gemini اليومية أو اللحظية'
            hlp_en='The API project has exceeded its quota or request rate limit.'
            hlp_ar='تم الوصول إلى حد الاستخدام أو استنفاد الحصة المؤقتة للمشروع.'
            dot='warning'
        else:
            st='degraded'
            msg_en='Service Offline'
            msg_ar='الخدمة غير متصلة'
            tip_en='Could not reach Google Gemini service'
            tip_ar='تعذر الاتصال بخدمة Google Gemini'
            hlp_en='Network connectivity or upstream service issue connecting to Google.'
            hlp_ar='تعذر الاتصال بالشبكة أو بخدمات Google الخارجية.'
            dot='error'

        result={
            'status':st,
            'configured':False,
            'connected':False,
            'dot':dot,
            'system':'Bayan-AI',
            'message_en':msg_en,
            'message_ar':msg_ar,
            'tooltip_en':tip_en,
            'tooltip_ar':tip_ar,
            'help_en':hlp_en,
            'help_ar':hlp_ar,
            'remote_connectivity':'error',
            'absolute_accuracy_guarantee':False,
            'privacy':EXECUTION['messages']['en']['privacy']
        }

    with _health_lock:
        _health_cache['checked_at']=now
        _health_cache['data']=result
    return result

@app.get('/api/health')
async def health(refresh:bool=False):
    return await run_in_threadpool(check_gemini_health, force=refresh)

@app.get('/api/benchmarks')
async def run_benchmarks():
    # No fabricated PASS results. Evaluation is read from an actual test-run artifact.
    report_path=PROJECT_ROOT / 'data/evaluation_report.json'
    if not report_path.exists():
        return {'success':False,'status':'NOT_RUN','results':[],
            'message':'No evaluation report has been produced. Run the documented test suite.'}
    return json.loads(report_path.read_text(encoding='utf-8'))

# Terminology submissions are private, authenticated review records, not automatically active doctrine.
PENDING_TERMS_PATH=PROJECT_ROOT / 'data/pending_terms.json'
TERMS_GUIDE_PATH=PROJECT_ROOT / 'configs/agents/terminology_preserver/islamic_terms_guide.md'

def _load_pending():
    return json.loads(PENDING_TERMS_PATH.read_text(encoding='utf-8')) if PENDING_TERMS_PATH.exists() else []

def _save_pending(terms):
    PENDING_TERMS_PATH.parent.mkdir(parents=True,exist_ok=True)
    temp=PENDING_TERMS_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(terms,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(PENDING_TERMS_PATH)

def require_admin(password):
    expected=os.getenv('ADMIN_PASSWORD','')
    if not expected or not password or not hmac.compare_digest(password.encode(),expected.encode()):
        raise HTTPException(403,'Admin access required')

@app.get('/terms',response_class=HTMLResponse)
async def terms_page(request:Request):
    return templates.TemplateResponse(request=request,name='terms.html',context={'request':request})

@app.get('/admin/terms',response_class=HTMLResponse)
async def admin_terms_page(request:Request):
    return templates.TemplateResponse(request=request,name='admin_terms.html',context={'request':request})

class AccessCheckRequest(BaseModel):
    code:str=Field(max_length=200)

@app.post('/api/terms/check-access')
async def check_access(req:AccessCheckRequest):
    expected=os.getenv('WORKER_ACCESS_CODE','')
    valid=bool(expected and req.code and hmac.compare_digest(req.code.encode(),expected.encode()))
    if not valid:
        return {'valid':False}
    token=secrets.token_urlsafe(32)
    AuthenticatedStore().put('worker',fingerprint(token),{'authorized':True},POLICY['worker_session_seconds'])
    return {'valid':True,'token':token}

class TermSubmission(BaseModel):
    worker_token:str=Field(default='',max_length=200)
    arabic_term:str=Field(min_length=1,max_length=100)
    forbidden_translations:str=Field(max_length=2000)
    canonical_form:str=Field(min_length=1,max_length=2000)
    scholarly_reference:str=Field(default='',max_length=2000)
    submitter_name:str=Field(default='',max_length=100)
    submitter_role:str=Field(default='',max_length=100)

@app.post('/api/terms/submit')
async def submit_term(data:TermSubmission):
    if not data.worker_token or not AuthenticatedStore().get('worker',fingerprint(data.worker_token)):
        raise HTTPException(403,'A valid worker session is required')
    if not data.arabic_term.strip() or not data.canonical_form.strip():
        raise HTTPException(400,'Required fields are blank')
    entry=data.model_dump(exclude={'worker_token'})
    entry.update(id=str(uuid.uuid4()),submitted_at=datetime.now(timezone.utc).isoformat(),status='pending',active=False)
    entry['forbidden_translations']=data.forbidden_translations.splitlines()
    with term_lock:
        terms=_load_pending()
        terms.append(entry)
        _save_pending(terms)
    return {'success':True,'id':entry['id'],'message':'Submitted for scholarly review'}

@app.get('/api/terms/list')
async def list_approved_terms():
    # The real active linter registry, rather than a markdown guide that the linter never reads.
    lexicon=json.loads((PROJECT_ROOT / 'configs/sharia_lexicon.json').read_text(encoding='utf-8'))
    terms=[{'arabic':t['arabic'],'forbidden':' | '.join(t['translations'].get('en',{}).get('forbidden_substitutes',[])),
        'canonical':t['translations'].get('en',{}).get('canonical','')} for t in lexicon['terms'].values()]
    return {'terms':terms,'count':len(terms)}

class AdminAction(BaseModel):
    admin_password:str=Field(max_length=200)
    term_id:str=Field(default='',max_length=100)

@app.post('/api/admin/terms/list')
async def admin_list_terms(req:AdminAction):
    require_admin(req.admin_password)
    with term_lock:
        terms=_load_pending()
    return {'pending':[t for t in terms if t.get('status')=='pending'],
            'approved':[t for t in terms if t.get('status')=='approved']}

@app.post('/api/admin/terms/approve')
async def approve_term(req:AdminAction):
    require_admin(req.admin_password)
    with term_lock:
        terms=_load_pending()
        target=next((t for t in terms if t['id']==req.term_id),None)
        if not target:
            raise HTTPException(404,'Term not found')
        if target.get('status')!='pending':
            raise HTTPException(409,'Term has already been reviewed')
        try:
            validate_source_url(target.get('scholarly_reference',''))
        except ValueError:
            raise HTTPException(400,'A direct accredited scholarly reference URL is required')
        target.update(status='approved',approved_at=datetime.now(timezone.utc).isoformat(),active=False)
        _save_pending(terms)
    return {'success':True,'active':False,'message':'Review recorded. A reviewed language rule must be added to the linter registry before activation.'}

@app.post('/api/admin/terms/reject')
async def reject_term(req:AdminAction):
    require_admin(req.admin_password)
    with term_lock:
        terms=_load_pending()
        target=next((t for t in terms if t['id']==req.term_id),None)
        if not target:
            raise HTTPException(404,'Term not found')
        if target.get('status')!='pending':
            raise HTTPException(409,'Term has already been reviewed')
        target['status']='rejected'
        _save_pending(terms)
    return {'success':True}
