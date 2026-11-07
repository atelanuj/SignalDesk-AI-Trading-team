import test from 'node:test'
import assert from 'node:assert/strict'
import {defaultFilters, filterScreen} from '../src/screener.ts'

const items = [
  {symbol:'TCS.NS',signal:'SELL',agreement_pct:80,change_pct:-2,stale:false,score:-.6},
  {symbol:'RELIANCE.NS',signal:'BUY',agreement_pct:60,change_pct:2,stale:false,score:.4},
  {symbol:'INFY.NS',signal:'HOLD',agreement_pct:100,change_pct:0,stale:true,score:0},
]
test('signal, text, agreement, movement and freshness filters compose',()=>{
  assert.deepEqual(filterScreen(items,{...defaultFilters,search:' tcs ',minAgreement:80,movement:'losers',freshness:'fresh'},'SELL').map(x=>x.symbol),['TCS.NS'])
  assert.equal(filterScreen(items,{...defaultFilters,minAgreement:80},'BUY').length,0)
  assert.deepEqual(filterScreen(items,{...defaultFilters,movement:'gainers'}).map(x=>x.symbol),['RELIANCE.NS'])
  assert.deepEqual(filterScreen(items,{...defaultFilters,freshness:'stale'}).map(x=>x.symbol),['INFY.NS'])
})
test('sort order changes without mutating the scan results',()=>{
  const original=items.map(x=>x.symbol)
  assert.deepEqual(filterScreen(items,{...defaultFilters,sort:'change_asc'}).map(x=>x.symbol),['TCS.NS','INFY.NS','RELIANCE.NS'])
  assert.deepEqual(filterScreen(items,{...defaultFilters,sort:'agreement'}).map(x=>x.symbol),['INFY.NS','TCS.NS','RELIANCE.NS'])
  assert.deepEqual(filterScreen(items,{...defaultFilters,sort:'symbol'}).map(x=>x.symbol),['INFY.NS','RELIANCE.NS','TCS.NS'])
  assert.deepEqual(items.map(x=>x.symbol),original)
})
