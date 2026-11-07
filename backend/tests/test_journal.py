from uuid import uuid4
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import PaperTrade, PriceCache, Scan, Session, Setting, init_db
from app.main import app


@pytest.fixture
def journal_scan():
    init_db()
    item = {'symbol': 'JOURNALTEST.NS', 'currency': 'INR', 'price': 100, 'as_of': '2026-11-06', 'signal': 'HOLD', 'stale': False, 'source': 'Test fixture', 'risk': {'stop_reference': None, 'target_reference': None}}
    with Session.begin() as db:
        scan = Scan(status='completed', request={'market': 'IN', 'symbols': [item['symbol']]}, result={'items': [item]})
        db.add(scan)
        db.flush()
        scan_id = scan.id
    yield scan_id
    with Session.begin() as db:
        db.execute(delete(PaperTrade).where(PaperTrade.scan_id == scan_id))
        db.execute(delete(Scan).where(Scan.id == scan_id))


def payload(source_scan_id, **extra):
    return {'client_token': str(uuid4()), 'scan_id': source_scan_id, 'market': 'IN', 'symbol': 'JOURNALTEST.NS', 'quantity': 10, 'entry_price': '101.25', 'entry_fees': '2.50', 'notes': 'A hypothetical thesis', **extra}


def test_paper_entry_exit_fees_persistence_and_duplicate_protection(journal_scan):
    with TestClient(app) as client:
        body = payload(journal_scan)
        opened = client.post('/api/journal', json=body)
        assert opened.status_code == 201
        trade = opened.json()
        assert trade['status'] == 'OPEN' and trade['pnl'] == '-15.00'
        assert trade['stop_loss'] is None and trade['target_price'] is None
        assert trade['signal_snapshot']['signal'] == 'HOLD'
        assert client.post('/api/journal', json=body).status_code == 409
        assert any(t['id'] == trade['id'] for t in client.get('/api/journal?market=IN').json()['trades'])
        assert all(t['id'] != trade['id'] for t in client.get('/api/journal?market=US').json()['trades'])
        closed = client.post(f"/api/journal/{trade['id']}/close", json={'exit_price': '110.75', 'exit_fees': '1.25', 'reflection': 'Wait for stronger agreement next time.'})
        assert closed.status_code == 200
        result = closed.json()
        assert result['status'] == 'CLOSED' and result['pnl_kind'] == 'realized'
        assert result['pnl'] == '91.25' and result['closed_at']
        assert result['reflection'].startswith('Wait for stronger')
        assert client.post(f"/api/journal/{trade['id']}/close", json={'exit_price': '115'}).status_code == 409
        saved = next(t for t in client.get('/api/journal?market=IN').json()['trades'] if t['id'] == trade['id'])
        assert saved['pnl'] == '91.25'


@pytest.mark.parametrize('changes', [{'quantity': 0}, {'quantity': 1.5}, {'entry_price': 'NaN'}, {'entry_price': '-10'}, {'entry_fees': '-1'}, {'symbol': 'NOTINSCAN.NS'}, {'market': 'US', 'symbol': 'NVDA'}, {'scan_id': str(uuid4())}])
def test_invalid_or_cross_market_paper_entries_are_rejected(journal_scan, changes):
    with TestClient(app) as client:
        assert client.post('/api/journal', json=payload(journal_scan, **changes)).status_code == 422


def test_watchlist_add_is_deduplicated_preserves_members_and_respects_limit():
    init_db()
    with Session() as db:
        saved = db.get(Setting, 'watchlist_IN')
        original = deepcopy(saved.value) if saved else None
    try:
        with TestClient(app) as client:
            client.put('/api/watchlist', json={'market': 'IN', 'symbols': ['TCS.NS']})
            for _ in range(2):
                response = client.post('/api/watchlist/symbols', json={'market': 'IN', 'symbol': ' reliance.ns '})
                assert response.status_code == 200
                assert response.json()['symbols'] == ['TCS.NS', 'RELIANCE.NS']
            assert client.post('/api/watchlist/symbols', json={'market': 'IN', 'symbol': 'NVDA'}).status_code == 422
            client.put('/api/watchlist', json={'market': 'IN', 'symbols': [f'TEST{i}.NS' for i in range(20)]})
            assert client.post('/api/watchlist/symbols', json={'market': 'IN', 'symbol': 'TCS.NS'}).status_code == 422
            assert len(client.get('/api/watchlist?market=IN').json()['symbols']) == 20
    finally:
        with Session.begin() as db:
            db.execute(delete(Setting).where(Setting.key == 'watchlist_IN'))
            if original is not None:
                db.add(Setting(key='watchlist_IN', value=original))


def test_paper_exit_levels_can_be_saved_edited_cleared_and_preserved_on_close(journal_scan):
    with TestClient(app) as client:
        response = client.post('/api/journal', json=payload(journal_scan, stop_loss='95.5', target_price='120'))
        assert response.status_code == 201
        trade = response.json()
        assert trade['stop_loss'] == '95.5' and trade['target_price'] == '120'
        assert trade['levels_mode'] == 'manual'
        path = f"/api/journal/{trade['id']}/levels"
        changed = client.put(path, json={'stop_loss': '98.1234', 'target_price': '125.75'})
        assert changed.status_code == 200
        assert changed.json()['stop_loss'] == '98.1234'
        invalid = client.put(path, json={'stop_loss': '105', 'target_price': '130'})
        assert invalid.status_code == 422
        assert 'below your entry' in invalid.json()['detail']
        saved = next(t for t in client.get('/api/journal?market=IN').json()['trades'] if t['id'] == trade['id'])
        assert saved['stop_loss'] == '98.1234' and saved['target_price'] == '125.7500'
        assert saved['status'] == 'OPEN' and saved['pnl'] == '-15.00'
        cleared = client.put(path, json={'stop_loss': None, 'target_price': None}).json()
        assert cleared['stop_loss'] is None and cleared['target_price'] is None
        assert client.put(path, json={'stop_loss': '90', 'target_price': '115'}).status_code == 200
        closed = client.post(f"/api/journal/{trade['id']}/close", json={'exit_price': '115'}).json()
        assert closed['stop_loss'] == '90.0000' and closed['target_price'] == '115.0000'
        assert client.put(path, json={'stop_loss': '80', 'target_price': '140'}).status_code == 409


@pytest.mark.parametrize('levels', [{'stop_loss': '0'}, {'stop_loss': 'NaN'}, {'stop_loss': '101.25'}, {'stop_loss': '110'}, {'target_price': '101.25'}, {'target_price': '-1'}, {'target_price': '100'}, {'target_price': 'NaN'}])
def test_invalid_exit_levels_are_rejected_at_entry(journal_scan, levels):
    with TestClient(app) as client:
        assert client.post('/api/journal', json=payload(journal_scan, **levels)).status_code == 422
