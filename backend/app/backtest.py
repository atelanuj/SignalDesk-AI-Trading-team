"""Fixed-rule historical simulation; no tuning, future bars or journal writes."""
from datetime import date
import math
from .market import history
from .strategies import analyze, STRATEGIES

NOTE = ('Historical price-rule simulation only, not a prediction. Adjusted OHLC includes provider corporate-action revisions. '
        'No historical company/news/sentiment reconstruction, parameter optimization or out-of-sample validation. '
        'Long only, fractional shares, all available simulation cash per entry; HOLD retains a position, SELL exits. '
        'Signal after completed close executes at next session open. Final position liquidated at last close. '
        'No stop/target orders, taxes, liquidity limits or broker-specific charges simulated. Costs: 0.10% fee plus 0.10% slippage per side by default.')


def simulate(symbol, bars, strategies, initial_cash=100000, fee_bps=10, slippage_bps=10):
    if len(bars) < 222:
        raise ValueError('At least 222 completed bars are needed: 220 warm-up bars and two test sessions.')
    if not strategies or not set(strategies) <= set(STRATEGIES) or not 0 <= fee_bps <= 100 or not 0 <= slippage_bps <= 100 or not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError('Invalid simulation assumptions.')
    dates = [date.fromisoformat(b['date']) for b in bars]
    if dates != sorted(set(dates)):
        raise ValueError('Bars must have unique increasing dates.')
    for b in bars:
        if not all(math.isfinite(float(b[k])) for k in ('open','high','low','close','volume')) or min(b['open'],b['low'],b['close']) <= 0 or b['volume'] < 0 or b['high'] < max(b['open'],b['close'],b['low']) or b['low'] > min(b['open'],b['close']):
            raise ValueError('Invalid historical OHLCV.')
    fee, slip = fee_bps/10000, slippage_bps/10000
    cash, shares, peak, drawdown = float(initial_cash), 0., float(initial_cash), 0.
    trades, curve = [], []
    entry = None
    def exit_position(raw, stamp, reason):
        nonlocal cash, shares, entry
        fill = raw * (1-slip)
        proceeds = shares*fill*(1-fee)
        trades.append({'entry_date':entry['date'], 'exit_date':stamp, 'entry_fill':round(entry['fill'],4), 'exit_fill':round(fill,4), 'net_pnl':round(proceeds-entry['cost'],2), 'profitable':proceeds>entry['cost'], 'return_pct':round((proceeds/entry['cost']-1)*100,4), 'exit_reason':reason})
        cash += proceeds
        shares, entry = 0., None
    for i in range(220, len(bars)):
        # Only the prefix ending yesterday is visible when selecting today's order.
        signal = analyze(symbol, bars[:i], strategies, as_of_date=dates[i-1])['signal']
        b = bars[i]
        if signal == 'BUY' and not shares:
            fill = b['open']*(1+slip)
            shares = cash/(fill*(1+fee))
            entry = {'date':b['date'], 'fill':fill, 'cost':cash}
            cash = 0.
        elif signal == 'SELL' and shares:
            exit_position(b['open'], b['date'], 'SELL at next open')
        if i == len(bars)-1 and shares:
            exit_position(b['close'], b['date'], 'End of test at final close')
        equity = cash + shares*b['close']
        peak = max(peak,equity)
        drawdown = min(drawdown,equity/peak-1)
        curve.append({'date':b['date'], 'equity':round(equity,2)})
    benchmark = initial_cash/(bars[220]['open']*(1+slip)*(1+fee))*bars[-1]['close']*(1-slip)*(1-fee)
    return {'symbol':symbol, 'status':'completed', 'start_date':bars[220]['date'], 'end_date':bars[-1]['date'],
            'warmup_bars':220, 'test_sessions':len(curve), 'strategies':strategies,
            'initial_cash':initial_cash, 'ending_cash':round(cash,2), 'return_pct':round((cash/initial_cash-1)*100,4),
            'buy_hold_return_pct':round((benchmark/initial_cash-1)*100,4), 'max_drawdown_pct':round(-drawdown*100,4),
            'closed_trades':len(trades), 'win_rate_pct':round(sum(t['profitable'] for t in trades)/len(trades)*100,2) if trades else None,
            'fee_bps_per_side':fee_bps,'slippage_bps_per_side':slippage_bps,
            'cost_per_side_pct':(fee+slip)*100, 'approx_round_trip_cost_pct':2*(fee+slip)*100,
            'trades':trades[-20:], 'trades_omitted':max(0,len(trades)-20), 'equity_curve':curve, 'note':NOTE}


def collect_backtests(symbols, strategies):
    result = []
    for symbol in list(dict.fromkeys(symbols))[:3]:
        try:
            data = history(symbol)
            result.append(simulate(symbol, data['bars'], strategies) | {'currency':data['currency'], 'fetched_at':data['fetched_at'], 'source':'Yahoo Finance / yfinance: adjusted completed daily OHLCV'})
        except Exception:
            result.append({'symbol':symbol,'status':'unavailable','message':'Historical simulation unavailable: insufficient or invalid prices, or provider failure. No performance was invented.'})
    return {'items':result,'omitted_symbols':list(symbols)[3:],'note':NOTE}


def compact_backtests(data):
    return {**data, 'items':[{k:v for k,v in row.items() if k not in ('equity_curve','trades')} for row in data['items']]}


def backtest_markdown(data):
    lines = ['\n\n**Historical backtest · computed, not predicted**', '| Stock / period | Rules after costs | Buy & hold | Largest fall | Trades |', '| --- | --- | --- | --- | --- |']
    for r in data['items']:
        if r['status'] == 'completed':
            lines.append(f"| {r['symbol']} · {r['start_date']} to {r['end_date']} | {r['return_pct']:.2f}% | {r['buy_hold_return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['closed_trades']} |")
        else:
            lines.append(f"| {r['symbol']} | Unavailable | — | — | — |")
    lines.append('\nLargest fall means the decline from a previous portfolio peak, measured at daily closes. Next-session fills; 0.10% fees plus 0.10% slippage each side. All cash per entry, fractional shares, final close liquidation. No automatic stops/targets. Past results do not predict future profit; company finances/news were not backtested. See saved reports for full assumptions.')
    return '\n'.join(lines)
