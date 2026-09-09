import { describe, expect, it } from "vitest";
import { CommerceClient } from "../src/lib/commerceClient";

const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
const sse = (runId: string, items: Array<[number, Record<string, unknown>]>) => new Response(items.map(([seq, event]) =>
  `id: ${runId}:${seq}\ndata: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
const waitUntil = async (predicate: () => boolean) => {
  const deadline = Date.now() + 2000;
  while (!predicate()) { if (Date.now() > deadline) throw new Error("等待测试状态超时"); await new Promise((resolve) => setTimeout(resolve, 5)); }
};
const store = () => {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
};

describe("持久运行恢复（使用官方 AG-UI SDK）", () => {
  it("网络断流按cursor重连，丢弃重复和乱序帧，不重新POST模型", async () => {
    let body: any, posts = 0, gets = 0;
    const client = new CommerceClient({ url: "/commerce/ag-ui/run", fetch: async (url, init) => {
      if (init.method === "POST") {
        posts++; body = JSON.parse(String(init.body));
        return sse(body.runId, [[1, { type: "RUN_STARTED", threadId: body.threadId, runId: body.runId }],
          [2, { type: "TEXT_MESSAGE_START", messageId: "a", role: "assistant" }],
          [3, { type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "甲" }]]);
      }
      gets++;
      expect(String(url)).toContain("after=3");
      if (gets === 1) return sse(body.runId, [[3, { type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "甲" }],
        [5, { type: "TEXT_MESSAGE_END", messageId: "a" }]]);
      return sse(body.runId, [[4, { type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "乙" }],
        [5, { type: "TEXT_MESSAGE_END", messageId: "a" }],
        [6, { type: "RUN_FINISHED", threadId: body.threadId, runId: body.runId }]]);
    }});
    await client.submit("背包");
    expect(posts).toBe(1); expect(gets).toBe(2);
    expect(client.getSnapshot().messages.at(-1)?.content).toBe("甲乙");
    expect(client.getSnapshot()).toMatchObject({ status: "idle", recoverableRunId: null });
  });

  it("切页detach只断订阅，刷新后从服务端恢复运行和历史", async () => {
    const storage = store();
    let body: any, posts = 0, cancels = 0, complete = false;
    const fetch = async (url: string, init: RequestInit) => {
      if (url.includes("/cancel")) { cancels++; return json({}); }
      if (init.method === "POST") {
        posts++; body = JSON.parse(String(init.body));
        return new Response(new ReadableStream({ start(controller) {
          controller.enqueue(new TextEncoder().encode(`id: ${body.runId}:1\ndata: ${JSON.stringify({ type: "RUN_STARTED", threadId: body.threadId, runId: body.runId })}\n\n`));
          init.signal?.addEventListener("abort", () => controller.error(new DOMException("断开订阅", "AbortError")), { once: true });
        }}), { headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.includes("/events")) {
        complete = true;
        return sse(body.runId, [[1, { type: "RUN_STARTED", threadId: body.threadId, runId: body.runId }],
          [2, { type: "MESSAGES_SNAPSHOT", messages: [...body.messages, { id: "final", role: "assistant", content: "恢复后的完整结果" }] }],
          [3, { type: "STATE_SNAPSHOT", snapshot: { products: [], searchCompleted: true, progress: [] } }],
          [4, { type: "RUN_FINISHED", threadId: body.threadId, runId: body.runId }]]);
      }
      const run = { runId: body.runId, threadId: body.threadId, status: complete ? "completed" : "running", input: body,
        messages: body.messages, state: { products: [], searchCompleted: false } };
      if (url.includes("/sessions?")) return json({ sessions: [{ id: body.threadId, title: "服务端历史", updatedAt: Date.now() }] });
      if (url.includes("/sessions/")) return json({ id: body.threadId, run });
      return json(run);
    };
    const first = new CommerceClient({ url: "/commerce/ag-ui/run", storage, fetch });
    const pending = first.submit("恢复测试");
    await waitUntil(() => !!body);
    first.detach();
    await pending;
    expect(cancels).toBe(0);
    const restored = new CommerceClient({ url: "/commerce/ag-ui/run", storage, fetch });
    await restored.initialize();
    expect(posts).toBe(1);
    expect(restored.getSnapshot()).toMatchObject({ status: "idle", searchCompleted: true, recoverableRunId: null });
    expect(restored.getSnapshot().messages.at(-1)?.content).toBe("恢复后的完整结果");
    expect(restored.getSnapshot().history[0].source).toBe("server");
  });

  it("明确停止调用cancel API，带身份头；断网失败不能声称已停止", async () => {
    let body: any, cancels = 0;
    const client = new CommerceClient({ url: "/commerce/ag-ui/run", buyerId: "verified-buyer", accessToken: "test-token",
      fetch: async (url, init) => {
        expect(new Headers(init.headers).get("Authorization")).toBe("Bearer test-token");
        if (String(url).includes("/cancel")) {
          cancels++; throw new Error("网络断开");
        }
        body = JSON.parse(String(init.body));
        return new Response(new ReadableStream({ start(controller) {
          init.signal?.addEventListener("abort", () => controller.error(new DOMException("abort", "AbortError")), { once: true });
        }}), { headers: { "Content-Type": "text/event-stream" } });
      }});
    const pending = client.submit("背包");
    await waitUntil(() => !!body);
    client.stop();
    await pending;
    await waitUntil(() => client.getSnapshot().status === "error");
    expect(cancels).toBe(1);
    expect(client.getSnapshot().error).toContain("停止请求尚未确认");
    expect(client.getSnapshot().recoverableRunId).toBe(body.runId);
    expect(body.forwardedProps.buyerId).toBe("verified-buyer");
  });

  it("服务重启中断态从历史读取，不伪造继续执行", async () => {
    const values = store();
    values.setItem("globex.buyer", "b1");
    values.setItem("globex.agui.active-session", "s1");
    values.setItem("globex.agui.sessions.v1", JSON.stringify([{ id: "s1", title: "旧记录", updatedAt: 1, messages: [{ id: "u", role: "user", content: "查询" }], products: [], searchCompleted: false, runId: "r1" }]));
    let posts = 0;
    const client = new CommerceClient({ url: "/commerce/ag-ui/run", storage: values, fetch: async (url, init) => {
      if (init.method === "POST") posts++;
      if (String(url).includes("/sessions?")) return json({ sessions: [{ id: "s1", title: "服务端记录", updatedAt: 2 }] });
      return json({ run: { runId: "r1", threadId: "s1", status: "interrupted", messages: [{ id: "a", role: "assistant", content: "已保存的部分结果" }], state: {} } });
    }});
    await client.initialize();
    expect(posts).toBe(0);
    expect(client.getSnapshot()).toMatchObject({ status: "error", recoverableRunId: null });
    expect(client.getSnapshot().messages[0].content).toBe("已保存的部分结果");
  });
});

describe("刷新时以服务端记录恢复，缓存仅用于加速", () => {
  const historyFetch = async (url: string) => url.includes("/sessions?")
    ? json({sessions:[{id:"older",title:"旧对话",updatedAt:1},{id:"saved",title:"已保存",updatedAt:2}]})
    : json({run:{runId:"r",threadId:"saved",status:"completed",messages:[{id:"a",role:"assistant",content:"完整的已保存对话"}],state:{}}});

  it.each([null, "{坏缓存", "[]"])("正文缓存为 %s 时仍按当前会话恢复", async (cache) => {
    const storage=store();
    storage.setItem("globex.buyer","b1");
    storage.setItem("globex.agui.active-session","saved");
    if(cache!==null) storage.setItem("globex.agui.sessions.v1",cache);
    const client=new CommerceClient({url:"/commerce/ag-ui/run",storage,fetch:historyFetch});
    expect(client.getSnapshot().sessionId).toBe("saved");
    await client.initialize();
    expect(client.getSnapshot().messages[0].content).toBe("完整的已保存对话");
  });

  it("缺少当前会话指针时按服务端时间恢复最新记录",async()=>{
    const storage=store();
    const client=new CommerceClient({url:"/commerce/ag-ui/run",storage,buyerId:"b1",fetch:historyFetch});
    expect(storage.getItem("globex.buyer")).toBe("b1");
    await client.initialize();
    expect(client.getSnapshot().sessionId).toBe("saved");
    expect(storage.getItem("globex.agui.active-session")).toBe("saved");
  });

  it("主动开启的新选购刷新后保持空白，不被旧对话覆盖",async()=>{
    const storage=store();
    const first=new CommerceClient({url:"/commerce/ag-ui/run",storage,fetch:historyFetch});
    await first.initialize(); first.reset();
    const draft=first.getSnapshot().sessionId;
    const restored=new CommerceClient({url:"/commerce/ag-ui/run",storage,fetch:historyFetch});
    await restored.initialize();
    expect(restored.getSnapshot().sessionId).toBe(draft);
    expect(restored.getSnapshot().messages).toEqual([]);
  });

  it("买家切换时不读取上一个买家的本机记录和会话指针",async()=>{
    const storage=store();
    storage.setItem("globex.buyer","old-buyer");
    storage.setItem("globex.agui.active-session","private-old");
    storage.setItem("globex.agui.sessions.v1",JSON.stringify([{id:"private-old",title:"私有",updatedAt:1,messages:[{id:"a",role:"user",content:"私有内容"}]}]));
    const client=new CommerceClient({url:"/commerce/ag-ui/run",buyerId:"new-buyer",storage,fetch:async url=>{
      expect(url).toContain("buyer_id=new-buyer");return json({sessions:[]});
    }});
    await client.initialize();
    expect(client.getSnapshot().messages).toEqual([]);
    expect(client.getSnapshot().history).toEqual([]);
    expect(client.getSnapshot().sessionId).not.toBe("private-old");
  });
});
