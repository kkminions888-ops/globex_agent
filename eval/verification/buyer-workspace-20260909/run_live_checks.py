"""复用本轮测试浏览器身份验证 Agent 真正修改和删除长期记忆。"""
import asyncio
from collections import Counter
import json
from pathlib import Path
import uuid
import httpx

OUTPUT=Path(__file__).resolve().parent


async def main():
    buyer=json.loads((OUTPUT/"browser-identifiers.json").read_text())["buyer_id"]
    cases=[
        ("update","把长期偏好“喜欢蓝色”改成“喜欢绿色”，以后都按绿色。请直接更新长期记忆，只做这件事，不搜索商品。"),
        ("read_updated","我长期喜欢什么颜色？只根据当前保存的长期记忆回答，不新增或修改记忆，不搜索商品。"),
        ("delete","删除长期记忆中的“喜欢绿色”，以后不再保留这条颜色偏好。请实际删除，只做这件事，不搜索商品。"),
        ("read_deleted","当前长期记忆里还记录了我的颜色偏好吗？没有就说未记录，不猜测，也不要新增记忆。"),
        ("auto_skill","请使用我的“三行需求单”方案整理：通勤背包，预算300元人民币，寄到中国。不要搜索商品。"),
    ]
    reports=[]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001",timeout=240) as client:
        for name,query in cases:
            rid,sid=uuid.uuid4().hex,uuid.uuid4().hex
            request={"threadId":sid,"runId":rid,"state":{},"tools":[],"context":[],
                "messages":[{"id":uuid.uuid4().hex,"role":"user","content":query}],
                "forwardedProps":{"buyerId":buyer,"locale":"zh-CN","currency":"CNY"}}
            events=[]
            async with client.stream("POST","/commerce/ag-ui/run",json=request) as response:
                response.raise_for_status();trace=response.headers.get("x-trace-id")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):events.append(json.loads(line[6:]))
            snapshots=[e["messages"] for e in events if e["type"]=="MESSAGES_SNAPSHOT"]
            final="\n".join(m.get("content","") for m in (snapshots[-1] if snapshots else []) if m.get("role")=="assistant")
            tools=[e["toolCallName"] for e in events if e["type"]=="TOOL_CALL_START"]
            pref_response=await client.get("/commerce/preferences",params={"buyer_id":buyer});pref_response.raise_for_status()
            preferences=pref_response.json()["preferences"]
            counts=dict(Counter(e["type"] for e in events))
            report={"case":name,"query":query,"run_id":rid,"session_id":sid,"trace_id":trace,"tool_names":tools,
                    "final_text":final,"preferences":preferences,"events":counts}
            expected = {"update":"update_preference_tool","delete":"forget_preference_tool","auto_skill":"load_agent_skill_tool"}.get(name)
            passed=counts.get("RUN_FINISHED")==1 and not counts.get("RUN_ERROR") and (not expected or expected in tools)
            if name in ("update","read_updated"):
                passed=passed and [p["statement"] for p in preferences]==["喜欢绿色"]
            if name in ("delete","read_deleted"):passed=passed and preferences==[]
            report["mechanism_pass"]=bool(passed)
            (OUTPUT/(name+".json")).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            (OUTPUT/(name+".events.json")).write_text(json.dumps(events,ensure_ascii=False,indent=2))
            reports.append(report)
            print(json.dumps(report,ensure_ascii=False),flush=True)
        (OUTPUT/"live-checks.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2))
        if not all(r["mechanism_pass"] for r in reports):raise SystemExit(2)


if __name__=="__main__":asyncio.run(main())
