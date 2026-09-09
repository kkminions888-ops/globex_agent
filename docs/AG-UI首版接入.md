# AG-UI 首版接入

后端锁定 `ag-ui-protocol==0.1.22`，新增 `POST /commerce/ag-ui/run`，请求为标准 `RunAgentInput`，响应使用 SDK `EventEncoder` 输出 SSE。旧 `/commerce/intents`、WebSocket 和订单接口保持可用。

## 运行条件

沿用 README 中的模型凭据与启动方式：

```bash
uv sync
uv run python -m uvicorn app.presentation.server:app --port 8000 --workers 1
```

AG-UI 当前直接在 API 进程执行，不经过旧意图接口的 Redis Stream 队列。同进程新旧入口共用会话锁。首版以单 API worker 运行为边界；尚不支持分布式会话互斥、事件重放或运行恢复。使用本地 Qdrant 文件模式时，同一数据目录也不能同时由多个进程打开。

AG-UI 不读取或写入现有的最终文本语义缓存，因为该缓存没有商品结果，命中后无法恢复真实商品卡。embedding 缓存仍正常使用；旧接口的语义缓存策略保持原样。以后只有把经过版本校验的完整结果一起缓存，才适合为 AG-UI 恢复该能力。

## 请求契约

```json
{
  "threadId": "session-unique",
  "runId": "run-unique",
  "state": {},
  "messages": [{"id": "user-message-unique", "role": "user", "content": "推荐旅行三件套"}],
  "tools": [],
  "context": [],
  "forwardedProps": {"buyerId": "buyer-demo", "locale": "zh-CN", "currency": "CNY"}
}
```

每次新意图使用新的 `runId`，同一会话保留 `threadId`。`messages` 最后一项必须是非空纯文本用户消息，之前的消息用于前端展示同步；模型历史从后端 AgentState 恢复，不重复灌入客户端历史。`forwardedProps.buyerId` 暂沿用现有买家标识契约，并不等价于已实现身份认证。

客户端的 `state` 不覆盖服务端商品或交易事实。当前不支持非空 `tools`、`resume`，提交会返回 422；因此不能把本接口称为已完成交易人工确认。不要把重发原请求当作断线恢复或幂等重放。

## 事件和展示状态

- 每个请求先发 `RUN_STARTED`，成功以 `RUN_FINISHED` 收口，失败以 `RUN_ERROR` 收口。
- 文本由 AgentScope 原生 block 开始/增量/结束映射为 `TEXT_MESSAGE_*`。
- 工具参数流映射为 `TOOL_CALL_START/ARGS/END`。`END` 只表示参数齐备，实际执行结果由 `TOOL_CALL_RESULT` 表达，关联同一 `toolCallId`。
- 商品状态来自本轮真实 `product_search_tool` 结果，包括子 Agent 检索结果。不会从模型回答提取或填充演示商品。
- `cache.hit`、`model.fallback`、`context.compressed` 等通知用 `CUSTOM`，可恢复错误通知不会提前终止 run。
- 最终 `MESSAGES_SNAPSHOT` 保留请求中的完整历史和本轮用户消息，追加经过输出审核的最终回答，替换本轮流式中间消息。

`STATE_SNAPSHOT.snapshot` 的当前契约：

```json
{
  "products": [],
  "searchCompleted": false,
  "status": "queued",
  "progress": [{"id": "tool-call-id", "label": "检索商品", "status": "running"}]
}
```

`products` 为真实商品卡数组；`searchCompleted` 每轮初始为 false，收到搜索结果（包括空数组）后为 true。`status` 为 `queued/running/completed/error/cancelled`；进度项状态为 `running/completed/error`。快照需要整体替换，不能将本轮空数组与上轮商品合并。

HTTP 断开会取消正在执行的 Agent，关闭原生生成器并持久化中断事实、释放会话锁；已经断开的连接无法继续接收终止事件，前端需要在用户主动停止时同步显示“已停止”。停止不能撤回已经完成的业务写操作。

## 验证

```bash
uv run pytest tests/test_ag_ui.py -q
```

离线测试用原生事件夹具替代付费模型，商品查询实际调用 `CatalogSearchUseCase`。覆盖真实商品投影、空结果清理、工具调用关联、历史保留、失败、同会话串行以及 ASGI 断连取消（包括 AgentScope 将取消异常转换成中断事件的情况）。真实网关端到端验证需使用可用模型凭据另行运行。
