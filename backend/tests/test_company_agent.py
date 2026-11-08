from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import Conversation, Message, Scan, Session
from app.main import app


@pytest.mark.parametrize('screen_symbol', [None, 'TCS.NS', 'RELIANCE.NS'])
def test_company_tool_routes_to_checked_research_without_borrowing_other_stock_signals(monkeypatch, company_sources, screen_symbol):
    captured = {}
    function = SimpleNamespace(name='company_research', arguments='{"symbols":["TCS.NS"]}')
    call = SimpleNamespace(id='company-call', function=function)
    choice = SimpleNamespace(tool_calls=[call], content=None, model_dump=lambda **kw: {'role': 'assistant', 'tool_calls': [{'id': 'company-call', 'type': 'function', 'function': {'name': function.name, 'arguments': function.arguments}}]})
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=choice)])))), close=AsyncMock())
    monkeypatch.setattr('app.agent.API_KEY', 'test-key')
    monkeypatch.setattr('app.agent.AsyncOpenAI', lambda **kw: fake_client)
    monkeypatch.setattr('app.agent.collect_evidence', lambda symbols, market: company_sources)
    async def evaluation(client, request, context, system, company_sources=None):
        captured.update(context=context, sources=company_sources)
        yield 'delta', {'text': 'Checked company research; no technical signal was calculated.'}
    monkeypatch.setattr('app.agent.stream_evaluation', evaluation)
    cid = None
    sid = None
    try:
        with TestClient(app) as client:
            cid = client.post('/api/conversations').json()['id']
            if screen_symbol:
                with Session.begin() as db:
                    scan = Scan(status='completed', request={'market': 'IN', 'symbols': [screen_symbol]}, result={'items': [{'symbol': screen_symbol, 'signal': 'HOLD', 'risk': {}, 'stale': False}]})
                    db.add(scan)
                    db.flush()
                    sid = scan.id
            response = client.post('/api/chat', json={'conversation_id': cid, 'market': 'IN', 'scan_id': sid, 'message': 'Check company finances and sentiment for TCS.NS'})
            assert response.status_code == 200 and 'Checked company research' in response.text
            expected_scan = sid if screen_symbol == 'TCS.NS' else None
            assert captured['context']['id'] == expected_scan
            assert captured['context']['result']['items'][0]['signal'] == ('HOLD' if expected_scan else 'NO_SCREEN')
            assert captured['sources']['companies'][0]['fundamentals']['status'] == 'ok'
            messages = client.get(f'/api/conversations/{cid}/messages').json()
            assert messages[-1]['scan_id'] == expected_scan
    finally:
        if cid:
            with Session.begin() as db:
                db.execute(delete(Message).where(Message.conversation_id == cid))
                db.execute(delete(Conversation).where(Conversation.id == cid))
                if sid:
                    db.execute(delete(Scan).where(Scan.id == sid))
