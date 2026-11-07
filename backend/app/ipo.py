"""Read-only Indian IPO feed. Missing or blocked provider data is never invented."""
from datetime import datetime, timezone
from threading import Lock
import time

import httpx

SOURCE = 'https://www.nseindia.com/market-data/all-upcoming-issues-ipo'
_cache = None
_expires = 0
_lock = Lock()


def normalize_issue(row, category):
    if not isinstance(row, dict):
        raise ValueError('Invalid NSE issue record')
    name = row.get('companyName') or row.get('company') or row.get('name')
    if not name:
        raise ValueError('NSE issue has no company name')
    return {
        'name': str(name), 'symbol': row.get('symbol'), 'category': category,
        'open_date': row.get('issueStartDate') or row.get('issueOpenDate'),
        'close_date': row.get('issueEndDate') or row.get('issueCloseDate'),
        'price_band': row.get('issuePrice') or row.get('priceBand'),
        'lot_size': row.get('marketLot') or row.get('lotSize'),
        'status': row.get('status') or category,
    }


def indian_ipos():
    global _cache, _expires
    with _lock:
        if _cache is not None and time.monotonic() < _expires:
            return _cache
        result = {'market': 'IN', 'currency': 'INR', 'source': SOURCE, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'items': [], 'errors': []}
        try:
            with httpx.Client(timeout=12, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': SOURCE}) as client:
                client.get(SOURCE).raise_for_status()
                for category, endpoint in [('Current', 'ipo-current-issue'), ('Upcoming', 'all-upcoming-issues?category=ipo')]:
                    try:
                        response = client.get('https://www.nseindia.com/api/' + endpoint)
                        response.raise_for_status()
                        rows = response.json()
                        if isinstance(rows, dict):
                            rows = rows.get('data')
                        if not isinstance(rows, list):
                            raise ValueError('Unexpected NSE response')
                        result['items'].extend(normalize_issue(row, category) for row in rows)
                    except (httpx.HTTPError, ValueError, TypeError):
                        result['errors'].append(f'{category} IPO data is unavailable from NSE. Verify details on the exchange website.')
        except httpx.HTTPError:
            result['errors'].append('NSE IPO feed is unavailable. The exchange may block automated requests; use the official source links.')
        result['status'] = 'partial' if result['errors'] and result['items'] else 'unavailable' if result['errors'] else 'ok'
        _cache, _expires = result, time.monotonic() + (60 if result['errors'] else 900)
        return result
