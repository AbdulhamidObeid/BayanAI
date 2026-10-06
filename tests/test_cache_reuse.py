"""Cache boundaries: saved evidence accelerates retrieval, never proves coverage."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_trust_pipeline import harness, citation
from src.core.cache_policy import CacheMeasurements, runtime_fingerprint, cache_config
from src.core.integrity import AuthenticatedStore, fingerprint
from src.core.official_sources import OfficialSources
from src.core.schema import PipelineInput
from src.core.analysis_agent import SafetyStop


def test_exact_repeat_skips_model_work_but_reports_reuse():
    pipe, analyzer, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    first = pipe.run_pipeline(inp)
    second = pipe.run_pipeline(inp)
    assert first.answer_status == second.answer_status == 'ANSWERED'
    assert not first.answer_cache_reused and second.answer_cache_reused
    assert first.cache_metrics['decision'].misses == 1
    assert second.cache_metrics['decision'].hits == 1
    assert second.total_duration_ms > 0
    assert all(step.duration_ms >= 0 for step in second.telemetry)
    assert analyzer.analyze.call_count == analyzer.select.call_count == 1
    assert pipe.localizer_factory(None).localize.call_count == 1


@pytest.mark.parametrize('change', [
    {'query': 'What is this concept? Explain its exceptions too.'},
    {'cultural_context': 'european'},
    {'cultural_persona': 'NEW_MUSLIM'},
    {'requested_response_level': 'LEVEL_C'},
])
def test_changed_input_requires_fresh_analysis_selection_and_review(change):
    pipe, analyzer, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    assert pipe.run_pipeline(inp).answer_status == 'ANSWERED'
    second = pipe.run_pipeline(PipelineInput(**{**inp.model_dump(), **change}))
    assert second.answer_status == 'ANSWERED' and not second.answer_cache_reused
    assert second.cache_metrics['decision'].misses == 1
    assert analyzer.analyze.call_count == analyzer.select.call_count == 2
    assert pipe.localizer_factory(None).localize.call_count == 2


def test_changed_language_does_not_reuse_answer():
    pipe, analyzer, sources = harness()
    assert pipe.run_pipeline(PipelineInput(query='What is this concept?')).answer_status == 'ANSWERED'
    sources.retrieve.return_value = [citation(('ar',))]
    result = pipe.run_pipeline(PipelineInput(query='What is this concept?', target_language='ar'))
    assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
    assert analyzer.analyze.call_count == 2


def test_share_option_does_not_change_answer_identity():
    pipe, analyzer, _ = harness()
    pipe.run_pipeline(PipelineInput(query='What is this concept?'))
    result = pipe.run_pipeline(PipelineInput(query='What is this concept?', share_response=True))
    assert result.answer_cache_reused and analyzer.analyze.call_count == 1


def test_runtime_change_invalidates_approved_answer(monkeypatch):
    monkeypatch.setattr('src.core.orchestrator.runtime_fingerprint', lambda: 'version-one')
    pipe, analyzer, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    pipe.run_pipeline(inp)
    monkeypatch.setattr('src.core.orchestrator.runtime_fingerprint', lambda: 'version-two')
    result = pipe.run_pipeline(inp)
    assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
    assert analyzer.analyze.call_count == analyzer.select.call_count == 2


@pytest.mark.parametrize('path', [
    'src/core/orchestrator.py', 'src/core/schema.py', 'src/agents/cultural_localizer.py',
    'configs/model_routing.json', 'configs/trust_policy.json',
    'configs/agents/cultural_localizer/rules.md',
])
def test_actual_model_prompt_and_code_edits_change_fingerprint(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('original', encoding='utf-8')
    before = runtime_fingerprint(tmp_path)
    target.write_text('updated', encoding='utf-8')
    assert runtime_fingerprint(tmp_path) != before


def test_expired_decision_requires_fresh_work():
    pipe, analyzer, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    pipe.run_pipeline(inp)
    store = AuthenticatedStore()
    with store.connect() as db:
        db.execute("UPDATE records SET expires=0 WHERE kind='decision'")
    result = pipe.run_pipeline(inp)
    assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
    assert analyzer.analyze.call_count == 2


def test_answer_hits_do_not_extend_original_approval_lifetime():
    pipe, _, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    pipe.run_pipeline(inp)
    store = AuthenticatedStore()
    with store.connect() as db:
        before = db.execute("SELECT expires FROM records WHERE kind='decision'").fetchone()[0]
    assert pipe.run_pipeline(inp).answer_cache_reused
    with store.connect() as db:
        after = db.execute("SELECT expires FROM records WHERE kind='decision'").fetchone()[0]
    assert after == before


def test_changed_evidence_requires_fresh_work():
    pipe, analyzer, sources = harness()
    inp = PipelineInput(query='What is this concept?')
    pipe.run_pipeline(inp)
    changed = citation()
    changed.translations['en'] = changed.accredited_translation = 'Changed publisher passage'
    changed.source_digest = OfficialSources.digest(changed)
    sources.retrieve.return_value = [changed]
    result = pipe.run_pipeline(inp)
    assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
    assert analyzer.analyze.call_count == analyzer.select.call_count == 2


def test_disable_decisions_preserves_fresh_processing(monkeypatch):
    config = {**cache_config(), 'decision_reuse_enabled': False}
    monkeypatch.setattr('src.core.orchestrator.cache_config', lambda: config)
    pipe, analyzer, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    for _ in range(2):
        result = pipe.run_pipeline(inp)
        assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
        assert 'decision' not in result.cache_metrics
    assert analyzer.analyze.call_count == 2
    with AuthenticatedStore().connect() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='decision'").fetchone()[0] == 0


def test_different_questions_share_sources_without_sharing_decisions(monkeypatch):
    store = AuthenticatedStore()
    key = fingerprint({'kind': 'quran', 'reference': '112:1', 'languages': ['en', 'ar'], 'format_version': 5})
    store.put('source', key, citation().model_dump(mode='json'), 3600)
    monkeypatch.setattr(OfficialSources, 'retrieve',
        lambda self, analysis, languages, explicit=None: [self.get('quran', '112:1', languages)])
    monkeypatch.setattr('requests.get', lambda *a, **k: pytest.fail('Cached source should avoid network'))
    pipe, analyzer, _ = harness()
    pipe.sources_factory = OfficialSources
    for question in ['What is this concept?', 'What is this concept? Include its exceptions.']:
        result = pipe.run_pipeline(PipelineInput(query=question))
        assert result.answer_status == 'ANSWERED' and not result.answer_cache_reused
        assert result.cache_metrics['source'].hits == 1
    assert analyzer.analyze.call_count == analyzer.select.call_count == 2
    assert pipe.localizer_factory(None).localize.call_count == 2


def test_similar_question_with_failed_review_cannot_receive_previous_answer():
    pipe, analyzer, _ = harness()
    assert pipe.run_pipeline(PipelineInput(query='What is this concept?')).answer_status == 'ANSWERED'
    pipe.localizer_factory(None).localize.side_effect = SafetyStop('unsupported_personalized_explanation')
    result = pipe.run_pipeline(PipelineInput(query='What is this concept? Include exceptions.'))
    assert result.answer_status != 'ANSWERED' and not result.answer_cache_reused
    assert not result.citations
    assert analyzer.analyze.call_count == analyzer.select.call_count == 2


def test_cached_answer_still_requires_terminology_approval():
    pipe, _, _ = harness()
    inp = PipelineInput(query='What is this concept?')
    assert pipe.run_pipeline(inp).answer_status == 'ANSWERED'
    pipe.preserver_factory = lambda: (_ for _ in ()).throw(RuntimeError('Unavailable terminology check'))
    result = pipe.run_pipeline(inp)
    assert result.answer_status != 'ANSWERED' and not result.answer_cache_reused
    assert result.cache_metrics['decision'].hits == 1
    assert not result.citations


def test_expired_and_tampered_records_are_not_hits(tmp_path):
    metrics = CacheMeasurements()
    store = AuthenticatedStore(tmp_path, measurements=metrics)
    store.put('source', 'expired', {'text': 'private'}, -1)
    assert store.get('source', 'expired') is None
    store.put('source', 'tampered', {'text': 'private'}, 3600)
    with store.connect() as db:
        db.execute("UPDATE records SET signature='invalid' WHERE id='tampered'")
    with pytest.raises(ValueError, match='authentication'):
        store.get('source', 'tampered')
    row = metrics.snapshot()['source']
    assert row['hits'] == 0 and row['misses'] == row['errors'] == 1
    assert 'private' not in json.dumps(metrics.snapshot())


def test_parallel_cache_counters_are_request_local():
    metrics = CacheMeasurements()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: metrics.record('source', 'hits', 1), range(200)))
    assert metrics.snapshot()['source']['hits'] == 200
    assert CacheMeasurements().snapshot() == {}
