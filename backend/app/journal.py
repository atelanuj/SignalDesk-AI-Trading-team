"""Manual, long-only simulated trades backed by separate virtual-cash wallets."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, text

from .db import PaperTrade, PriceCache, Scan, Session, PaperCashMovement, now
from .schemas import ClosePaperTradeRequest, PaperTradeRequest, ScanRequest, PaperTradeLevelsRequest, PaperTopUpRequest, validate_paper_levels
from .wallet import locked_wallet, wallet_dict

router = APIRouter(prefix='/api/journal', tags=['Paper trading'])


def money(value):
    return format(Decimal(value).quantize(Decimal('0.01')), 'f')


def trade_dict(trade, cached=None):
    snapshot = trade.signal_snapshot
    mark, mark_date = Decimal(str(snapshot['price'])), snapshot['as_of']
    if cached and cached.data.get('bars'):
        bar = cached.data['bars'][-1]
        if bar['date'] >= mark_date:
            mark, mark_date = Decimal(str(bar['close'])), bar['date']
    cost = trade.entry_price * trade.quantity + trade.entry_fees
    closed = trade.status == 'CLOSED'
    value = (trade.exit_price if closed else mark) * trade.quantity
    pnl = value - cost - (trade.exit_fees if closed else 0)
    return {
        'id': trade.id, 'market': trade.market, 'symbol': trade.symbol, 'currency': trade.currency,
        'status': trade.status, 'quantity': trade.quantity, 'entry_price': str(trade.entry_price),
        'entry_fees': str(trade.entry_fees), 'exit_price': str(trade.exit_price) if closed else None,
        'stop_loss': str(trade.stop_loss) if trade.stop_loss is not None else None,
        'target_price': str(trade.target_price) if trade.target_price is not None else None,
        'levels_mode': 'manual',
        'wallet_funded': trade.wallet_market is not None,
        'exit_fees': str(trade.exit_fees), 'opened_at': trade.opened_at.isoformat(),
        'closed_at': trade.closed_at.isoformat() if trade.closed_at else None,
        'notes': trade.notes, 'reflection': trade.reflection, 'scan_id': trade.scan_id,
        'signal_snapshot': snapshot, 'entry_cost': money(cost), 'mark_price': str(mark),
        'mark_date': mark_date, 'pnl': money(pnl), 'pnl_pct': money(pnl / cost * 100),
        'pnl_kind': 'realized' if closed else 'unrealized',
    }


@router.get('')
def list_trades(market: str = Query('IN', pattern='^(IN|US)$')):
    with Session.begin() as db:
        wallet = locked_wallet(db, market)
        trades = db.scalars(select(PaperTrade).where(PaperTrade.market == market).order_by(PaperTrade.opened_at.desc())).all()
        symbols = {t.symbol for t in trades if t.status == 'OPEN'}
        prices = {p.symbol: p for p in db.scalars(select(PriceCache).where(PriceCache.symbol.in_(symbols)))} if symbols else {}
        rows = [trade_dict(t, prices.get(t.symbol)) for t in trades]
        holdings = sum((Decimal(r['mark_price']) * r['quantity'] for r in rows if r['status'] == 'OPEN' and r['wallet_funded']), Decimal(0))
        account = wallet_dict(wallet) | {'holdings_value': money(holdings), 'equity': money(wallet.cash + holdings), 'net_pnl': money(wallet.cash + holdings - wallet.total_funding)}
    closed = [r for r in rows if r['status'] == 'CLOSED']
    return {'trades': rows, 'wallet': account, 'summary': {
        'currency': 'INR' if market == 'IN' else 'USD',
        'open_count': len(rows) - len(closed), 'closed_count': len(closed),
        'realized_pnl': money(sum((Decimal(r['pnl']) for r in closed), Decimal(0))),
        'unrealized_pnl': money(sum((Decimal(r['pnl']) for r in rows if r['status'] == 'OPEN'), Decimal(0))),
    }}


@router.get('/wallet')
def get_wallet(market: str = Query('IN', pattern='^(IN|US)$')):
    with Session.begin() as db:
        return wallet_dict(locked_wallet(db, market))


@router.post('/wallet/top-up')
def top_up_wallet(request: PaperTopUpRequest):
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:token))'), {'token': str(request.client_token)})
        wallet = locked_wallet(db, request.market)
        previous = db.get(PaperCashMovement, str(request.client_token))
        if previous:
            if previous.kind != 'TOP_UP' or previous.market != request.market or previous.amount != request.amount:
                raise HTTPException(409, 'This add-money request was already used. Open the form again.')
            return wallet_dict(wallet)
        wallet.cash += request.amount
        wallet.total_funding += request.amount
        db.add(PaperCashMovement(id=str(request.client_token), market=request.market, kind='TOP_UP', amount=request.amount))
        db.flush()
        return wallet_dict(wallet)


@router.post('', status_code=201)
def open_trade(request: PaperTradeRequest):
    with Session.begin() as db:
        # Serialise a repeated client token without placing duplicate simulated orders.
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:token))'), {'token': str(request.client_token)})
        if db.get(PaperTrade, str(request.client_token)):
            raise HTTPException(409, 'This paper trade has already been recorded. Refresh the journal.')
        scan = db.get(Scan, str(request.scan_id)) if request.scan_id else None
        if request.scan_id and (not scan or scan.status != 'completed'):
            raise HTTPException(422, 'Choose a stock from a completed screen before paper trading.')
        try:
            scan_market = ScanRequest(**scan.request).market if scan else request.market
        except ValueError:
            raise HTTPException(422, 'Run a new market-specific screen before paper trading this legacy result.')
        if scan_market != request.market:
            raise HTTPException(422, 'The selected screen belongs to another market.')
        item = next((i for i in (scan.result or {}).get('items', []) if i['symbol'] == request.symbol), None) if scan else {
            'symbol': request.symbol, 'currency': 'INR' if request.market == 'IN' else 'USD',
            'price': float(request.entry_price), 'as_of': now().date().isoformat(),
            'signal': 'MANUAL', 'source': 'User-entered simulated fill, not a market quote',
            'stale': False, 'manual': True, 'risk': {},
        }
        if not item or item.get('currency') != ('INR' if request.market == 'IN' else 'USD'):
            raise HTTPException(422, 'Stock or currency does not match the selected screen.')
        wallet = locked_wallet(db, request.market)
        cost = request.entry_price * request.quantity + request.entry_fees
        if cost > wallet.cash:
            raise HTTPException(422, f'Not enough practice money. This trade costs {money(cost)} {wallet.currency}, but you have {money(wallet.cash)}. Reduce the quantity or add practice money in the journal.')
        trade = PaperTrade(
            id=str(request.client_token), market=request.market, symbol=request.symbol,
            currency=item['currency'], quantity=request.quantity, entry_price=request.entry_price,
            entry_fees=request.entry_fees, notes=request.notes.strip(), scan_id=scan.id if scan else None,
            stop_loss=request.stop_loss, target_price=request.target_price,
            wallet_market=request.market,
            signal_snapshot={k: v for k, v in item.items() if k != 'chart'},
        )
        db.add(trade)
        db.flush()
        wallet.cash -= cost
        db.add(PaperCashMovement(market=request.market, trade_id=trade.id, kind='BUY', amount=-cost))
        db.flush()
        return trade_dict(trade) | {'wallet': wallet_dict(wallet)}


@router.put('/{trade_id}/levels')
def update_levels(trade_id: UUID, request: PaperTradeLevelsRequest):
    with Session.begin() as db:
        trade = db.scalar(select(PaperTrade).where(PaperTrade.id == str(trade_id)).with_for_update())
        if not trade:
            raise HTTPException(404, 'Paper trade not found.')
        if trade.status != 'OPEN':
            raise HTTPException(409, 'Targets and stop losses cannot be changed after a paper position is closed.')
        try:
            validate_paper_levels(trade.entry_price, request.stop_loss, request.target_price)
        except ValueError as error:
            raise HTTPException(422, str(error))
        trade.stop_loss, trade.target_price = request.stop_loss, request.target_price
        db.flush()
        return trade_dict(trade)


@router.post('/{trade_id}/close')
def close_trade(trade_id: UUID, request: ClosePaperTradeRequest):
    with Session.begin() as db:
        initial = db.execute(select(PaperTrade.market, PaperTrade.status).where(PaperTrade.id == str(trade_id))).one_or_none()
        if not initial:
            raise HTTPException(404, 'Paper trade not found.')
        if initial.status != 'OPEN':
            raise HTTPException(409, 'This paper trade is already closed.')
        # Every money mutation takes the wallet lock before a trade row lock.
        wallet = locked_wallet(db, initial.market)
        trade = db.scalar(select(PaperTrade).where(PaperTrade.id == str(trade_id)).with_for_update().execution_options(populate_existing=True))
        if trade.status != 'OPEN':
            raise HTTPException(409, 'This paper trade is already closed.')
        proceeds = request.exit_price * trade.quantity - request.exit_fees
        if wallet.cash + proceeds < 0:
            raise HTTPException(422, 'Exit fees exceed the sale proceeds and your available practice cash. Check the fees or add practice money first.')
        trade.status, trade.closed_at = 'CLOSED', now()
        trade.exit_price, trade.exit_fees = request.exit_price, request.exit_fees
        trade.reflection = request.reflection.strip()
        wallet.cash += proceeds
        db.add(PaperCashMovement(market=trade.market, trade_id=trade.id, kind='SELL', amount=proceeds))
        db.flush()
        return trade_dict(trade) | {'wallet': wallet_dict(wallet)}
