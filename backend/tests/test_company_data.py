from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import httpx
import pandas as pd
from sqlalchemy import delete

from app import company_data as data
from app.db import ResearchSourceCache, Session, init_db
from app.main import app
from app.schemas import CompanyResearchRequest

IDENTITY = {'symbol': 'TCS.NS', 'name': 'Tata Consultancy Services Limited'}
NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def test_similarly_named_listed_company_is_not_reliance_industries_sentiment():
    identity = {'symbol': 'RELIANCE.NS', 'name': 'Reliance Industries Limited'}
    assert not data.is_related('Reliance Power traders', identity)
    assert data.is_related('Reliance rally after results', identity)
    assert data.is_related('Compare Reliance Industries and Reliance Power', identity)


def test_news_feed_filters_unrelated_old_future_undated_and_unsafe_items():
    def item(title, url, date):
        return f'<item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate><source>Fixture News</source><description>Summary</description></item>'
    recent = 'Sun, 08 Nov 2026 11:00:00 GMT'
    xml = '<rss><channel>' + ''.join([
        item('TCS reports profits', 'https://news.google.com/articles/a', recent),
        item('TCS reports profits', 'https://news.google.com/articles/a', recent),
        item('Other company report', 'https://news.google.com/articles/b', recent),
        item('TCS old report', 'https://news.google.com/articles/c', 'Sun, 25 Oct 2026 11:00:00 GMT'),
        item('TCS future report', 'https://news.google.com/articles/d', 'Sun, 15 Nov 2026 11:00:00 GMT'),
        item('TCS undated', 'https://news.google.com/articles/e', ''),
        item('TCS unsafe', 'javascript:alert(1)', recent),
    ]) + '</channel></rss>'
    rows = data.recent_items(ET.fromstring(xml), IDENTITY, 'news', NOW)
    assert len(rows) == 1 and rows[0]['title'] == 'TCS reports profits'
    assert rows[0]['publisher'] == 'Fixture News'


def test_social_feed_requires_a_dated_relevant_reddit_post():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom">'
    for title, host, stamp in [('TCS discussion', 'www.reddit.com', '2026-11-07T10:00:00Z'), ('TCS unrelated host', 'example.com', '2026-11-07T10:00:00Z'), ('Unrelated company', 'www.reddit.com', '2026-11-07T10:00:00Z'), ('TCS undated', 'www.reddit.com', '')]:
        xml += f'<entry><title>{title}</title><link href="https://{host}/r/IndianStockMarket/comments/fixture/"/><published>{stamp}</published><content>Discussion excerpt</content></entry>'
    rows = data.recent_items(ET.fromstring(xml + '</feed>'), IDENTITY, 'social', NOW)
    assert len(rows) == 1 and rows[0]['title'] == 'TCS discussion'


def test_blocked_feed_is_unavailable_not_an_empty_or_neutral_result(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(data.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw))
    result = data.fetch_feed(IDENTITY, 'IN', 'social')
    assert result['status'] == 'unavailable' and result['items'] == []
    assert 'not neutral' in result['message']


def test_successful_empty_feed_is_distinct_from_a_provider_failure(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(data.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<rss><channel/></rss>')), **kw))
    assert data.fetch_feed(IDENTITY, 'IN', 'news')['status'] == 'empty'


def test_financial_snapshot_preserves_periods_missing_values_and_currencies(monkeypatch):
    frame = pd.DataFrame({pd.Timestamp('2026-03-31'): {'TotalRevenue': 1000, 'NetIncome': float('nan')}})
    ticker = SimpleNamespace(get_info=lambda: {'symbol': 'TCS.NS', 'quoteType': 'EQUITY', 'longName': IDENTITY['name'], 'financialCurrency': 'USD', 'currency': 'INR', 'totalRevenue': 1000, 'marketCap': 10000, 'profitMargins': .1}, get_income_stmt=lambda **kw: frame, get_balance_sheet=lambda **kw: pd.DataFrame(), get_cash_flow=lambda **kw: pd.DataFrame())
    monkeypatch.setattr(data, 'YahooSession', lambda **kw: nullcontext(object()))
    monkeypatch.setattr(data.yf, 'Ticker', lambda *args, **kw: ticker)
    result = data.fetch_fundamentals('TCS.NS')
    assert result['status'] == 'partial'
    assert result['metrics']['Revenue (provider trailing period)']['currency'] == 'USD'
    assert result['metrics']['Market value']['currency'] == 'INR'
    assert result['metrics']['Profit margin']['value'] == .1
    assert result['statements']['income'] == [{'period_end': '2026-03-31', 'values': {'Revenue': 1000.0}}]
    assert 'not verified publication' in result['message']


def test_mismatched_company_is_not_used_as_a_substitute(monkeypatch):
    monkeypatch.setattr(data, 'YahooSession', lambda **kw: nullcontext(object()))
    monkeypatch.setattr(data.yf, 'Ticker', lambda *args, **kw: SimpleNamespace(get_info=lambda: {'symbol': 'OTHER.NS', 'quoteType': 'EQUITY'}))
    result = data.fetch_fundamentals('TCS.NS')
    assert result['status'] == 'unavailable' and 'identity' not in result


def test_plain_text_and_links_do_not_preserve_executable_markup():
    assert data.plain('&lt;script&gt;bad()&lt;/script&gt;<b>TCS</b> earnings') == 'TCS earnings'
    assert data.safe_url('javascript:alert(1)') is None
    assert data.safe_url('https://user:pass@example.com') is None
    assert data.safe_url('http://localhost/private') is None
    assert data.numeric(float('nan')) is None
    assert data.numeric(True) is None


def test_source_cache_is_reused_and_does_not_silently_replace_failures():
    init_db()
    key = 'test:' + uuid4().hex
    calls = []
    def fetch():
        calls.append(True)
        return data.channel('unavailable', 'https://example.com', 'Blocked')
    try:
        assert data.cached(key, 900, fetch)['status'] == 'unavailable'
        assert data.cached(key, 900, fetch)['status'] == 'unavailable'
        assert len(calls) == 1
        with Session.begin() as db:
            db.get(ResearchSourceCache, key).fetched_at = data.utcnow() - timedelta(seconds=61)
        data.cached(key, 900, fetch)
        assert len(calls) == 2
    finally:
        with Session.begin() as db:
            db.execute(delete(ResearchSourceCache).where(ResearchSourceCache.key == key))


def test_collection_caps_full_company_checks_and_discloses_omissions(monkeypatch, company_sources):
    calls = []
    def collect(symbol, market):
        calls.append(symbol)
        return {**company_sources['companies'][0], 'symbol': symbol}
    monkeypatch.setattr(data, 'collect_company', collect)
    result = data.collect_evidence(['TCS.NS', 'INFY.NS', 'ITC.NS', 'SBIN.NS'], 'IN')
    assert len(calls) == 3 and result['omitted_symbols'] == ['SBIN.NS']


def test_company_endpoint_works_without_a_price_scan_and_validates_market(monkeypatch, company_sources):
    monkeypatch.setattr('app.main.collect_evidence', lambda symbols, market: company_sources)
    with TestClient(app) as client:
        response = client.get('/api/companies/TCS.NS/research?market=IN')
        assert response.status_code == 200
        assert response.json()['companies'][0]['fundamentals']['metrics']['Revenue']['value'] == 1000
        assert client.get('/api/companies/TCS.NS/research?market=US').status_code == 422
        assert client.get('/api/companies/TCS/research?market=IN').status_code == 422
    assert CompanyResearchRequest(market='IN', symbols=[' tcs.ns ']).symbols == ['TCS.NS']
