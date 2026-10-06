"""Unit tests for the dynamic zero-token Google Gemini API health probe."""
import os
from unittest.mock import Mock
import pytest
from starlette.testclient import TestClient
from src.web.app import app, _health_cache, _health_lock


def test_api_health_live_key(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-configured-key')
    provider = Mock()
    provider.models.list.return_value = iter([Mock(name='model')])
    monkeypatch.setattr('google.genai.Client', Mock(return_value=provider))
    client = TestClient(app)
    with _health_lock:
        _health_cache['checked_at'] = 0.0
    res = client.get('/api/health?refresh=true').json()
    assert 'status' in res
    assert 'dot' in res
    assert 'system' in res
    assert res['system'] == 'Bayan-AI'
    assert 'message_en' in res
    assert 'message_ar' in res
    assert res['status'] == 'configured' and res['connected'] is True
    provider.models.list.assert_called_once_with(config={'page_size': 1})


def test_api_health_missing_key():
    client = TestClient(app)
    orig = os.environ.get('GEMINI_API_KEY')
    try:
        os.environ['GEMINI_API_KEY'] = ''
        with _health_lock:
            _health_cache['checked_at'] = 0.0
        res = client.get('/api/health?refresh=true').json()
        assert res['status'] == 'missing_key'
        assert res['connected'] is False
        assert res['dot'] == 'error'
        assert res['message_en'] == 'API Key Missing'
        assert res['message_ar'] == 'مفتاح API غير متوفر'
        assert 'help_en' in res
        assert 'help_ar' in res
    finally:
        if orig:
            os.environ['GEMINI_API_KEY'] = orig
        with _health_lock:
            _health_cache['checked_at'] = 0.0


def test_api_health_invalid_key(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'AIzaSyInvalidFakeKey')
    provider = Mock()
    provider.models.list.side_effect = RuntimeError('400 API_KEY_INVALID: API key not valid')
    monkeypatch.setattr('google.genai.Client', Mock(return_value=provider))
    res = TestClient(app).get('/api/health?refresh=true').json()
    assert res['status'] == 'invalid_key'
    assert res['connected'] is False
    assert res['dot'] == 'error'
    assert res['message_en'] == 'Invalid API Key'
    assert res['message_ar'] == 'مفتاح API غير صالح'


@pytest.mark.parametrize('error,status,dot', [
    ('Connection failed', 'degraded', 'error'),
    ('503 UNAVAILABLE', 'degraded', 'error'),
    ('429 RESOURCE_EXHAUSTED', 'quota_depleted', 'warning'),
])
def test_api_health_network_and_quota_are_not_invalid_keys(monkeypatch,error,status,dot):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-configured-key')
    provider = Mock()
    provider.models.list.side_effect = RuntimeError(error)
    monkeypatch.setattr('google.genai.Client', Mock(return_value=provider))
    res = TestClient(app).get('/api/health?refresh=true').json()
    assert res['status'] == status and res['dot'] == dot
    assert res['connected'] is False
