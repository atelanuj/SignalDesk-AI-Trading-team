from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from app.db import PaperCashMovement, PaperTrade, PaperWallet, Scan, Session, init_db
from app.main import app


@pytest.fixture
def wallet_scan():
    if os.getenv('TEST_ISOLATED') != '1':
        pytest.skip('Wallet checks require python tests/run_isolated.py to protect user funds.')
    init_db()
    with Session.begin() as db:
        assert not db.scalar(select(PaperTrade).limit(1)), 'Wallet tests require a clean isolated journal.'
        db.execute(delete(PaperCashMovement))
        db.execute(delete(PaperWallet))
        item = {'symbol': 'WALLETTEST.NS', 'currency': 'INR', 'price': 100, 'as_of': '2026-11-06', 'signal': 'HOLD', 'stale': False, 'source': 'Test fixture', 'risk': {'stop_reference': None, 'target_reference': None}}
        scan = Scan(status='completed', request={'market': 'IN', 'symbols': [item['symbol']]}, result={'items': [item]})
        db.add(scan)
        db.flush()
        sid = scan.id
    yield sid, item
    with Session.begin() as db:
        db.execute(delete(PaperCashMovement))
        db.execute(delete(PaperTrade).where(PaperTrade.scan_id == sid))
        db.execute(delete(PaperWallet))
        db.execute(delete(Scan).where(Scan.id == sid))


def entry(sid, **extra):
    return {'client_token': str(uuid4()), 'scan_id': sid, 'market': 'IN', 'symbol': 'WALLETTEST.NS', 'quantity': 10, 'entry_price': '100', 'entry_fees': '2.50', **extra}


def cash(client, market='IN'):
    return Decimal(client.get('/api/journal/wallet', params={'market': market}).json()['cash'])


def test_wallets_start_once_persist_and_keep_currencies_separate(wallet_scan):
    with TestClient(app) as client:
        assert cash(client) == Decimal('100000')
        assert cash(client) == Decimal('100000')
        assert cash(client, 'US') == Decimal('10000')
        body = {'market': 'IN', 'client_token': str(uuid4()), 'amount': '500.25'}
        assert client.post('/api/journal/wallet/top-up', json=body).status_code == 200
        assert client.post('/api/journal/wallet/top-up', json=body).status_code == 200
        assert cash(client) == Decimal('100500.25')
        assert cash(client, 'US') == Decimal('10000')
        assert client.post('/api/journal/wallet/top-up', json={**body, 'amount': '600'}).status_code == 409
    with TestClient(app) as restarted:
        assert cash(restarted) == Decimal('100500.25')


def test_buy_debits_cash_and_close_returns_proceeds_after_fees_once(wallet_scan):
    sid, _ = wallet_scan
    with TestClient(app) as client:
        body = entry(sid)
        opened = client.post('/api/journal', json=body)
        assert opened.status_code == 201
        trade = opened.json()
        assert trade['wallet_funded'] is True
        assert cash(client) == Decimal('98997.5')
        assert client.post('/api/journal', json=body).status_code == 409
        assert cash(client) == Decimal('98997.5')
        result = client.post(f"/api/journal/{trade['id']}/close", json={'exit_price': '110', 'exit_fees': '1.25'})
        assert result.status_code == 200 and result.json()['pnl'] == '96.25'
        assert cash(client) == Decimal('100096.25')
        assert client.post(f"/api/journal/{trade['id']}/close", json={'exit_price': '110'}).status_code == 409
        assert cash(client) == Decimal('100096.25')
        summary = client.get('/api/journal').json()['wallet']
        assert summary['equity'] == '100096.25' and summary['net_pnl'] == '96.25'


def test_insufficient_funds_and_excessive_exit_fees_cannot_overdraw(wallet_scan):
    sid, _ = wallet_scan
    with TestClient(app) as client:
        assert client.post('/api/journal', json=entry(sid, quantity=1001)).status_code == 422
        assert cash(client) == Decimal('100000')
        opened = client.post('/api/journal', json=entry(sid, quantity=1000, entry_fees='0')).json()
        assert cash(client) == Decimal(0)
        assert client.post(f"/api/journal/{opened['id']}/close", json={'exit_price': '1', 'exit_fees': '1001'}).status_code == 422
        assert cash(client) == Decimal(0)
        assert client.post(f"/api/journal/{opened['id']}/close", json={'exit_price': '1', 'exit_fees': '1'}).status_code == 200
        assert cash(client) == Decimal('999')


