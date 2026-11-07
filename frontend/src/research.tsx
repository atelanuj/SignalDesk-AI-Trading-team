import {useEffect, useState} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {X} from 'lucide-react'

type Report = {id:string;status:string;created_at:string;reports:Record<string,unknown>}
const roles=['technical_analyst','bull_researcher','bear_researcher','research_manager','trader','risk_manager','portfolio_reviewer']
export function ResearchReportDialog({id,api,onClose}:{id:string;api:<T>(path:string)=>Promise<T>;onClose:()=>void}){
  const [run,setRun]=useState<Report|null>(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;api<Report>(`/research/${id}`).then(r=>{if(!cancelled)setRun(r)}).catch(e=>{if(!cancelled)setError(e.message)});return()=>{cancelled=true}},[id])
  return <div className="modal-backdrop" onClick={onClose}><section className="modal research-modal" role="dialog" aria-modal="true" aria-label="AI evaluation reports" onClick={e=>e.stopPropagation()}><div className="modal-header"><div><div className="eyebrow">SEPARATE AI ROLE REPORTS</div><h2>Stock evaluation workflow</h2></div><button className="icon-button" aria-label="Close evaluation reports" onClick={onClose}><X size={20}/></button></div><p>TradingAgents-style technical research. These are public reports from separate model calls. Fundamentals, news and social sentiment are not connected. No role places trades.</p>{error&&<p className="data-error" role="alert">{error}</p>}{!run&&!error&&<p role="status">Loading saved reports…</p>}{run&&<><p className="journal-note">Status: {run.status} · {new Date(run.created_at).toLocaleString()}{run.status!=='completed'?' · This run is incomplete; no completed final review is available.':''}</p>{roles.map(role=><details className="research-role" key={role}><summary>{role.replaceAll('_',' ')}<span>{typeof run.reports[role]==='string'?'Report saved':'Not completed'}</span></summary><div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{typeof run.reports[role]==='string'?run.reports[role] as string:'No report was saved for this stage.'}</ReactMarkdown></div></details>)}</>}</section></div>
}
