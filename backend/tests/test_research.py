import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import Conversation, ResearchRun, Scan, Session, init_db
from app.research import ROLES, RESEARCH_ROLES, focused_evidence, stream_evaluation, computed_summary, analyst_input, source_coverage, source_footer
from app.agent import compact_scan
from app.language import BEGINNER_GUIDANCE


def context(scan_id='scan'):
    return {'id': scan_id, 'status': 'completed', 'result': {'items': [{'symbol': 'TCS.NS', 'signal': 'HOLD', 'price': 100, 'as_of': '2026-11-06', 'stale': False}, {'symbol': 'RELIANCE.NS', 'signal': 'BUY', 'price': 200, 'as_of': '2026-11-06', 'stale': True}], 'counts': {'BUY': 1, 'SELL': 0, 'HOLD': 1}}}


def test_evaluation_focus_and_stale_signal_gate():
    result = focused_evidence(context(), 'Compare RELIANCE.NS and its risks')
    assert [i['symbol'] for i in result['items']] == ['RELIANCE.NS']
    assert result['risk_gates'][0]['defer_new_long'] is True
    assert result['omitted_items'] == 1
    assert focused_evidence(context(), 'Explain TCS')['items'][0]['symbol'] == 'TCS.NS'


def test_exact_signal_summary_and_atr_ratio_are_generated_in_python():
    data = context()
    data['result']['items'][0].update({'risk': {'stop_reference': 96, 'target_reference': 108}, 'score': 0, 'agreement_pct': 100})
    evidence = focused_evidence(data, 'TCS.NS')
    assert evidence['atr_reward_risk']['TCS.NS'] == 2
    assert '| TCS.NS | HOLD | 0 | 100% |' in computed_summary(evidence, detailed=True)
    assert 'manually' in evidence['gate_scope']


def test_large_index_is_bounded_before_tool_routing_without_losing_named_stocks():
    data = context()['result']
    data['items'] = [{'symbol': f'TICKER{i}.NS', 'signal': 'HOLD', 'stale': False, 'chart': [{'close': 100}]} for i in range(200)]
    scan = SimpleNamespace(id='scan', status='completed', result=data)
    compact = compact_scan(scan, 'Evaluate the index')
    assert len(compact['result']['items']) == 20
    assert compact['result']['total_items'] == 200
    assert 'chart' not in compact['result']['items'][0]
    named = compact_scan(scan, 'Evaluate TICKER199.NS')
    assert [i['symbol'] for i in named['result']['items']] == ['TICKER199.NS']
    assert focused_evidence(named, 'Evaluate TICKER199.NS')['omitted_items'] == 199


