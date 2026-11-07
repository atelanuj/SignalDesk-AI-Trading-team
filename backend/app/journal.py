"""Manual, long-only simulated trades. No broker, cash account or automatic fills."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from .db import PaperTrade, PriceCache, Scan, Session, now
from .schemas import ClosePaperTradeRequest, PaperTradeRequest, ScanRequest, PaperTradeLevelsRequest, validate_paper_levels

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
        'exit_fees': str(trade.exit_fees), 'opened_at': trade.opened_at.isoformat(),
        'closed_at': trade.closed_at.isoformat() if trade.closed_at else None,
        'notes': trade.notes, 'reflection': trade.reflection, 'scan_id': trade.scan_id,
        'signal_snapshot': snapshot, 'entry_cost': money(cost), 'mark_price': str(mark),
        'mark_date': mark_date, 'pnl': money(pnl), 'pnl_pct': money(pnl / cost * 100),
        'pnl_kind': 'realized' if closed else 'unrealized',
    }


@router.get('')
def list_trades(market: str = Query('IN', pattern='^(IN|US)$')):
    with Session() as db:
        trades = db.scalars(select(PaperTrade).where(PaperTrade.market == market).order_by(PaperTrade.opened_at.desc())).all()
        symbols = {t.symbol for t in trades if t.status == 'OPEN'}
        prices = {p.symbol: p for p in db.scalars(select(PriceCache).where(PriceCache.symbol.in_(symbols)))} if symbols else {}
        rows = [trade_dict(t, prices.get(t.symbol)) for t in trades]
    closed = [r for r in rows if r['status'] == 'CLOSED']
    return {'trades': rows, 'summary': {
        'currency': 'INR' if market == 'IN' else 'USD',
        'open_count': len(rows) - len(closed), 'closed_count': len(closed),
        'realized_pnl': money(sum((Decimal(r['pnl']) for r in closed), Decimal(0))),
        'unrealized_pnl': money(sum((Decimal(r['pnl']) for r in rows if r['status'] == 'OPEN'), Decimal(0))),
    }}


@router.post('', status_code=201)
def open_trade(request: PaperTradeRequest):
    with Session.begin() as db:
        # Serialise a repeated client token without placing duplicate simulated orders.
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:token))'), {'token': str(request.client_token)})
        if db.get(PaperTrade, str(request.client_token)):
            raise HTTPException(409, 'This paper trade has already been recorded. Refresh the journal.')
        scan = db.get(Scan, str(request.scan_id))
        if not scan or scan.status != 'completed':
            raise HTTPException(422, 'Choose a stock from a completed screen before paper trading.')
        try:
            scan_market = ScanRequest(**scan.request).market
        except ValueError:
            raise HTTPException(422, 'Run a new market-specific screen before paper trading this legacy result.')
        if scan_market != request.market:
            raise HTTPException(422, 'The selected screen belongs to another market.')
        item = next((i for i in (scan.result or {}).get('items', []) if i['symbol'] == request.symbol), None)
        if not item or item.get('currency') != ('INR' if request.market == 'IN' else 'USD'):
            raise HTTPException(422, 'Stock or currency does not match the selected screen.')
        trade = PaperTrade(
            id=str(request.client_token), market=request.market, symbol=request.symbol,
            currency=item['currency'], quantity=request.quantity, entry_price=request.entry_price,
            entry_fees=request.entry_fees, notes=request.notes.strip(), scan_id=scan.id,
            stop_loss=request.stop_loss, target_price=request.target_price,
            signal_snapshot={k: v for k, v in item.items() if k != 'chart'},
        )
        db.add(trade)
        db.flush()
        return trade_dict(trade)


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
        trade = db.scalar(select(PaperTrade).where(PaperTrade.id == str(trade_id)).with_for_update())
        if not trade:
            raise HTTPException(404, 'Paper trade not found.')
        if trade.status != 'OPEN':
            raise HTTPException(409, 'This paper trade is already closed.')
        trade.status, trade.closed_at = 'CLOSED', now()
        trade.exit_price, trade.exit_fees = request.exit_price, request.exit_fees
        trade.reflection = request.reflection.strip()
        db.flush()
        return trade_dict(trade)
