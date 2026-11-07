from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import tempfile

import yfinance as yf

from .db import PriceCache, Session
from .strategies import analyze

logger = logging.getLogger(__name__)

# Avoid concurrent first-use directory creation in yfinance's local timezone cache.
cache_dir = Path(tempfile.gettempdir()) / 'signaldesk-yfinance'
cache_dir.mkdir(parents=True, exist_ok=True)
yf.set_tz_cache_location(str(cache_dir))


def history(symbol, refresh=False):
    with Session() as db:
        cached = db.get(PriceCache, symbol)
        if cached and not refresh and datetime.now(timezone.utc) - cached.fetched_at < timedelta(minutes=15):
            return cached.data
    # auto_adjust makes indicators consistent across splits and dividends.
    frame = yf.Ticker(symbol).history(period='2y', interval='1d', auto_adjust=True, raise_errors=True, timeout=12)
    if frame.empty:
        raise ValueError('No daily price history returned by the data provider.')
    exchange_today = datetime.now(frame.index.tz).date() if frame.index.tz else datetime.now(timezone.utc).date()
    # Always exclude the exchange's current calendar day to avoid partial bars.
    frame = frame[[stamp.date() < exchange_today for stamp in frame.index]].dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
    bars = [{'date': stamp.date().isoformat(), 'open': float(row.Open), 'high': float(row.High), 'low': float(row.Low), 'close': float(row.Close), 'volume': float(row.Volume)} for stamp, row in frame.iterrows()]
    fetched = datetime.now(timezone.utc)
    data = {'bars': bars, 'currency': 'INR' if symbol.endswith(('.NS', '.BO')) else 'USD', 'fetched_at': fetched.isoformat()}
    with Session() as db:
        cached = db.get(PriceCache, symbol)
        if cached:
            cached.data, cached.fetched_at = data, fetched
        else:
            db.add(PriceCache(symbol=symbol, data=data, fetched_at=fetched))
        db.commit()
    return data


def screen(request):
    def one(symbol):
        try:
            data = history(symbol, request.refresh)
            return {'item': analyze(symbol, data['bars'], request.strategies, currency=data['currency'], fetched_at=data['fetched_at'])}
        except Exception:
            logger.warning('Market data unavailable for %s', symbol)
            return {'error': {'symbol': symbol, 'message': 'Price history unavailable or insufficient. Check the ticker and try again; the provider may be rate limited.'}}

    with ThreadPoolExecutor(max_workers=4 if request.universe != 'watchlist' else 2) as pool:
        outputs = list(pool.map(one, request.symbols))
    items = [o['item'] for o in outputs if 'item' in o]
    items.sort(key=lambda x: (-x['score'], x['symbol']))
    return {
        'items': items, 'errors': [o['error'] for o in outputs if 'error' in o],
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'strategies': request.strategies,
        'counts': {s: sum(x['signal'] == s for x in items) for s in ['BUY', 'SELL', 'HOLD']},
        'method': 'Equal-weight votes: BUY at score >= 0.4, SELL at <= -0.4, otherwise HOLD. Agreement is the share of selected strategies matching the final signal, not a probability of profit.',
        'data_note': 'Adjusted, completed daily bars from Yahoo Finance via yfinance. Current exchange day excluded. Prices can be delayed; coverage is limited to the selected watchlist or index constituents.',
    }
