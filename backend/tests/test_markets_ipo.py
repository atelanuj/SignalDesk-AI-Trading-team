import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app import ipo
from app.config import NSE_SYMBOLS, US_SYMBOLS
from app.main import app
from app.schemas import ScanRequest, ChatRequest, WatchlistRequest


def test_market_defaults_and_legacy_jobs():
    assert ScanRequest().symbols == NSE_SYMBOLS
    assert ScanRequest(market='US').symbols == US_SYMBOLS
    assert ScanRequest(symbols=['NVDA']).market == 'US'
    assert ScanRequest(symbols=['TCS.BO']).market == 'IN'


@pytest.mark.parametrize('market,symbols', [('IN', ['NVDA']), ('US', ['TCS.NS']), ('IN', ['TCS.NS', 'NVDA'])])
def test_cross_market_context_is_rejected(market, symbols):
    for schema, extra in [(ScanRequest, {}), (WatchlistRequest, {}), (ChatRequest, {'conversation_id': 'a'*36, 'message': 'Screen'})]:
        with pytest.raises(ValueError):
            schema(market=market, symbols=symbols, **extra)


def test_ipo_feed_preserves_missing_fields_and_reuses_cache(monkeypatch):
    monkeypatch.setattr(ipo, '_cache', None)
    monkeypatch.setattr(ipo, '_expires', 0)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        rows = [{'companyName': 'Fixture Ltd', 'issueStartDate': '01-Jan-2027', 'issueEndDate': '03-Jan-2027'}] if 'ipo-current' in str(request.url) else []
        return httpx.Response(200, json=rows)
    real_client = httpx.Client
    with patch.object(ipo.httpx, 'Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        feed = ipo.indian_ipos()
        assert feed['status'] == 'ok'
        assert feed['items'][0]['price_band'] is None
        assert feed['items'][0]['open_date'] == '01-Jan-2027'
        assert ipo.indian_ipos() == feed
        assert len(calls) == 3


def test_ipo_provider_failure_is_visible(monkeypatch):
    monkeypatch.setattr(ipo, '_cache', None)
    monkeypatch.setattr(ipo, '_expires', 0)
    real_client = httpx.Client
    with patch.object(ipo.httpx, 'Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw)):
        feed = ipo.indian_ipos()
        assert feed['status'] == 'unavailable'
        assert feed['errors'] and feed['items'] == []


def test_us_ipo_endpoint_is_rejected():
    with TestClient(app) as client:
        assert client.get('/api/ipos?market=US').status_code == 422
        assert client.get('/api/ipos?market=OTHER').status_code == 422
