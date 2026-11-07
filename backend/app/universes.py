"""Current NSE Indices constituent files; never substitute a partial watchlist."""
import csv
import io
import re
import time
from datetime import datetime, timezone
from threading import Lock

import httpx

INDEX_UNIVERSES = {
    'nifty50': ('NIFTY 50', 'ind_nifty50list.csv', 50),
    'nifty100': ('NIFTY 100', 'ind_nifty100list.csv', 100),
    'nifty_next50': ('NIFTY Next 50', 'ind_niftynext50list.csv', 50),
    'nifty200': ('NIFTY 200', 'ind_nifty200list.csv', 200),
    'nifty_bank': ('NIFTY Bank', 'ind_niftybanklist.csv', None),
}
_cache = {}
_lock = Lock()


def universe_options():
    return [{'id': key, 'name': value[0]} for key, value in INDEX_UNIVERSES.items()]


def parse_constituents(content, expected_count=None):
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    if not reader.fieldnames or 'Symbol' not in reader.fieldnames:
        raise ValueError('Index provider returned an invalid constituent file.')
    symbols = list(dict.fromkeys(row['Symbol'].strip().upper() for row in reader))
    if not symbols or len(symbols) > 200 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9&-]{0,19}', s) for s in symbols):
        raise ValueError('Index provider returned invalid constituent symbols.')
    if expected_count and len(symbols) != expected_count:
        raise ValueError('Index provider returned an incomplete constituent list. Please retry later.')
    return [s + '.NS' for s in symbols]


def get_universe(universe):
    if universe not in INDEX_UNIVERSES:
        raise ValueError('Unknown index universe.')
    with _lock:
        cached = _cache.get(universe)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        name, filename, expected = INDEX_UNIVERSES[universe]
        source = 'https://www.niftyindices.com/IndexConstituent/' + filename
        response = httpx.get(source, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        symbols = parse_constituents(response.text, expected)
        data = {'id': universe, 'name': name, 'symbols': symbols, 'count': len(symbols), 'source': source, 'fetched_at': datetime.now(timezone.utc).isoformat()}
        _cache[universe] = (time.monotonic() + 3600, data)
        return data
