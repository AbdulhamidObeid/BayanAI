"""Evaluation must never convert an empty answer or outage into benchmark approval."""
import json
from types import SimpleNamespace
import subprocess
import hashlib
import sqlite3
import pytest
from scripts import evaluate_acceptance as evaluation
from src.core.schema import PipelineOutput
from tests.test_trust_pipeline import citation


@pytest.mark.parametrize('status,verified,reused,with_sources', [
    ('ABSTAINED', False, False, False),
    ('ANSWERED', False, False, True),
    ('ANSWERED', True, True, True),
    ('ANSWERED', True, False, False),
])
def test_passing_keyword_assertion_cannot_approve_invalid_outcome(monkeypatch,tmp_path,status,verified,reused,with_sources):
    matrix=SimpleNamespace()
    class TestT01_Probe:
        def test_keyword(self):
            assert 'expected' in matrix.run('synthetic question').localized_text
    matrix.TestT01_Probe=TestT01_Probe
    monkeypatch.setattr(evaluation.importlib,'import_module',lambda name:matrix)
    def execute(command,**kwargs):
        output=PipelineOutput(query='synthetic question',target_language='en',localized_text='expected keyword',
            answer_status=status,source_verified=verified,answer_cache_reused=reused,
            citations=[citation()] if with_sources else [])
        evaluation.write_json(__import__('pathlib').Path(command[-1]),output.model_dump(mode='json'))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(evaluation.subprocess,'run',execute)
    result=evaluation.live_matrix(tmp_path,tmp_path,{},1)
    assert result['assertions'][0]['status']=='PASS'
    assert result['categories'][0]['status']=='FAIL'
    assert result['official_passed']==0


def test_evaluation_timeout_is_retained_as_failure(monkeypatch,tmp_path):
    matrix=SimpleNamespace()
    class TestT01_Probe:
        def test_answer(self):
            matrix.run('synthetic question')
    matrix.TestT01_Probe=TestT01_Probe
    monkeypatch.setattr(evaluation.importlib,'import_module',lambda name:matrix)
    def execute(command,**kwargs):
        raise subprocess.TimeoutExpired(command,1)
    monkeypatch.setattr(evaluation.subprocess,'run',execute)
    result=evaluation.live_matrix(tmp_path,tmp_path,{},1)
    assert result['assertions'][0]['status']=='FAIL'
    assert result['categories'][0]['outcomes'][0]['answer_status']=='evaluation_timeout'
    assert result['official_passed']==0


def test_warm_snapshot_excludes_private_answers_and_preserves_source_expiry(monkeypatch,tmp_path):
    root=tmp_path/'project'
    for name in ('src','configs','scripts','tests','data/private'):
        (root/name).mkdir(parents=True,exist_ok=True)
    evaluation.write_json(root/'configs/trust_policy.json',{'private_store':'data/private'})
    private=root/'data/private'
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(private))
    (private/'integrity.key').write_bytes(b'x'*32)
    with sqlite3.connect(private/'records.db') as db:
        db.execute('CREATE TABLE records(kind TEXT,id TEXT,payload TEXT,signature TEXT,expires REAL)')
        db.executemany('INSERT INTO records VALUES(?,?,?,?,?)',[
            ('source','published','unchanged-original','unchanged-signature',12345),
            ('decision','answer','private-answer','sig',12345),
            ('receipt','shared','private-question','sig',12345),
            ('worker','token','credential','sig',12345)])
    before=hashlib.sha256((private/'records.db').read_bytes()).hexdigest()
    clone,store=evaluation.isolated_checkout(root,True)
    try:
        with sqlite3.connect(store/'records.db') as db:
            assert db.execute('SELECT * FROM records').fetchall()==[
                ('source','published','unchanged-original','unchanged-signature',12345)]
        assert not (clone/'data/private').exists()
        assert hashlib.sha256((private/'records.db').read_bytes()).hexdigest()==before
    finally:
        evaluation.shutil.rmtree(clone)
