import pytest


@pytest.fixture
def company_sources():
    finance = {'status': 'ok', 'source': 'https://finance.yahoo.com/quote/TCS.NS/financials/', 'fetched_at': '2026-11-08T00:00:00+00:00', 'message': 'Current snapshot, not point-in-time.', 'financial_currency': 'INR', 'identity': {'symbol': 'TCS.NS', 'name': 'Tata Consultancy Services Limited'}, 'metrics': {'Revenue': {'value': 1000, 'unit': 'currency', 'currency': 'INR'}}, 'statements': {'income': [{'period_end': '2026-03-31', 'values': {'Revenue': 1000}}]}, 'sources': {'income': 'https://finance.yahoo.com/quote/TCS.NS/financials/'}}
    row = {'title': 'TCS reports revenue', 'excerpt': 'Company report summary.', 'url': 'https://news.google.com/rss/articles/fixture', 'published_at': '2026-11-07T12:00:00+00:00', 'publisher': 'Fixture News'}
    news = {'status': 'ok', 'source': 'https://news.google.com/rss/search', 'fetched_at': finance['fetched_at'], 'message': 'Headline summaries only.', 'items': [row]}
    social = {'status': 'ok', 'source': 'https://www.reddit.com/search.rss', 'fetched_at': finance['fetched_at'], 'message': 'Limited public post sample.', 'items': [{**row, 'title': 'TCS discussion', 'url': 'https://www.reddit.com/r/IndianStockMarket/comments/fixture/', 'publisher': 'Reddit', 'forum': 'IndianStockMarket'}]}
    return {'companies': [{'symbol': 'TCS.NS', 'name': 'Tata Consultancy Services Limited', 'fundamentals': finance, 'news': news, 'social': social}], 'fetched_at': finance['fetched_at'], 'requested_symbols': ['TCS.NS'], 'omitted_symbols': [], 'max_companies': 3, 'window_days': 7, 'market': 'IN', 'note': 'Test fixture'}

@pytest.fixture(autouse=True)
def avoid_live_backtests_in_research_unit_tests(monkeypatch):
    monkeypatch.setattr('app.research.collect_backtests', lambda symbols, strategies: {'items':[{'symbol':s,'status':'unavailable','message':'Unit fixture: no live provider'} for s in symbols[:3]],'omitted_symbols':symbols[3:],'note':'Unit fixture'})
