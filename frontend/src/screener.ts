export type ScreenFilters = {search:string; minAgreement:number; movement:string; freshness:string; sort:string}
type Screenable = {symbol:string;signal:string;agreement_pct:number;change_pct:number;stale:boolean;score:number}

export const defaultFilters:ScreenFilters = {search:'', minAgreement:0, movement:'all', freshness:'all', sort:'score'}

export function filterScreen<T extends Screenable>(items:T[], filters:ScreenFilters, signal='ALL'):T[] {
  const query=filters.search.trim().toUpperCase()
  return items.filter(x=>(signal==='ALL'||x.signal===signal)
    &&x.symbol.includes(query)&&x.agreement_pct>=filters.minAgreement
    &&(filters.movement==='all'||(filters.movement==='gainers'?x.change_pct>0:x.change_pct<0))
    &&(filters.freshness==='all'||(filters.freshness==='fresh'?!x.stale:x.stale)))
    .sort((a,b)=>filters.sort==='symbol'?a.symbol.localeCompare(b.symbol)
      : (filters.sort==='change_desc'?b.change_pct-a.change_pct:filters.sort==='change_asc'?a.change_pct-b.change_pct:filters.sort==='agreement'?b.agreement_pct-a.agreement_pct:b.score-a.score)||a.symbol.localeCompare(b.symbol))
}
