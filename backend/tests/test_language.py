from app.language import BEGINNER_GUIDANCE, is_learning_question
from app.agent import SYSTEM
from app.research import computed_summary


def evidence(items):
    return {'items': items, 'report_items': len(items), 'total_items': len(items)}


def test_learning_questions_do_not_become_stock_reviews_but_stock_questions_still_do():
    stocks = [{'symbol': 'TCS.NS'}]
    assert is_learning_question('What does HOLD mean?', stocks)
    assert is_learning_question('How does paper trading work?', stocks)
    assert is_learning_question('Explain RSI in simple terms', stocks)
    assert not is_learning_question('What does HOLD mean for TCS.NS?', stocks)
    assert not is_learning_question('What do the signals in my watchlist mean?', stocks)
    assert not is_learning_question('What is the risk in this stock?', stocks)
    assert not is_learning_question('Screen TCS.NS', stocks)


def test_single_stock_summary_defines_signal_and_uses_exact_rule_count():
    item = {'symbol': 'TCS.NS', 'signal': 'HOLD', 'as_of': '2026-11-06', 'strategies': [{'signal': 'HOLD'}, {'signal': 'BUY'}, {'signal': 'HOLD'}]}
    text = computed_summary(evidence([item]))
    assert 'TCS.NS — HOLD' in text
    assert '2 of 3 selected rules give HOLD' in text
    assert 'do not show a clear buy or sell signal' in text
    assert 'does not measure your chance of making money' in text
    assert '| Score |' not in text


def test_sell_summary_does_not_assume_ownership_or_encourage_shorting():
    text = computed_summary(evidence([{'symbol': 'TCS.NS', 'signal': 'SELL', 'as_of': '2026-11-06', 'stale': True}]))
    assert 'If you already own this stock' in text
    assert 'does not mean selling shares you do not own' in text
    assert 'price data is old' in text


def test_broad_screen_summary_shows_five_and_discloses_complete_coverage():
    rows = [{'symbol': f'TEST{i}.NS', 'signal': 'HOLD', 'as_of': '2026-11-06'} for i in range(20)]
    text = computed_summary(evidence(rows))
    assert 'Showing 5 of 20' in text
    assert 'TEST4.NS' in text and 'TEST5.NS' not in text
    assert 'complete results are in Market screener' in text
    assert BEGINNER_GUIDANCE in SYSTEM
