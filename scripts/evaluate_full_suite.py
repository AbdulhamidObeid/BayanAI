"""Run unchanged pytest assertions using real, manifest-matched acceptance results.

Source law: docs/guides/10_immutability_constitution_and_source_lock.md.
Only the three integration modules use recorded/worker pipeline results. Unit
fixtures remain isolated and provider-disabled. New integration inputs run the
real pipeline in a fresh worker. No result status or assertion is rewritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.evaluate_acceptance import isolated_checkout,write_json


def input_key(payload):
    return json.dumps(payload,sort_keys=True,ensure_ascii=False)


def load_results(directory,root):
    """An older pipeline/configuration cannot certify the current suite."""
    run=json.loads((directory/'run.json').read_text())
    if run.get('mode')!='live' or not run.get('finished_at') or not run.get('full_matrix'):
        raise ValueError('A completed full live matrix is required, including failed outcomes.')
    manifest=json.loads((directory/'manifest.json').read_text())
    for relative,digest in manifest.items():
        if not relative.startswith(('src/','configs/','tests/')):continue
        path=root/relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Current code/config/tests differ from the live result: '+relative)
    from src.core.schema import PipelineInput,PipelineOutput
    results={}
    for request in directory.glob('query-*.json'):
        answer=directory/request.name.replace('query-','answer-')
        if not answer.is_file():continue  # New real worker will retry failed execution, never synthesize success.
        payload=PipelineInput.model_validate(json.loads(request.read_text())).model_dump(mode='json')
        result=PipelineOutput.model_validate(json.loads(answer.read_text()))
        results[input_key(payload)]=result.model_dump(mode='json')
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--query-timeout',type=int,default=240,
        help='Additional live worker bound; timeout remains a test failure.')
    args=parser.parse_args();matrix_directory=args.matrix.resolve()
    recorded=load_results(matrix_directory,ROOT)
    destination=args.output.resolve()
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    clone,_=isolated_checkout(ROOT,False)
    worker_clone,store=isolated_checkout(ROOT,True)
    from dotenv import dotenv_values
    env=os.environ.copy();env.update(BAYAN_PRIVATE_STORE=str(store),PYTHONPATH=str(worker_clone),
        PYTHONPYCACHEPREFIX=str(worker_clone/'pycache'))
    env['GEMINI_API_KEY']=env.get('GEMINI_API_KEY') or dotenv_values(ROOT/'.env').get('GEMINI_API_KEY','')
    if not env['GEMINI_API_KEY']:raise ValueError('Configured live provider access is required.')
    policy=json.loads((worker_clone/'configs/cache_policy.json').read_text());policy['decision_reuse_enabled']=False
    write_json(worker_clone/'configs/cache_policy.json',policy)
    # Import all production and pytest modules from the isolated checkout.
    os.chdir(clone);sys.path.insert(0,str(clone))
    for name in list(sys.modules):
        if name=='src' or name.startswith('src.') or name=='tests' or name.startswith('tests.'):
            del sys.modules[name]
    from src.core.schema import PipelineInput,PipelineOutput
    def pipeline(inp):
        payload=inp.model_dump(mode='json');key=input_key(payload)
        if key not in recorded:
            number=len(list(destination.glob('query-*.json')))+1
            req=destination/f'query-{number:02}.json';answer=destination/f'answer-{number:02}.json'
            write_json(req,payload);started=time.monotonic()
            with (destination/f'worker-{number:02}.log').open('w') as log:
                p=subprocess.run([sys.executable,str(worker_clone/'scripts/evaluate_acceptance.py'),
                    '--worker',str(req),str(answer)],cwd=worker_clone,env=env,
                    stdout=log,stderr=subprocess.STDOUT,timeout=args.query_timeout)
            if p.returncode or not answer.exists():raise AssertionError('Real pipeline worker failed')
            result=PipelineOutput.model_validate(json.loads(answer.read_text()))
            recorded[key]=result.model_dump(mode='json')
            print('Additional actual input:',number,result.answer_status,result.stop_reason,
                round(time.monotonic()-started,2),'seconds',flush=True)
        return PipelineOutput.model_validate(recorded[key])
    def run(query,target_language='en',cultural_context='general'):
        return pipeline(PipelineInput(query=query,target_language=target_language,cultural_context=cultural_context))
    class IntegrationReplay:
        def pytest_collection_modifyitems(self,items):
            # Patch only integration entrypoints, never unit fixture dependencies.
            for module in {item.module for item in items}:
                name=module.__name__.rsplit('.',1)[-1]
                if name in ('test_official_bathel_benchmarks','test_red_teaming'):
                    module.run=run
                elif name=='test_dawa_rag':
                    module.run_pipeline=pipeline
    os.environ['GEMINI_API_KEY']=''
    os.environ['PYTHONPYCACHEPREFIX']=str(clone/'pycache')
    import pytest
    status=pytest.main(['tests','-q','--tb=short','--junitxml='+str(destination/'pytest.xml')],
        plugins=[IntegrationReplay()])
    write_json(destination/'run.json',{'mode':'full_suite_with_actual_integration_results',
        'matrix':str(matrix_directory),'finished_at':datetime.now(timezone.utc).isoformat(),
        'exit_code':status,'previous_answer_reuse':False,'assertions_modified':False,
        'production_storage_modified':False,'additional_real_inputs':len(list(destination.glob('query-*.json')))})
    sys.exit(status)


if __name__=='__main__':main()
