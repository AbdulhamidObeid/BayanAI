"""Production Gemini 3.8-only routes, paid budgets and bounded repair behavior."""
import copy
import time
from unittest.mock import Mock
import pytest
from src.core.analysis_agent import AnalysisAgent,SafetyStop
from src.core.model_router import QuotaLedger,routing_config,models_for
from src.core.schema import DiscoveryRanking,QueryAnalysis,EvidenceSelection,ExplanationDraft,ExplanationReview,SearchExpansion,SourceBoundDraft,PassageTranslationResult
from tests.test_model_routing import fake_provider,ProviderError


def test_every_production_contract_and_repair_uses_only_gemini_38():
    c=routing_config()
    assert set(c['models'])=={'gemini-3.8-flash'}
    assert c['models']['gemini-3.8-flash']['rpd']==10000
    for contract in (QueryAnalysis,DiscoveryRanking,SearchExpansion,EvidenceSelection,ExplanationDraft,ExplanationReview,SourceBoundDraft,PassageTranslationResult):
        for level in ('LEVEL_A','LEVEL_B','LEVEL_C','LEVEL_D'):
            for repair in (False,True):
                assert models_for(contract,{'level':level,'question_parts':[{}, {}, {}],'required_languages':['ar','en','fr']},c,repair)==['gemini-3.8-flash']


def test_paid_limit_change_preserves_previous_usage(tmp_path):
    c=copy.deepcopy(routing_config());model='gemini-3.8-flash'
    c['models'][model]['rpd']=20
    ledger=QuotaLedger(tmp_path,c);ledger.mark_exhausted(model)
    upgraded=QuotaLedger(tmp_path,routing_config())
    assert upgraded.snapshot()['models'][model]['used']==20
    assert upgraded.snapshot()['models'][model]['remaining']==9980
    assert upgraded.acquire([model],'review',time.monotonic()+1)==model


def test_single_model_transient_error_retries_the_same_model(monkeypatch):
    c=routing_config();c['service_cooldown_seconds']=0
    monkeypatch.setattr('src.core.model_router.routing_config',lambda:c)
    calls=fake_provider(monkeypatch,[ProviderError('Unavailable',503),DiscoveryRanking(candidate_ids=[0])])
    assert AnalysisAgent(time.monotonic()+10).request('Rank',{},DiscoveryRanking).candidate_ids==[0]
    assert calls==['gemini-3.8-flash']*2


def test_single_model_contract_repair_reuses_38_and_receives_feedback(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    client=Mock();client.models.generate_content.side_effect=[Mock(text='{}'),Mock(text=DiscoveryRanking(candidate_ids=[0]).model_dump_json())]
    monkeypatch.setattr(genai,'Client',Mock(return_value=client))
    assert AnalysisAgent(time.monotonic()+10).request('Rank',{},DiscoveryRanking).candidate_ids==[0]
    assert [c.kwargs['model'] for c in client.models.generate_content.call_args_list]==['gemini-3.8-flash']*2
    assert 'contract_repair' in client.models.generate_content.call_args.kwargs['contents']


def test_persistent_invalid_single_model_contract_stops_after_two_attempts(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    client=Mock();client.models.generate_content.return_value=Mock(text='{}')
    monkeypatch.setattr(genai,'Client',Mock(return_value=client))
    with pytest.raises(SafetyStop,match='invalid_model_response'):
        AnalysisAgent(time.monotonic()+10).request('Rank',{},DiscoveryRanking)
    assert client.models.generate_content.call_count==2


def test_single_model_rate_limit_never_uses_another_model(monkeypatch):
    calls=fake_provider(monkeypatch,[ProviderError('RequestsPerMinute exhausted')])
    with pytest.raises(SafetyStop,match='model_rate_limited'):
        AnalysisAgent(time.monotonic()+10).request('Rank',{},DiscoveryRanking)
    assert calls==['gemini-3.8-flash']
    assert QuotaLedger().snapshot()['models']['gemini-3.8-flash']['remaining']==9999


def test_gemini38_low_thinking_setting_reaches_actual_provider_body(monkeypatch):
    from google import genai
    fake_provider(monkeypatch,[DiscoveryRanking(candidate_ids=[0])])
    AnalysisAgent(time.monotonic()+10).request('Rank',{},DiscoveryRanking)
    body=genai.Client.call_args.kwargs['http_options'].extra_body
    assert body=={'generationConfig':{'thinkingConfig':{'thinkingLevel':'low'}}}
