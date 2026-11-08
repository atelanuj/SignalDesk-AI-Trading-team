"""Discovery ranks only actual completed screens, never an invented recommendation."""
from .schemas import ScanRequest
from .universes import get_universe
from .config import US_SYMBOLS


def discovery_request(market, strategies):
    if market == 'IN':
        u = get_universe('nifty50')
        return ScanRequest(market=market, universe='nifty50', symbols=u['symbols'], strategies=strategies, universe_source=u['source'], universe_fetched_at=u['fetched_at'])
    return ScanRequest(market=market, symbols=US_SYMBOLS, strategies=strategies)


def rank_discovery(result, mode, market):
    items = list(result.get('items', []))
    fresh = [i for i in items if not i.get('stale')]
    if mode == 'trending':
        fresh.sort(key=lambda i: (i['price'] > i['indicators']['sma50'], i['indicators']['volume_ratio'], i['change_pct']), reverse=True)
    else:
        fresh.sort(key=lambda i: (-i['score'], i['risk']['atr_pct'], i['symbol']))
    return {**result, 'items':fresh + [i for i in items if i.get('stale')], 'discovery':{
        'mode':mode, 'universe':'Current NIFTY 50' if market == 'IN' else 'Configured US leaders sample',
        'evaluated':len(items), 'failed':len(result.get('errors', [])), 'fresh':len(fresh),
        'ranking':'Above 50-session average first, then relative daily volume, then daily change' if mode == 'trending' else 'Higher rule score first, then lower ATR percentage (historical daily price swings)',
        'note':'Research candidates only, not a safest/best stock or a prediction. Trending means completed-day price/volume activity, not social-media popularity. This is not an exchange-wide search. No fresh candidates means wait.'}}
