from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import pytest

from app.schemas import ScanRequest
from app.strategies import analyze, wilder_rsi


def bars(prices, old=False):
    end = datetime.now(timezone.utc).date() - timedelta(days=2 if not old else 60)
    return [{'date': (end-timedelta(days=len(prices)-1-i)).isoformat(), 'open': float(p), 'high': float(p+1), 'low': float(p-1), 'close': float(p), 'volume': 10000.0} for i,p in enumerate(prices)]


def test_rising_trend_and_overbought_rsi_can_disagree():
    result = analyze('TEST', bars(np.linspace(100,200,260)), ['trend','rsi'])
    assert result['signal'] == 'HOLD'
    assert {v['signal'] for v in result['strategies']} == {'BUY','SELL'}
    assert result['score'] == 0
    assert result['risk']['stop_reference'] is None


def test_trend_direction_and_long_only_risk_levels():
    bullish = analyze('TEST', bars(np.linspace(100,200,260)), ['trend'])
    bearish = analyze('TEST', bars(np.linspace(200,100,260)), ['trend'])
    assert bullish['signal'] == 'BUY'
    assert bullish['risk']['stop_reference'] < bullish['price'] < bullish['risk']['target_reference']
    assert bearish['signal'] == 'SELL'
    assert bearish['risk']['stop_reference'] is None


def test_stale_data_cannot_produce_actionable_signal():
    result = analyze('TEST', bars(np.linspace(100,200,260), old=True), ['trend'])
    assert result['stale'] and result['warnings']
    assert result['signal'] == 'HOLD'
    assert result['risk']['target_reference'] is None


def test_breakout_compares_with_previous_bars_and_requires_volume():
    prices = np.full(260,100.0)
    prices[-1] = 110
    data = bars(prices)
    without_volume = analyze('TEST', data, ['breakout'])
    data[-1]['volume'] = 20000
    with_volume = analyze('TEST', data, ['breakout'])
    assert without_volume['signal'] == 'HOLD'
    assert with_volume['signal'] == 'BUY'


def test_flat_market_rsi_is_neutral_and_math_is_finite():
    result = analyze('TEST', bars(np.full(260,100.0)), ['rsi'])
    assert result['indicators']['rsi14'] == 50
    assert result['signal'] == 'HOLD'


def test_wilder_rsi_known_example():
    prices = pd.Series([44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.0,46.03])
    result = wilder_rsi(prices)
    assert result.iloc[14] == pytest.approx(70.46,abs=.01)
    assert result.iloc[15] == pytest.approx(66.25,abs=.01)


@pytest.mark.parametrize('data', [bars(np.full(100,100.0)), bars([float('nan')]*260), bars([-1.0]*260)])
def test_unusable_data_is_rejected(data):
    with pytest.raises(ValueError):
        analyze('TEST', data, ['trend'])


def test_untrusted_tool_inputs_are_bounded_and_normalized():
    assert ScanRequest(market='IN', symbols=[' reliance.ns ', 'RELIANCE.NS','TCS.NS']).symbols == ['RELIANCE.NS','TCS.NS']
    for payload in [{'symbols':['../../etc/passwd']}, {'symbols':['NVDA']*21}, {'strategies':['invented']}, {'strategies':[]}]:
        with pytest.raises(ValueError):
            ScanRequest(**payload)
