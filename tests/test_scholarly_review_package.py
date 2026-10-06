"""Human-review exports preserve evidence and never infer approval."""
import hashlib
import json
import pytest
from scripts.prepare_scholarly_review import prepare


def test_package_is_pending_even_when_benchmarks_pass(tmp_path):
    run=tmp_path/'run';run.mkdir()
    contents={'run.json':{'mode':'live','finished_at':'2026-10-06','official_passed':12},
        'manifest.json':{'protected-file':'original-hash'},'assertions.json':[],
        'query-01.json':{'query':'synthetic acceptance query'},
        'answer-01.json':{'answer_status':'ANSWERED','localized_text':'exact output'},
        'answer-01.stages.json':[{'contract':'ExplanationReview','status':'returned'}]}
    for name,value in contents.items():(run/name).write_text(json.dumps(value))
    (run/'integrity.key').write_text('must not export secrets')
    destination=tmp_path/'review'
    result=prepare(run,destination)
    assert result['status']=='PENDING_INDEPENDENT_SCHOLARLY_REVIEW'
    assert result['reviewer_name'] is None
    assert result['cases'][0]['decision']=='PENDING'
    assert not (destination/'integrity.key').exists()
    hashes=json.loads((destination/'evidence-hashes.json').read_text())
    for name in contents:
        assert (destination/name).read_bytes()==(run/name).read_bytes()
        assert hashes[name]==hashlib.sha256((run/name).read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='preserve previous evidence'):
        prepare(run,destination)


@pytest.mark.parametrize('metadata',[{'mode':'offline','finished_at':'today'}, {'mode':'live'}])
def test_unfinished_or_offline_run_cannot_be_exported_as_live(tmp_path,metadata):
    (tmp_path/'run.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='completed real live'):
        prepare(tmp_path,tmp_path/'review')
