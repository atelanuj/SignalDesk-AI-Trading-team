import pytest
from app.discovery import discovery_request,rank_discovery


def test_indian_discovery_uses_complete_provider_universe_not_personal_watchlist(monkeypatch):
    monkeypatch.setattr('app.discovery.get_universe',lambda key:{'symbols':[f'S{i}.NS' for i in range(50)],'source':'https://www.niftyindices.com/fixture','fetched_at':'2026-11-08'})
    r=discovery_request('IN',['trend'])
    assert r.universe=='nifty50' and len(r.symbols)==50 and r.universe_source.endswith('fixture')
    us=discovery_request('US',['trend'])
    assert us.universe=='watchlist' and all(not s.endswith('.NS') for s in us.symbols)


def test_discovery_never_substitutes_if_index_feed_fails(monkeypatch):
    def fail(key):raise ValueError('blocked')
    monkeypatch.setattr('app.discovery.get_universe',fail)
    with pytest.raises(ValueError):discovery_request('IN',['trend'])


def test_discovery_ranks_fresh_data_and_discloses_coverage_and_failures():
    def item(symbol,score,atr,above,vol,stale=False):
        return {'symbol':symbol,'score':score,'risk':{'atr_pct':atr},'stale':stale,'price':110 if above else 90,'indicators':{'sma50':100,'volume_ratio':vol},'change_pct':1}
    data={'items':[item('STALE.NS',1,1,True,10,True),item('HIGH.NS',.4,4,False,5),item('LOW.NS',.4,2,True,2)],'errors':[{'symbol':'FAILED.NS'}]}
    beginner=rank_discovery(data,'beginner','IN')
    assert [i['symbol'] for i in beginner['items']]==['LOW.NS','HIGH.NS','STALE.NS']
    trending=rank_discovery(data,'trending','IN')
    assert trending['items'][0]['symbol']=='LOW.NS'
    assert trending['discovery']['fresh']==2 and trending['discovery']['failed']==1
    assert 'not social-media popularity' in trending['discovery']['note']
