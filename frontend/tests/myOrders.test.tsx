// @vitest-environment jsdom
import {act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import MyOrders from "../src/components/MyOrders";
it("订单筛选、详情、取消先准备确认单，不能直接扣改账本",async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const request=vi.fn(async(path:string)=>({orders:path.includes("CANCELLED")?[]:[{order_id:"GBX-1",status:"CONFIRMED",currency:"CNY",total_amount_major:129,shipping_address:"测试收货地",created_at:"2026-09-09T00:00:00Z",cancel_reason:null,lines:[{sku_id:"s1",title:"背包",quantity:1,unit_price_major:129}]}],total:1}));
 const prepare=vi.fn(async()=>true),resolve=vi.fn(async()=>true);
 const btn=(s:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent===s)!;
 try{
 await act(async()=>root.render(<MyOrders request={request} confirmations={[]} busy={false} error={null} onPrepare={prepare} onResolve={resolve} onRefresh={async()=>{}}/>));
 expect(host.textContent).toContain("GBX-1");
 await act(async()=>btn("订单详情").click());expect(host.textContent).toContain("测试收货地");
 await act(async()=>btn("取消订单").click());
 const input=host.querySelector('input')!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'改变计划');input.dispatchEvent(new Event('input',{bubbles:true}));});
 await act(async()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 expect(prepare).toHaveBeenCalledWith('GBX-1','改变计划');expect(resolve).not.toHaveBeenCalled();
 await act(async()=>btn("已取消").click());expect(request.mock.calls.at(-1)![0]).toContain('status=CANCELLED');expect(host.textContent).toContain('暂时没有这类订单');
 }finally{await act(async()=>root.unmount());host.remove();}
});
