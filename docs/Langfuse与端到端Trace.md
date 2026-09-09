# Langfuse 与端到端 Trace

当前版本复用 OpenTelemetry OTLP/HTTP 和 AgentScope 2.0.6 原生 `TracingMiddleware`，直接依赖的 OTel API、SDK、HTTP exporter 均锁定为 1.44.0。配置端点后可以导出跨 API、队列、worker、Agent、模型与工具的同一条链路。

**2026-09-09 更新：本机接收器验证和真实 Langfuse 项目的 API 回查均已通过。** 当前结果包含真实商品业务 Trace 及其评分回读，不只是项目鉴权成功。证据见 [远端 Trace 与评分报告](../eval/verification/slash-skill-langfuse-20260909/trace-and-score-remote.json)。此前未配置凭据的记录保留为早期阶段事实，不再代表当前状态。

## 当前远端验收结果

北京时间 2026-09-09 15:14:17，目标项目 `cmttqx7930a9aad0do3ftfd7q` 的真实商品业务 Trace `2e86df81c08bccf146fe559fb5b2a71e` 经公开 API 回查得到 `VERIFIED`：API 1、Agent 1、模型 2、工具 1，共 5 个已结束且无错误的 observation；单根、4 条父子关联，无缺失父节点，组件祖先关系校验通过。模型用量合计输入 **13,969**、输出 **184** Token。评分按项目、Trace 主体、评分 ID、名称、类型和数值核对通过。

这证明远端项目中实际存在可检索的业务链路和对应评分。**未登录 Langfuse 网页 UI 操作，不宣称网页人工验收；本轮也未做包含 worker 的远端队列链路验收。** worker 跨进程关联保留既有本机真实 OTLP/Redis 验证结果，不能借这条直接执行的商品 Trace 宣称远端 worker 已通过。远端未返回成本，`cost_usd` 保持 `null`，不是 0，也不估算费用。

## 配置

支持下列标准环境变量，Docker Compose 的 app 和 worker 均已透传认证和端点配置：

| 变量 | 行为 |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 基础地址，自动补 `/v1/traces`；已经包含该后缀则不重复补 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Trace 专用完整地址，优先于基础地址，按原地址发送 |
| `OTEL_EXPORTER_OTLP_HEADERS` | 通用认证头，逗号分隔的 `name=value` |
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | Trace 专用认证头，优先于通用头；支持百分号编码 |
| `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` | 导出超时秒数，默认 5；未设置时读取通用 `OTEL_EXPORTER_OTLP_TIMEOUT` |
| `OTEL_SERVICE_NAME` | 进程服务名称；Compose 分别设置为 `globex-api` 和 `globex-worker` |
| `LANGFUSE_BASE_URL` | Langfuse 项目所在地域的基础地址，例如 `https://cloud.langfuse.com` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 项目公钥和私钥；无显式 OTLP 端点时自动派生 Basic Auth、v4 header 和完整 Trace 端点 |

全部端点为空时不创建 exporter，业务接口保持可用。无有效 OTel SpanContext 时不会生成假的 Trace ID。认证头不出现在 Settings 的 repr 和本模块日志中；地址不允许嵌入账号、查询参数或 URL 片段。

三个 `LANGFUSE_*` 字段均配置后即可启用，且密钥不出现在配置对象 repr 中。显式 `OTEL_EXPORTER_OTLP_*` 配置继续优先：只有最终端点与该 Langfuse 配置派生的端点完全相同时才自动附带项目认证，绝不把 Langfuse 密钥发到另一个 collector；显式认证头按大小写不敏感规则覆盖自动值。HTTP 仅允许本机回环验证，远端 Langfuse 基础地址须为 HTTPS。

API 的配置读取与 CLI 默认规则相同：环境变量优先，项目根 `.env` 兜底。CLI 可显式指定 `--env-file /本机路径/文件.env`，该文件中的三个字段覆盖同名环境；不会展开 `${其他秘密}`，也不读取无关模型字段或要求 `LLM_API_KEY`。真实密钥只存本地被忽略文件或部署环境，不复制到命令行参数、报告或 Git。

