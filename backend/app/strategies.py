"""Deterministic daily-bar rules. Scores measure agreement, never win probability."""
import math
import numpy as np
import pandas as pd

STRATEGIES = {
    'trend': {'name': 'Trend following', 'description': 'BUY when close > SMA50 > SMA200; SELL when close < SMA50 < SMA200.'},
    'rsi': {'name': 'RSI mean reversion', 'description': 'Wilder RSI14: BUY below 30, SELL above 70, otherwise HOLD.'},
    'macd': {'name': 'MACD momentum', 'description': 'BUY on a fresh MACD(12,26,9) bullish crossover; SELL on a bearish crossover.'},
    'bollinger': {'name': 'Bollinger reversion', 'description': '20-day bands at 2 standard deviations: BUY below the lower band, SELL above the upper band.'},
    'breakout': {'name': 'Volume breakout', 'description': 'BUY above the previous 20-day high or SELL below its low, with volume > 1.5x the previous 20-day average.'},
}


def number(value):
    value = float(value)
    return round(value, 4) if math.isfinite(value) else None


def wilder_rsi(close, period=14):
    delta = np.diff(np.asarray(close, dtype=float))
    gains, losses = np.maximum(delta, 0), np.maximum(-delta, 0)
    avg_gain, avg_loss = gains[:period].mean(), losses[:period].mean()
    values = [np.nan] * period
    for i in range(period - 1, len(delta)):
        if i >= period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values.append(50.0 if avg_gain == avg_loss == 0 else 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return pd.Series(values, index=close.index)


def analyze(symbol, bars, selected, currency='USD', source='Yahoo Finance / yfinance', fetched_at=None):
    df = pd.DataFrame(bars)
    if len(df) < 220:
        raise ValueError('At least 220 completed daily bars are required.')
    required = ['open', 'high', 'low', 'close', 'volume']
    if not all(c in df for c in required) or not np.isfinite(df[required].to_numpy(dtype=float)).all():
        raise ValueError('Price history contains missing or invalid values.')
    if (df['close'] <= 0).any() or (df['volume'] < 0).any() or (df['high'] < df['low']).any():
        raise ValueError('Price history failed OHLCV validation.')
    c, h, l, v = (df[x].astype(float) for x in ['close', 'high', 'low', 'volume'])
    sma50, sma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
    mid, std = c.rolling(20).mean().iloc[-1], c.rolling(20).std(ddof=0).iloc[-1]
    lower, upper = mid - 2 * std, mid + 2 * std
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    rsi = wilder_rsi(c).iloc[-1]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    # Seed Wilder ATR with the arithmetic mean, then smooth.
    atr = tr.iloc[1:15].mean()
    for val in tr.iloc[15:]:
        atr = (atr * 13 + val) / 14
    close = c.iloc[-1]
    prev_high, prev_low = h.iloc[-21:-1].max(), l.iloc[-21:-1].min()
    avg_vol = v.iloc[-21:-1].mean()
    vol_ratio = v.iloc[-1] / avg_vol if avg_vol else 0
    votes = []

    def vote(key, score, reason):
        if key in selected:
            votes.append({'id': key, 'name': STRATEGIES[key]['name'], 'signal': 'BUY' if score > 0 else 'SELL' if score < 0 else 'HOLD', 'score': score, 'reason': reason})

    trend = 1 if close > sma50 > sma200 else -1 if close < sma50 < sma200 else 0
    vote('trend', trend, f'Close {close:.2f}; SMA50 {sma50:.2f}; SMA200 {sma200:.2f}.')
    vote('rsi', 1 if rsi < 30 else -1 if rsi > 70 else 0, f'RSI14 is {rsi:.1f}; oversold <30, overbought >70.')
    hist = macd - macd_signal
    cross = 1 if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0 else -1 if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0 else 0
    vote('macd', cross, f'MACD histogram {hist.iloc[-1]:.3f}; {"bullish crossover" if cross == 1 else "bearish crossover" if cross == -1 else "no fresh crossover"}.')
    vote('bollinger', 1 if close < lower else -1 if close > upper else 0, f'Close {close:.2f}; 20-day band {lower:.2f}–{upper:.2f}.')
    breakout = (1 if close > prev_high else -1 if close < prev_low else 0) if vol_ratio > 1.5 else 0
    vote('breakout', breakout, f'Prior 20-day range {prev_low:.2f}–{prev_high:.2f}; volume {vol_ratio:.2f}x average.')
    score = sum(x['score'] for x in votes) / len(votes) if votes else 0
    raw_signal = 'BUY' if score >= .4 else 'SELL' if score <= -.4 else 'HOLD'
    age = (pd.Timestamp.now(tz='UTC').date() - pd.Timestamp(df['date'].iloc[-1]).date()).days
    stale = age > 7
    signal = 'HOLD' if stale else raw_signal
    # Long-only exit/invalidation references, not broker orders or price predictions.
    stop = max(0, close - 2 * atr) if signal == 'BUY' else None
    target = close + 4 * atr if signal == 'BUY' else None
    return {
        'symbol': symbol, 'currency': currency, 'price': number(close),
        'change_pct': number((close / c.iloc[-2] - 1) * 100),
        'signal': signal, 'score': number(score),
        'agreement_pct': round(sum(x['signal'] == signal for x in votes) / len(votes) * 100) if votes else 0,
        'as_of': str(df['date'].iloc[-1]), 'fetched_at': fetched_at, 'source': source,
        'stale': stale, 'warnings': ['Latest completed bar is more than 7 calendar days old; signal forced to HOLD.'] if stale else [],
        'indicators': {'sma50': number(sma50), 'sma200': number(sma200), 'rsi14': number(rsi), 'macd': number(macd.iloc[-1]), 'macd_signal': number(macd_signal.iloc[-1]), 'bollinger_lower': number(lower), 'bollinger_upper': number(upper), 'atr14': number(atr), 'volume_ratio': number(vol_ratio)},
        'risk': {'stop_reference': number(stop) if stop is not None else None, 'target_reference': number(target) if target is not None else None, 'atr_pct': number(atr/close*100), 'note': 'Illustrative long setup: 2 ATR invalidation, 4 ATR target. SELL means reduce/exit a long position.'},
        'strategies': votes,
        'chart': [{'date': str(row['date']), 'close': number(row['close'])} for row in bars[-90:]],
    }
