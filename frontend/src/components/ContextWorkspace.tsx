import { useEffect, useRef, useState } from 'react';
import type { WorkspaceRequest } from './BuyerWorkspace';
import './contextWorkspace.css';

type Props={sessionId:string;busy:boolean;pending:boolean;hasMessages:boolean;request:WorkspaceRequest;onBusyChange:(busy:boolean)=>void};
type View={revision:number;strategy?:string;summary?:string;working?:{goal?:string;latest_request?:string;constraints?:Record<string,{source?:string;value?:string;currency?:string}>;selected?:string[];comparisons?:string[]};statistics?:{status?:string;before_tokens?:number;after_tokens?:number}};
export default function ContextWorkspace({sessionId,busy,pending,hasMessages,request,onBusyChange}:Props){
 const [view,setView]=useState<View|null>(null),[running,setRunning]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState('');
 const generation=useRef(0), operation=useRef('');
 const refresh=async(current:number)=>{
  try{const data=await request('/context?session_id='+encodeURIComponent(sessionId));if(current===generation.current){setView(data as View);const active=data.operation as {operation_id?:string;status?:string}|undefined;if(active?.status==='running'&&active.operation_id){operation.current=active.operation_id;setRunning(true);onBusyChange(true);}}}
  catch(e){if(current===generation.current && !(e instanceof Error && 'status' in e && e.status===404))setError(e instanceof Error?e.message:'摘要暂时无法读取');}
 };
 useEffect(()=>{
  const current=++generation.current;setView(null);setError('');setNotice('');setRunning(false);operation.current='';onBusyChange(false);
  if(hasMessages) void refresh(current);
  return()=>{++generation.current;};
 },[sessionId,hasMessages]);
 useEffect(()=>{if(!busy&&hasMessages)void refresh(generation.current);},[busy]);
 useEffect(()=>{
  if(!running)return;
  let stopped=false,timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{
   try{
    const data=await request('/context/operations/'+encodeURIComponent(operation.current));
    if(stopped)return;
    if(data.status!=='running'){
     setRunning(false);onBusyChange(false);setNotice(typeof data.message==='string'?data.message:'整理已结束');
     await refresh(generation.current);return;
    }
   }catch(e){if(!stopped)setError(e instanceof Error?e.message:'读取整理进度失败，正在重试');}
   if(!stopped)timer=setTimeout(poll,1500);
  };void poll();return()=>{stopped=true;clearTimeout(timer);};
 },[running,request,onBusyChange]);
 const compact=async()=>{
  if(!view||busy||pending||running)return;
  const current=generation.current;setError('');setNotice('');onBusyChange(true);
  try{
   const data=await request('/context/compact','POST',{session_id:sessionId,request_id:crypto.randomUUID(),expected_revision:view.revision});
   if(current!==generation.current)return;
   operation.current=String(data.operation_id);setRunning(data.status==='running');
   if(data.status!=='running'){onBusyChange(false);setNotice(String(data.message??'整理已结束'));}
  }catch(e){if(current===generation.current){onBusyChange(false);setError(e instanceof Error?e.message:'未能整理，原记录保留');void refresh(current);}}
 };
 if(!hasMessages)return null;
 return <section className="context-workspace" aria-label="本次选购摘要">
  <div className="context-workspace-header"><details><summary>本次选购摘要</summary>
   {view?.strategy==='legacy'&&view.working?.goal&&<small>以下为最近一次整理的记录，后续补充以对话为准。</small>}
   {view?.working?.goal&&<p>最初需求：{view.working.goal}</p>}
   {view?.working?.latest_request&&<p>最近补充：{view.working.latest_request}</p>}
   {view?.working?.constraints&&<ul>{Object.entries(view.working.constraints).map(([key,value])=><li key={key}>{value.source??value.value}{value.currency&&`（${value.currency}）`}</li>)}</ul>}
   {!!view?.working?.selected?.length&&<p>关注商品：{view.working.selected.join('、')}</p>}
   {!!view?.working?.comparisons?.length&&<p>比较商品：{view.working.comparisons.join('、')}</p>}
   {!view?.working?.goal&&<p>尚未整理本次需求。你可以继续补充，完整对话会保留。</p>}
   <small>这是本次选购的工作记录。价格与库存以重新查询为准；需要修改需求，直接告诉 Globex。</small>
  </details><button type="button" onClick={()=>void compact()} disabled={!view||busy||pending||running}>{running?'正在整理…':'整理上下文'}</button></div>
  {pending&&<small>请先完成或拒绝待确认操作。</small>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
