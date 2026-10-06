"""Recorded actual failures and current-pipeline identity remain visible."""
import hashlib
import json
import pytest
from scripts.evaluate_full_suite import input_key,load_results
from src.core.schema import PipelineInput,PipelineOutput,CulturalPersona


def completed(tmp_path):
    root=tmp_path/'root';root.mkdir();(root/'src').mkdir()
    source=root/'src/current.py';source.write_text('current code')
    run=tmp_path/'run';run.mkdir()
    (run/'run.json').write_text(json.dumps({'mode':'live','finished_at':'done','full_matrix':True}))
    (run/'manifest.json').write_text(json.dumps({'src/current.py':hashlib.sha256(source.read_bytes()).hexdigest()}))
    inp=PipelineInput(query='Synthetic question')
    (run/'query-01.json').write_text(inp.model_dump_json())
    (run/'answer-01.json').write_text(PipelineOutput(query=inp.query,target_language='en',
        answer_status='ABSTAINED',localized_text='No evidence found.').model_dump_json())
    return root,run,inp


def test_recorded_real_failure_is_never_changed_into_success(tmp_path):
    root,run,inp=completed(tmp_path)
    result=load_results(run,root)[input_key(inp.model_dump(mode='json'))]
    assert result['answer_status']=='ABSTAINED' and not result['citations']


def test_changed_pipeline_cannot_replay_earlier_approval(tmp_path):
    root,run,_=completed(tmp_path);(root/'src/current.py').write_text('changed code')
    with pytest.raises(ValueError,match='differ from the live result'):load_results(run,root)


def test_targeted_or_unfinished_run_cannot_certify_whole_suite(tmp_path):
    root,run,_=completed(tmp_path)
    (run/'run.json').write_text(json.dumps({'mode':'live','finished_at':'done','full_matrix':False}))
    with pytest.raises(ValueError,match='completed full live matrix'):load_results(run,root)


def test_different_audience_needs_its_own_actual_pipeline_result():
    first=PipelineInput(query='Synthetic question')
    second=first.model_copy(update={'cultural_persona':CulturalPersona.ACADEMIC_SEEKER})
    assert input_key(first.model_dump(mode='json'))!=input_key(second.model_dump(mode='json'))
