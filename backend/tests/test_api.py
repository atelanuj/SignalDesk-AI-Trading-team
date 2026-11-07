"""Integration tests use the Compose PostgreSQL service and clean up their own rows."""
from fastapi.testclient import TestClient
from sqlalchemy import delete
from unittest.mock import patch
from app.db import Conversation, Message, Scan, Session, Setting
from app.main import app


def test_index_scan_queues_all_server_resolved_constituents():
    scan_id = None
    members = {'symbols': [f'TICKER{i}.NS' for i in range(100)], 'source': 'https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv', 'fetched_at': '2026-11-08T00:00:00+00:00'}
    try:
        with TestClient(app) as client, patch('app.main.get_universe', return_value=members):
            response = client.post('/api/scans', json={'market': 'IN', 'universe': 'nifty100', 'symbols': ['TCS.NS']})
            assert response.status_code == 202
            scan_id = response.json()['id']
            request = client.get(f'/api/scans/{scan_id}').json()['request']
            assert request['universe'] == 'nifty100'
            assert request['symbols'] == members['symbols']
            assert request['universe_source'] == members['source']
    finally:
        if scan_id:
            with Session.begin() as db:
                db.execute(delete(Scan).where(Scan.id == scan_id))


def test_database_health_conversation_and_queue():
    created_scan = created_conversation = None
    try:
        with TestClient(app) as client:
            assert client.get('/api/health').json()['database'] == 'connected'
            invalid = client.post('/api/scans', json={'symbols':['../../secret']})
            assert invalid.status_code == 422
            response = client.post('/api/conversations')
            assert response.status_code == 201
            created_conversation = response.json()['id']
            assert client.get(f'/api/conversations/{created_conversation}/messages').json() == []
            queued = client.post('/api/scans', json={'symbols':['NVDA'], 'strategies':['trend','rsi']})
            assert queued.status_code == 202
            created_scan = queued.json()['id']
            fetched = client.get(f'/api/scans/{created_scan}').json()
            assert fetched['request']['symbols'] == ['NVDA']
            assert fetched['status'] in ['queued','running','completed']
            assert client.post('/api/chat', json={'conversation_id': created_conversation, 'message': 'Explain', 'market': 'IN', 'symbols': ['TCS.NS'], 'scan_id': created_scan}).status_code == 422
            assert client.get('/api/scans/not-a-scan').status_code == 404
    finally:
        with Session.begin() as db:
            if created_scan:
                db.execute(delete(Scan).where(Scan.id == created_scan))
            if created_conversation:
                db.execute(delete(Message).where(Message.conversation_id == created_conversation))
                db.execute(delete(Conversation).where(Conversation.id == created_conversation))


def test_market_watchlists_persist_independently():
    keys = ['watchlist_IN', 'watchlist_US']
    with Session() as db:
        original = {key: db.get(Setting, key).value if db.get(Setting, key) else None for key in keys}
    try:
        with TestClient(app) as client:
            for market, symbol in [('IN', 'TCS.NS'), ('US', 'NVDA')]:
                assert client.put('/api/watchlist', json={'market': market, 'symbols': [symbol]}).status_code == 200
            assert client.get('/api/watchlist?market=IN').json()['symbols'] == ['TCS.NS']
            assert client.get('/api/watchlist?market=US').json()['symbols'] == ['NVDA']
            assert client.put('/api/watchlist', json={'market': 'IN', 'symbols': ['NVDA']}).status_code == 422
    finally:
        with Session.begin() as db:
            for key, value in original.items():
                db.execute(delete(Setting).where(Setting.key == key))
                if value is not None:
                    db.add(Setting(key=key, value=value))
