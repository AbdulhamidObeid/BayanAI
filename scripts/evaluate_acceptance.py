"""Reproducible isolated acceptance evidence, without changing locked assertions.

Source law: docs/guides/10_immutability_constitution_and_source_lock.md.
Live mode spends provider quota. Outputs contain synthetic benchmark questions
and published passages; keep the raw output directory private.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def snapshot_database(source, destination):
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def isolated_checkout(root, warm_sources):
    clone=Path(tempfile.mkdtemp(prefix='bayan-evaluation-'))
    for directory in ('src', 'configs', 'scripts', 'tests', 'data'):
        shutil.copytree(root/directory, clone/directory,
            ignore=shutil.ignore_patterns('__pycache__', 'private', 'source_documents',
                'evaluation_runs', 'response_store.json', 'pending_terms.json', 'query_jobs.db*'))
    store=clone/'private'
    store.mkdir(mode=0o700)
    if warm_sources:
        policy=json.loads((root/'configs/trust_policy.json').read_text())
        original=Path(os.getenv('BAYAN_PRIVATE_STORE') or root/policy['private_store']).resolve()
        if (original/'integrity.key').exists():
            shutil.copyfile(original/'integrity.key', store/'integrity.key')
            os.chmod(store/'integrity.key', 0o600)
            for name in ('source_index.db', 'evidence_routes.db', 'model_quota.db'):
                if (original/name).exists():
                    snapshot_database(original/name, store/name)
            if (original/'records.db').exists():
                # Copy only authenticated published-source/navigation records.
                # Decisions, user answers, receipts and worker tokens stay out.
                with sqlite3.connect((original/'records.db').as_uri()+'?mode=ro', uri=True) as src:
                    rows=src.execute("SELECT * FROM records WHERE kind IN ('source','indexed_source',"
                        "'evidence_route','catalog','remote_catalog','search_catalog',"
                        "'publication_html','publication_document','dictionary_catalog')").fetchall()
                with sqlite3.connect(store/'records.db') as dst:
                    dst.execute('CREATE TABLE records(kind TEXT,id TEXT,payload TEXT NOT NULL,signature TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(kind,id))')
                    dst.executemany('INSERT INTO records VALUES(?,?,?,?,?)', rows)
    return clone,store


def worker(request, output):
    from src.core.analysis_agent import AnalysisAgent
    from src.agents.terminology_preserver import TerminologyPreserverAgent
    from src.core.orchestrator import run_pipeline
    from src.core.schema import PipelineInput
    original=AnalysisAgent.request
    original_audit=TerminologyPreserverAgent.audit_published_text
    stages=[]
    audits=[]
    def audit(self,text,language,found_terms=None):
        result=original_audit(self,text,language,found_terms)
        audits.append({'text':text,'audit':result[1]})
        write_json(output.with_suffix('.audits.json'),audits)
        return result
    TerminologyPreserverAgent.audit_published_text=audit
    def record(self,instruction,payload,contract):
        started=time.monotonic()
        try:
            result=original(self,instruction,payload,contract)
            stages.append({'contract':contract.__name__,'status':'returned',
                'elapsed_seconds':round(time.monotonic()-started,3),
                'result':result.model_dump(mode='json')})
            return result
        except Exception as exc:
            stages.append({'contract':contract.__name__,'status':'failed',
                'elapsed_seconds':round(time.monotonic()-started,3),'exception_type':type(exc).__name__})
            raise
        finally:
            # Responses are diagnostic proposals, not approved published prose.
            # Do not retain API keys, instructions or provider error messages.
            write_json(output.with_suffix('.stages.json'),stages)
    AnalysisAgent.request=record
    result=run_pipeline(PipelineInput.model_validate(json.loads(request.read_text())))
    write_json(output,result.model_dump(mode='json'))


def live_matrix(clone, destination, env, timeout, selected_categories=None):
    sys.path.insert(0,str(clone))
    matrix=importlib.import_module('tests.test_official_bathel_benchmarks')
    if selected_categories:
        unknown=set(selected_categories)-{name for name,cls in vars(matrix).items()
            if isinstance(cls,type) and name.startswith('Test')}
        if unknown:
            raise ValueError('Unknown benchmark category: '+', '.join(sorted(unknown)))
    from src.core.schema import PipelineInput, PipelineOutput
    captured={}
    rows=[]
    category_queries={}
    active=[None]

    def run(query,target_language='en',cultural_context='general'):
        payload=PipelineInput(query=query,target_language=target_language,cultural_context=cultural_context)
        key=json.dumps(payload.model_dump(mode='json'),sort_keys=True)
        category_queries.setdefault(active[0],set()).add(key)
        if key not in captured:
            number=len(captured)+1
            req=destination/f'query-{number:02}.json'
            answer=destination/f'answer-{number:02}.json'
            write_json(req,payload.model_dump(mode='json'))
            started=time.monotonic()
            with (destination/f'worker-{number:02}.log').open('w') as log:
                try:
                    p=subprocess.run([sys.executable,str(clone/'scripts/evaluate_acceptance.py'),
                        '--worker',str(req),str(answer)],cwd=clone,env=env,
                        stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
                    state='completed' if p.returncode==0 and answer.exists() else 'worker_error'
                    write_json(destination/f'worker-{number:02}.execution.json',{'returncode':p.returncode,'answer_file_exists':answer.exists()})
                except subprocess.TimeoutExpired:
                    state='evaluation_timeout'
                except Exception:
                    state='worker_error'
            output=None
            if state=='completed':
                try:
                    output=PipelineOutput.model_validate(json.loads(answer.read_text()))
                except Exception:
                    state='invalid_worker_output'
            captured[key]={'request_id':number,'elapsed_seconds':round(time.monotonic()-started,2),
                'execution':state,'output':output}
            status=output.answer_status if output else state
            reason=output.stop_reason if output else state
            print(f'Query {number:02}: {status} / {reason} / {captured[key]["elapsed_seconds"]}s',flush=True)
        result=captured[key]
        if result['output'] is None:
            raise AssertionError(result['execution'])
        # Each assertion sees the same real captured answer, never another
        # provider call or a synthetic success. This is evaluation replay only.
        return result['output'].model_copy(deep=True)

    matrix.run=run
    for name,cls in vars(matrix).items():
        if not isinstance(cls,type) or not name.startswith('Test'):
            continue
        if selected_categories and name not in selected_categories:
            continue
        active[0]=name
        instance=cls()
        if hasattr(instance,'setup_class'):
            instance.setup_class()
        for method in vars(cls):
            if not method.startswith('test_'):
                continue
            try:
                getattr(instance,method)()
                rows.append({'category':name,'test':method,'status':'PASS'})
            except Exception as exc:
                rows.append({'category':name,'test':method,'status':'FAIL','error':str(exc)})
        # Save partial progress after every category.
        write_json(destination/'assertions.json',rows)

    categories=[]
    guards=[]
    for category,keys in category_queries.items():
        official=category.startswith('TestT') and category!='TestTerminologyLockdown'
        failures=[r['test'] for r in rows if r['category']==category and r['status']=='FAIL']
        outcomes=[]
        for key in sorted(keys):
            item=captured[key];out=item['output']
            expected='REFERRED' if category.startswith('TestT05_') else ('ABSTAINED' if category.startswith('TestT06_') else 'ANSWERED')
            substantive=bool(out and out.answer_status==expected)
            if expected=='ANSWERED':
                substantive=substantive and bool(out.citations and out.source_verified and not out.answer_cache_reused)
            elif expected=='ABSTAINED':
                substantive=substantive and out.stop_reason in ('no_direct_answer','no_approved_passages_in_required_languages') and not out.citations
            elif expected=='REFERRED':
                substantive=substantive and out.response_level.value=='LEVEL_D' and out.fatwa_referral is not None and not out.citations
            outcomes.append({'request_id':item['request_id'],'elapsed_seconds':item['elapsed_seconds'],
                'answer_status':out.answer_status if out else item['execution'],
                'stop_reason':out.stop_reason if out else item['execution'],
                'level':out.response_level.value if out and out.response_level else None,
                'answer_cache_reused':out.answer_cache_reused if out else None,
                'required_outcome_met':bool(substantive)})
        (categories if official else guards).append({'category':category,'status':'PASS' if not failures and all(o['required_outcome_met'] for o in outcomes) else 'FAIL',
            'failed_assertions':failures,'outcomes':outcomes})
    return {'categories':categories,'guards':guards,'assertions':rows,
        'official_passed':sum(c['status']=='PASS' for c in categories),'official_total':len(categories),
        'unique_queries':len(captured),'independent_scholarly_review':'NOT_DONE',
        'method':'One real fresh pipeline result per unique input; unchanged locked assertions replayed against that result. Extra status/source gates prevent vacuous passes.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('offline','live'),default='offline')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--query-timeout',type=int,default=240,
        help='Evaluation worker limit, not a production answer deadline; timeout is a failure.')
    parser.add_argument('--worker',nargs=2,type=Path)
    parser.add_argument('--category',action='append',help='Targeted live rerun; cannot certify the full matrix.')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    if args.worker:
        worker(*args.worker)
        return
    dest=(args.output or root/'data/evaluation_runs'/time.strftime('%Y%m%d-%H%M%S')).resolve()
    dest.mkdir(parents=True,exist_ok=False,mode=0o700)
    clone,store=isolated_checkout(root,args.mode=='live')
    env=os.environ.copy()
    env.update(BAYAN_PRIVATE_STORE=str(store),PYTHONPATH=str(clone),PYTHONPYCACHEPREFIX=str(clone/'pycache'))
    if args.mode=='live':
        from dotenv import dotenv_values
        env['GEMINI_API_KEY']=env.get('GEMINI_API_KEY') or dotenv_values(root/'.env').get('GEMINI_API_KEY','')
        if not env['GEMINI_API_KEY']:
            raise SystemExit('Live evaluation requires a configured Gemini key.')
        policy_path=clone/'configs/cache_policy.json'
        policy=json.loads(policy_path.read_text());policy['decision_reuse_enabled']=False
        write_json(policy_path,policy)
    else:
        env['GEMINI_API_KEY']=''
    manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in ('src','configs','scripts','tests') for p in (root/directory).rglob('*')
        if p.is_file() and '__pycache__' not in p.parts}
    write_json(dest/'manifest.json',manifest)
    metadata={'mode':args.mode,'started_at':datetime.now(timezone.utc).isoformat(),
        'checkout':str(clone),'python':sys.version,'warm_source_snapshot':args.mode=='live',
        'previous_answer_reuse':False,'production_storage_modified':False}
    write_json(dest/'run.json',metadata)
    if args.mode=='offline':
        with (dest/'pytest.log').open('w') as log:
            p=subprocess.run([sys.executable,'-m','pytest','tests','-q','--tb=short',
                '--junitxml='+str(dest/'pytest.xml')],cwd=clone,env=env,stdout=log,stderr=subprocess.STDOUT)
        metadata['exit_code']=p.returncode
        print((dest/'pytest.log').read_text()[-7000:])
    else:
        metadata.update(live_matrix(clone,dest,env,args.query_timeout,args.category))
        metadata['selected_categories']=args.category
        metadata['full_matrix']=not bool(args.category)
        metadata['exit_code']=0 if ((metadata['official_passed']==12 if not args.category else
            all(c['status']=='PASS' for c in metadata['categories']))
            and all(r['status']=='PASS' for r in metadata['assertions'])
            and all(g['status']=='PASS' for g in metadata['guards'])) else 1
        print(f'Official categories: {metadata["official_passed"]}/{metadata["official_total"]}',flush=True)
    metadata['finished_at']=datetime.now(timezone.utc).isoformat()
    write_json(dest/'run.json',metadata)
    print('Evidence:',dest,flush=True)
    sys.exit(metadata['exit_code'])


if __name__=='__main__':
    main()
