from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import universes
from app.main import app
from app.schemas import ScanRequest


def test_constituents_are_complete_deduplicated_and_yahoo_compatible():
    csv = 'Company Name,Symbol\nMahindra,M&M\nTCS,TCS\nTCS,TCS\n'
    assert universes.parse_constituents(csv, 2) == ['M&M.NS', 'TCS.NS']
    assert ScanRequest(market='IN', symbols=['M&M.NS']).symbols == ['M&M.NS']
    with pytest.raises(ValueError):
        universes.parse_constituents(csv, 50)


@pytest.mark.parametrize('content', ['<html>Access denied</html>', 'Symbol\n../../bad\n', 'Symbol\n'])
def test_invalid_constituents_are_rejected(content):
    with pytest.raises(ValueError):
        universes.parse_constituents(content)


def test_index_screens_allow_full_constituent_lists_but_watchlists_remain_bounded():
    symbols = [f'TICKER{i}.NS' for i in range(200)]
    assert len(ScanRequest(universe='nifty200', symbols=symbols).symbols) == 200
    for args in [{'symbols': symbols}, {'market': 'US', 'universe': 'nifty50', 'symbols': ['NVDA']}, {'universe': 'invented'}]:
        with pytest.raises(ValueError):
            ScanRequest(**args)


def test_constituent_provider_uses_cache_and_preserves_source(monkeypatch):
    monkeypatch.setattr(universes, '_cache', {})
    content = 'Symbol\n' + '\n'.join(f'TICKER{i}' for i in range(50))
    response = httpx.Response(200, text=content, request=httpx.Request('GET', 'https://www.niftyindices.com'))
    with patch.object(universes.httpx, 'get', return_value=response) as get:
        data = universes.get_universe('nifty50')
        assert data['count'] == 50 and len(data['symbols']) == 50
        assert data['source'].endswith('ind_nifty50list.csv')
        assert universes.get_universe('nifty50') == data
        get.assert_called_once()


def test_constituent_endpoint_fails_visibly_without_a_partial_fallback():
    with TestClient(app) as client:
        with patch('app.main.get_universe', side_effect=ValueError('Incomplete CSV')):
            assert client.get('/api/universes/nifty100').status_code == 503
        assert client.get('/api/universes/not-an-index').status_code == 404