Langfuse 使用项目公钥和私钥组成的 Basic Auth，Trace 完整地址为 `/api/public/otel/v1/traces`。当前官方说明建议携带 `x-langfuse-ingestion-version: 4`，以使用 v4 实时摄取；旧部署应按对应版本核对。来源：[Langfuse OTLP 接入文档](https://langfuse.com/integrations/native/opentelemetry)。

配置示意，尖括号值需由部署环境注入；不要将真实认证值提交到仓库：

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='https://<your-langfuse-host>/api/public/otel/v1/traces'
export OTEL_EXPORTER_OTLP_TRACES_HEADERS='Authorization=Basic%20<base64-public-key-colon-secret-key>,x-langfuse-ingestion-version=4'
export OTEL_EXPORTER_OTLP_TRACES_TIMEOUT=5
```

## 链路与关联字段

```text
HTTP 请求 span
  └─ commerce.intent.consume（队列 worker）
       └─ invoke_agent ...（AgentScope）
            ├─ chat ...（模型）
            └─ execute_tool ...（工具）
```

AG-UI 直接执行时省去队列消费 span，AgentScope 接在 HTTP span 下。纯 ASGI middleware 覆盖整个 SSE 响应生命周期，不读取、缓存或修改业务正文。

- API 接收标准 `traceparent` / `tracestate`；通过 W3C propagator 校验并提取，不接收任意 baggage。没有上游时创建本次请求的根 span。
- API 返回 `X-Request-ID` 和存在有效链路时的 `X-Trace-ID`，CORS 已暴露这两个响应头。
- 每次 HTTP 请求生成新的 `X-Request-ID`。业务提交体里的 `request_id` 仍承担重复提交去重，两者职责独立；同业务提交重试可以拥有不同 HTTP Trace。
- `IntentTask` 保存 `traceparent`、`tracestate`、传输层 `request_id`，旧消息缺字段时默认空。worker 提取后创建消费 span，重投会产生同一父链路下新的执行 span。
- Trace 传输字段不参与队列业务指纹，同一业务提交不会因为关联上下文变化而冲突。
- `TradeEvent.correlation` 与日志记录附带 `request_id`、`task_id`、`session_id`、`trace_id`、`span_id`；缺少上下文时省略或显示 `-`。其中会话标识使用稳定 SHA-256 摘要。
- 所有子 span 通过 `CorrelationSpanProcessor` 获得相同关联属性，并映射 `langfuse.session.id`、`langfuse.trace.metadata.request_id/task_id`。这样模型、工具步骤也能按会话和任务检索。

标准上下文传播依据：[OpenTelemetry Python propagation](https://opentelemetry.io/docs/languages/python/propagation/)。

## 默认导出内容

AgentScope 原生中间件会在内存中构造输入、输出、工具参数/结果与异常详情。本工程在 OTLP exporter 前加入明确白名单，导出前删除自由文本，且不改变业务工具返回值。

保留：父子 Span ID、Trace ID、时间与耗时、HTTP 方法及路由模板、HTTP 状态、模型/工具名称、Token 用量、错误类型、取消标志、请求/任务关联及会话摘要。模型、Agent、工具映射为 Langfuse 的 generation、agent、tool。

删除：对话全文、system prompt、工具描述/定义、工具参数与结果全文、异常正文和 traceback、原始 buyer/session 标识、非白名单 resource/scope/link 属性。输入输出仅保留字符数和 `globex.content.redacted=true`。不提供通过配置绕过此过滤的开关。

HTTP span 记录路由模板，例如 `/commerce/orders/{order_id}`；不记录实际 URL、查询参数、请求头或请求/响应 body。认证失败时接收器的响应正文也不会写入本模块日志。

Exporter 使用后台批量线程和有界队列；导出失败记录脱敏告警，不改变订单、搜索或队列结果。进程退出由 SDK 的关闭流程处理剩余批次，也提供 `shutdown_tracing()` 供容器生命周期主动关闭。

## 本机验收

```bash
.venv/bin/python -m pytest tests/test_tracing.py -q
```

专项测试启用真实本机 `ThreadingHTTPServer`，通过真实 OTLP HTTP exporter 发送并解析 protobuf，验证：

1. `/api/public/otel/v1/traces` 路径、Basic 认证、v4 header、Content-Type。
2. 真实 `_enqueue`、`IntentTask` JSON 边界和 `execute_intent_task` 的父子关系。
3. 原生 AgentScope reply/model/tool 中间件生成的五个 span，以及模型 13/7 Token 用量。
4. HTTP 上游 W3C 父上下文和 tracestate、并行 worker 隔离、SSE 生命周期。
5. 事件与日志关联、取消及错误状态、导出字节中没有测试手机号、地址、正文和原始身份。
6. 未配置时正常返回业务结果，导出器失败不会向日志泄漏异常正文。

本节测试中的模型与工具回调只返回本地夹具，不调用外部模型、不创建真实订单。它证明本地传输、结构和脱敏闭环；本节结果与上方新增的真实远端 API 验收分别留证，不能用本地夹具替代远端结果。网页 UI 操作和项目保留策略不在本轮远端验收范围内。

## 真实项目只读验收

`scripts/verify_langfuse.py` 只发送 GET，不造 Trace、不写评分、不启动或重启服务。先在目标项目创建项目 API keys 并通过上述配置注入，再读取鉴权证据：

```bash
.venv/bin/python -m scripts.verify_langfuse --env-file /本机路径/langfuse.env
```

这只会得到 `PROJECT_ACCESS_VERIFIED`，`remote_trace_verified` 仍为 `false`。配置缺失时返回 `CONFIGURATION_MISSING` 和缺失字段名，不发网络请求。使用真实业务响应的 `X-Trace-ID` 进行完整检索验收：

```bash
.venv/bin/python -m scripts.verify_langfuse \
  --env-file /本机路径/langfuse.env \
  --trace-id '<真实32位小写十六进制Trace ID>' \
  --require-components api,agent,model,tool \
  --wait-seconds 30 \
  --output eval/langfuse-verification.json
```

队列路径将 `--require-components` 设为 `api,worker,agent,model,tool`；AG-UI 路径无需 worker。默认只查一轮；`--wait-seconds` 是允许再次轮询的时间窗口，最大 600 秒，不是整个命令的严格墙钟超时。每个 HTTP 请求另有 15 秒超时，一轮可能需要多页请求；已经开始的本轮读取完成后才检查轮询窗口。默认检索时间窗为过去 24 小时，可通过 `--lookback-hours` 调整至最多 31 天。有外部父节点未导出、span 未结束、错误记录、缺组件、模型用量未知或缺分页时都不会报告完整链通过。Token 只累计 generation，避免父 Agent 汇总重复计数；成本未返回则保留 `null`。报告不包含对话、工具正文、原始用户/session、项目名称、密钥或上游异常正文。

对业务反馈或评测流程已产生的真实数值评分，可在上面命令追加以下参数。必须同时匹配评分 ID、项目、Trace 主体、名称、类型及数值；脚本不会自行创建评分：

```bash
--score-id '<真实score ID>' --score-name feedback_helpful --score-value 1
```

当前官方读取路径为 `GET /api/public/projects`、`GET /api/public/v2/observations?traceId=...` 和 `GET /api/public/v3/scores?id=...&traceId=...`；Observation 只请求 `core,basic,model,usage,metrics`，评分只请求 `subject`。返回无 `io`、`metadata`、`details` 和 `annotation` 请求；即使上游意外多返也不写入报告。来源：[Public API](https://langfuse.com/docs/api-and-data-platform/features/public-api)、[Observations API](https://langfuse.com/docs/api-and-data-platform/features/observations-api)、[Scores API](https://langfuse.com/docs/api-and-data-platform/features/scores-api)。

新专项 `tests/test_langfuse_verification.py` 使用本机真实 HTTP 服务覆盖 GET 认证、分页和父子链、严格用量、评分关联、错误脱敏，以及自动配置驱动真实 OTLP exporter 的路径、认证、v4 header 和正文过滤。`tests/test_langfuse_parentage.py` 另拒绝把模型/工具平铺在 API 下面的假完整链，允许包装 span 和嵌套 Agent。以上本机结果不替代远端报告；当前远端通过结论以上方 5 个 observation 与评分实际回查的独立报告为准。
