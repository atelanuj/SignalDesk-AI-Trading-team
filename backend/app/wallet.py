"""Virtual money only. Market-level locking serializes all cash mutations."""
from decimal import Decimal
from sqlalchemy import select, text

from .db import PaperCashMovement, PaperTrade, PaperWallet

STARTER_CASH = {'IN': Decimal('100000'), 'US': Decimal('10000')}


def locked_wallet(db, market):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'paper_wallet_' + market})
    wallet = db.scalar(select(PaperWallet).where(PaperWallet.market == market).with_for_update())
    if not wallet:
        starter = STARTER_CASH[market]
        wallet = PaperWallet(market=market, currency='INR' if market == 'IN' else 'USD', cash=starter,
                             starter_cash=starter, total_funding=starter, carry_in_funding=Decimal(0))
        db.add(wallet)
        db.flush()
        db.add(PaperCashMovement(market=market, kind='STARTER_FUNDS', amount=starter))
    # Preserve existing positions and give them matching virtual funding. Importing
    # history never consumes the user's new spendable starter balance.
    legacy = db.scalars(select(PaperTrade).where(PaperTrade.market == market, PaperTrade.status == 'OPEN', PaperTrade.wallet_market.is_(None)).with_for_update()).all()
    for trade in legacy:
        cost = trade.entry_price * trade.quantity + trade.entry_fees
        trade.wallet_market = market
        wallet.total_funding += cost
        wallet.carry_in_funding += cost
        db.add_all([
            PaperCashMovement(market=market, trade_id=trade.id, kind='CARRY_IN_FUNDS', amount=cost),
            PaperCashMovement(market=market, trade_id=trade.id, kind='CARRY_IN_BUY', amount=-cost),
        ])
    db.flush()
    return wallet


def wallet_dict(wallet):
    return {key: str(getattr(wallet, key)) for key in ('cash', 'starter_cash', 'total_funding', 'carry_in_funding')} | {
        'market': wallet.market, 'currency': wallet.currency, 'virtual_only': True,
    }
