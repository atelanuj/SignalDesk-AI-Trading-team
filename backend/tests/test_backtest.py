from datetime import date,timedelta
import pytest
from app import backtest as bt


def bars(n=225):
    return [{'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),'open':100.,'high':100.,'low':100.,'close':100.,'volume':1000.} for i in range(n)]


def test_signal_uses_only_prior_prefix_and_executes_next_open(monkeypatch):
    data=bars(222)
    data[219].update(open=10.,close=10.,low=10.,high=10.)
    data[221].update(open=120.,close=100.,low=100.,high=120.)
    seen=[]
    def analyze(symbol,prefix,selected,**kwargs):
        seen.append(len(prefix))
        assert kwargs['as_of_date'].isoformat()==prefix[-1]['date']
        return {'signal':'BUY' if len(prefix)==220 else 'SELL'}
    monkeypatch.setattr(bt,'analyze',analyze)
    r=bt.simulate('TCS.NS',data,['trend'],initial_cash=1000,fee_bps=0,slippage_bps=0)
    assert seen==[220,221]
    assert r['return_pct']==20 and r['buy_hold_return_pct']==0
    assert r['trades'][0]['entry_fill']==100 and r['trades'][0]['exit_fill']==120
    assert r['trades'][0]['entry_date']==data[220]['date']


def test_holds_keep_position_drawdown_and_final_liquidation_include_costs(monkeypatch):
    data=bars()
    data[222].update(close=80.,low=80.)
    monkeypatch.setattr(bt,'analyze',lambda s,p,st,**kw:{'signal':'BUY' if len(p)==220 else 'HOLD'})
    r=bt.simulate('TCS.NS',data,['trend'],fee_bps=0,slippage_bps=0)
    assert r['max_drawdown_pct']==20 and r['closed_trades']==1
    assert r['return_pct']==0 and r['trades'][0]['exit_reason']=='End of test at final close'
    costed=bt.simulate('TCS.NS',bars(),['trend'])
    assert costed['return_pct']<0 and costed['win_rate_pct']==0


def test_real_flat_rule_has_zero_trades_and_no_win_probability():
    r=bt.simulate('TCS.NS',bars(),['trend','rsi','macd','bollinger','breakout'])
    assert r['return_pct']==0 and r['closed_trades']==0 and r['win_rate_pct'] is None
    assert r['buy_hold_return_pct']<0


@pytest.mark.parametrize('invalid',['short','duplicate','unsorted','nonfinite','bad_open','bad_ohlc'])
def test_invalid_history_rejected(invalid):
    data=bars()
    if invalid=='short':data=data[:221]
    elif invalid=='duplicate':data[1]['date']=data[0]['date']
    elif invalid=='unsorted':data[0],data[1]=data[1],data[0]
    elif invalid=='nonfinite':data[0]['volume']=float('nan')
    elif invalid=='bad_open':data[0]['open']=0
    else:data[0]['high']=50
    with pytest.raises(ValueError):bt.simulate('TCS.NS',data,['trend'])


def test_collection_bounded_and_failure_not_fabricated(monkeypatch):
    calls=[]
    def fetch(symbol):calls.append(symbol);raise ValueError('Provider down')
    monkeypatch.setattr(bt,'history',fetch)
    r=bt.collect_backtests(['A.NS','B.NS','C.NS','D.NS'],['trend'])
    assert len(calls)==3 and r['omitted_symbols']==['D.NS']
    assert all(i['status']=='unavailable' and 'return_pct' not in i for i in r['items'])


def test_backtest_api_validates_market_and_caps_work_before_provider_calls(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    calls=[]
    def collect(symbols,strategies):calls.append((symbols,strategies));return {'items':[],'note':'Fixture'}
    monkeypatch.setattr('app.main.collect_backtests',collect)
    with TestClient(app) as client:
        assert client.post('/api/backtests',json={'market':'IN','symbols':['TCS.NS'],'strategies':['trend']}).status_code==200
        assert calls==[(['TCS.NS'],['trend'])]
        assert client.post('/api/backtests',json={'market':'IN','symbols':['A.NS','B.NS','C.NS','D.NS']}).status_code==422
        assert client.post('/api/backtests',json={'market':'US','symbols':['TCS.NS']}).status_code==422
        assert len(calls)==1