@pytest.mark.parametrize('fail_at', [None, 3])
def test_roles_are_separate_calls_pass_reports_forward_and_persist(fail_at, company_sources, monkeypatch):
    monkeypatch.setattr('app.research.collect_evidence', lambda symbols, market: company_sources)
    init_db()
    with Session.begin() as db:
        conversation = Conversation()
        scan = Scan(status='completed', request={'market': 'IN', 'symbols': ['TCS.NS']}, result=context()['result'])
        db.add_all([conversation, scan])
        db.flush()
        cid, sid = conversation.id, scan.id
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        if fail_at and len(calls) == fail_at:
            raise RuntimeError('Provider unavailable')
        if kwargs.get('stream'):
            async def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='Final technical review: HOLD.'))])
            return chunks()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f'Public report {len(calls)}'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    request = SimpleNamespace(conversation_id=cid, market='IN', message='Evaluate TCS.NS')
    async def collect():
        return [event async for event in stream_evaluation(client, request, context(sid), 'Use only supplied technical evidence.')]
    try:
        if fail_at:
            with pytest.raises(RuntimeError):
                asyncio.run(collect())
        else:
            events = asyncio.run(collect())
            assert len(calls) == len(ROLES) + 1
            assert all(BEGINNER_GUIDANCE in call['messages'][0]['content'] for call in calls)
            assert [e[0] for e in events].count('stage') >= len(RESEARCH_ROLES) + 2
            assert any(e[0] == 'delta' for e in events)
            assert 'Public report 2' in calls[4]['messages'][1]['content']
            assert 'Public report 5' in calls[5]['messages'][1]['content']
            assert 'Company report summary.' in calls[4]['messages'][1]['content']
            assert 'Company report summary.' in calls[-1]['messages'][1]['content']
            assert 'Evaluate TCS.NS' not in calls[1]['messages'][1]['content']
            assert 'ONLY this role report' in calls[1]['messages'][0]['content']
        with Session() as db:
            run = db.scalar(select(ResearchRun).where(ResearchRun.conversation_id == cid))
            assert run.status == ('incomplete' if fail_at else 'completed')
            assert ('portfolio_reviewer' in run.reports) == (fail_at is None)
            if not fail_at:
                assert all(role[0] in run.reports for role in ROLES)
                assert run.reports['source_coverage'][0]['sentiment_basis'] == 'social_posts_and_headlines'
                assert run.reports['sources']['companies'][0]['fundamentals']['metrics']['Revenue']['value'] == 1000
    finally:
        with Session.begin() as db:
            db.execute(delete(ResearchRun).where(ResearchRun.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
            db.execute(delete(Scan).where(Scan.id == sid))


def test_headline_tone_is_distinct_from_missing_social_sentiment(company_sources):
    company_sources['companies'][0]['social'].update(status='unavailable', items=[])
    coverage = source_coverage(company_sources)[0]
    assert coverage['social'] == 'unavailable' and coverage['post_count'] == 0
    assert coverage['sentiment_basis'] == 'headlines_only'
    _, available = analyst_input('sentiment_analyst', {'technical_available': True}, company_sources)
    assert available is True
    company_sources['companies'][0]['news'].update(status='empty', items=[])
    _, available = analyst_input('sentiment_analyst', {'technical_available': True}, company_sources)
    assert available is False


def test_source_footer_uses_retrieved_links_dates_and_distinguishes_missing_posts(company_sources):
    company_sources['companies'][0]['social'].update(status='unavailable', items=[])
    company_sources['omitted_symbols'] = ['INFY.NS']
    footer = source_footer(company_sources)
    assert 'public posts unavailable (0 posts)' in footer
    assert '[Headline: 2026-11-07](https://news.google.com/rss/articles/fixture)' in footer
    assert 'Company income statement' in footer
    assert 'Reddit post:' not in footer
    assert 'INFY.NS (three-company limit)' in footer


def test_all_missing_company_sources_do_not_trigger_invented_analyst_reports(company_sources):
    init_db()
    for key in ('fundamentals', 'news', 'social'):
        company_sources['companies'][0][key] = {'status': 'unavailable', 'source': '', 'message': 'Blocked', 'fetched_at': '2026-11-08T00:00:00+00:00', 'items': []}
    with Session.begin() as db:
        conversation = Conversation()
        db.add(conversation)
        db.flush()
        cid = conversation.id
    async def forbidden(**kwargs):
        raise AssertionError('There is no evidence for an AI judgment')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)))
    request = SimpleNamespace(conversation_id=cid, market='IN', message='Company research for TCS.NS')
    ctx = {'id': None, 'status': 'completed', 'result': {'items': [{'symbol': 'TCS.NS', 'signal': 'NO_SCREEN', 'stale': False, 'risk': {}}]}}
    async def collect():
        return [event async for event in stream_evaluation(client, request, ctx, 'Use checked evidence.', company_sources=company_sources)]
    try:
        events = asyncio.run(collect())
        text = ''.join(e[1]['text'] for e in events if e[0] == 'delta')
        assert 'could not retrieve enough' in text
        assert 'not mean the company is healthy' in text
        with Session() as db:
            run = db.scalar(select(ResearchRun).where(ResearchRun.conversation_id == cid))
            assert run.reports['report_status']['fundamentals_analyst'] == 'unavailable'
            assert run.reports['report_status']['sentiment_analyst'] == 'unavailable'
            assert run.scan_id is None
    finally:
        with Session.begin() as db:
            db.execute(delete(ResearchRun).where(ResearchRun.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
