// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {it,expect,vi} from 'vitest';
import ContextWorkspace from '../src/components/ContextWorkspace';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
it('摘要显示、提交版本和幂等标识，审批时不允许整理',async()=>{
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const request=vi.fn(async(path:string)=>path==='/context/compact'?{operation_id:'op',status:'completed',message:'已整理'}:{revision:3,working:{goal:'通勤包',constraints:{budget:{source:'预算300元'}}}});
 const change=vi.fn();
 try{
  await act(async()=>root.render(<ContextWorkspace sessionId="s" busy={false} pending={false} hasMessages request={request} onBusyChange={change}/>));
  expect(host.textContent).toContain('预算300元');
  await act(async()=>host.querySelector('button')!.click());
  expect(host.textContent).toContain('已整理');
  expect(request).toHaveBeenCalledWith('/context/compact','POST',expect.objectContaining({session_id:'s',expected_revision:3,request_id:expect.any(String)}));
  await act(async()=>root.render(<ContextWorkspace sessionId="s" busy={false} pending hasMessages request={request} onBusyChange={change}/>));
  expect(host.querySelector('button')!.disabled).toBe(true);
 }finally{await act(async()=>root.unmount());host.remove();}
});
it('刷新从数据库操作恢复进度并读取完成结果',async()=>{
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 let done=false;const request=vi.fn(async(path:string)=>{if(path.includes('/operations/')){done=true;return{status:'completed',message:'恢复完成'};}return{revision:2,operation:done?null:{status:'running',operation_id:'op'}};});
 const change=vi.fn();
 try{
  await act(async()=>root.render(<ContextWorkspace sessionId="s" busy={false} pending={false} hasMessages request={request} onBusyChange={change}/>));
  expect(host.textContent).toContain('恢复完成');expect(request).toHaveBeenCalledWith('/context/operations/op');
 }finally{await act(async()=>root.unmount());host.remove();}
});