def test_concurrent_buys_share_one_available_balance(wallet_scan):
    sid, _ = wallet_scan
    with TestClient(app) as client:
        def buy(_):
            return client.post('/api/journal', json=entry(sid, quantity=600, entry_fees='0')).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(buy, range(2)))
        assert sorted(statuses) == [201, 422]
        assert cash(client) == Decimal('40000')


def test_existing_open_positions_get_matching_funding_without_spending_starter_cash(wallet_scan):
    sid, snapshot = wallet_scan
    with Session.begin() as db:
        old = PaperTrade(market='IN', symbol=snapshot['symbol'], currency='INR', quantity=2000, entry_price=Decimal('100'), scan_id=sid, signal_snapshot=snapshot)
        db.add(old)
        db.flush()
        tid = old.id
    with TestClient(app) as client:
        data = client.get('/api/journal').json()
        wallet = data['wallet']
        assert Decimal(wallet['cash']) == Decimal('100000')
        assert Decimal(wallet['carry_in_funding']) == Decimal('200000')
        assert Decimal(wallet['total_funding']) == Decimal('300000')
        assert wallet['equity'] == '300000.00'
        assert data['trades'][0]['wallet_funded'] is True
        assert cash(client) == Decimal('100000')
        assert client.post(f'/api/journal/{tid}/close', json={'exit_price': '110', 'exit_fees': '5'}).status_code == 200
        assert cash(client) == Decimal('319995')
    with Session() as db:
        moves = db.scalars(select(PaperCashMovement).where(PaperCashMovement.trade_id == tid)).all()
        assert sorted(m.kind for m in moves) == ['CARRY_IN_BUY', 'CARRY_IN_FUNDS', 'SELL']


@pytest.mark.parametrize('amount', ['0', '-1', 'NaN', '10000001', '1.001'])
def test_invalid_top_ups_are_rejected(wallet_scan, amount):
    with TestClient(app) as client:
        response = client.post('/api/journal/wallet/top-up', json={'market': 'IN', 'client_token': str(uuid4()), 'amount': amount})
        assert response.status_code == 422
        assert cash(client) == Decimal('100000')


def test_manual_entry_without_scan_keeps_wallet_levels_and_duplicate_guards(wallet_scan):
    token=str(uuid4())
    try:
        with TestClient(app) as client:
            body=entry(None,client_token=token,stop_loss='90',target_price='120')
            opened=client.post('/api/journal',json=body)
            assert opened.status_code==201
            row=opened.json()
            assert row['scan_id'] is None and row['signal_snapshot']['signal']=='MANUAL'
            assert row['signal_snapshot']['source']=='User-entered simulated fill, not a market quote'
            assert Decimal(row['stop_loss'])==90 and Decimal(row['target_price'])==120
            assert cash(client)==Decimal('98997.50')
            assert client.post('/api/journal',json=body).status_code==409
            assert cash(client)==Decimal('98997.50')
            journal=client.get('/api/journal?market=IN').json()
            assert len(journal['trades'])==1 and journal['trades'][0]['pnl']=='-2.50'
            closed=client.post(f'/api/journal/{token}/close',json={'exit_price':'110','exit_fees':'1'})
            assert closed.status_code==200 and cash(client)==Decimal('100096.50')
    finally:
        with Session.begin() as db:
            db.execute(delete(PaperCashMovement).where(PaperCashMovement.trade_id==token))
            db.execute(delete(PaperTrade).where(PaperTrade.id==token))


def test_manual_invalid_levels_market_and_unaffordable_entry_do_not_spend(wallet_scan):
    with TestClient(app) as client:
        for extra in [{'stop_loss':'101'},{'target_price':'99'},{'symbol':'AAPL'},{'quantity':100000}]:
            assert client.post('/api/journal',json=entry(None,**extra)).status_code==422
        assert cash(client)==Decimal('100000')
