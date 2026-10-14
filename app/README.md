# `app/` 项目结构说明

本目录是 Globex 选购 Agent 的 Python 后端主体。下面按职责展示**代表性模块**；同一行列出多个文件时，它们共同承担该职责，完整文件清单以当前目录为准。这里的名称取自本项目源码，不沿用其他项目的记忆系统类名。

```text
app/
├── 接口层 presentation/（HTTP 与 AG-UI 边界）
│   ├── server.py                         FastAPI 装配、生命周期、健康检查及旧接口
│   ├── ag_ui.py                           AG-UI 流式请求、运行状态与事件读取
│   ├── ag_ui_runtime.py                   旧导入路径的兼容转发
│   ├── confirmations.py                  买家确认入口
│   ├── model_workspace.py / context_workspace.py
│   │                                     模型选择、上下文管理接口
│   └── buyer_workspace.py / automations.py 等
│                                         买家资料与自动化等页面接口
├── 应用层 application/（用例、Agent 编排与工具）
│   ├── agents/
│   │   ├── orchestrator.py               MainAgentOrchestrator：会话、身份、事件与持久化编排
│   │   ├── main_agent.py                 MainAgentFactory、SessionRegistry：Agent 构建与会话恢复
│   │   ├── search_agent.py / trade_agent.py
│   │   │                                 按需构建检索、交易子 Agent
│   │   └── ag_ui_adapter.py              将 Agent 和交易事件转换为 AG-UI 事件
│   ├── usecases/                          CatalogSearchUseCase、ConfirmationService、订单用例
│   ├── tools/                             商品检索、偏好、订单、任务派发等 Agent 工具
│   ├── memory/                            BuyerMemoryMiddleware：偏好注入；PreferenceSelector：后备选择策略
│   ├── agent_runtime.py                  AGUIRuntime：运行租约、日志与过期状态处理
│   ├── automation.py / learning.py        自动化执行与学习任务
│   └── prompts/ / harness/               版本化提示词与运行约束
├── 领域层 domain/（业务对象与端口合同）
│   ├── catalog/                           Product、Sku、Money、ProductSearchSpec 及检索端口
│   ├── buyer/                             BuyerPreference 与偏好存储端口
│   ├── order/                             Order、Address、OrderLine 与交易存储端口
│   ├── shipping/                          关税规则与运费报价对象
│   ├── session/ / queue/                  会话、对话、任务队列端口
│   └── automation/ / learning.py          自动化定义与学习策略合同
├── 基础设施层 infrastructure/（外部能力与端口实现）
│   ├── settings.py / llm.py / model_registry.py
│   │                                     配置、模型调用与版本化模型档案
│   ├── embedding/ / vector/              OpenAI 兼容 Embedding、Qdrant 商品索引及增量同步
│   ├── retrieval/ / rerank/ / rag/        BM25/RRF、可选精排、品类知识库
│   ├── persistence/                       商品内存仓、JSON/SQL 会话与偏好仓、SQL 交易账本（默认 SQLite）
│   ├── semantic_memory.py / cache/        SQLite 长期偏好记忆与可选缓存
│   ├── queue/ / eventbus.py / ag_ui_journal.py
│   │                                     Redis Stream 队列、跨进程事件与运行日志
│   ├── context_*.py / security/ / harness_middleware.py
│   │                                     上下文治理、输出安全与工具护栏
│   ├── tracing.py / operational_metrics.py
│   │                                     追踪与运行指标
│   └── commerce/                          本机商家沙箱协议适配
├── composition.py                         Container / build_container：集中装配上述依赖
├── worker.py                              可选 Redis 队列消费者入口
└── automation_worker.py                   独立自动化工作进程入口
```

## 主链路如何穿过各层

1. 网页通过 `POST /commerce/ag-ui/run` 进入 [`presentation/ag_ui.py`](presentation/ag_ui.py)；[`presentation/server.py`](presentation/server.py) 创建 FastAPI 应用并在生命周期中调用 [`composition.py`](composition.py) 装配依赖。
2. [`application/agents/orchestrator.py`](application/agents/orchestrator.py) 恢复会话，结合身份、模型、提示词、偏好和交易事实执行本轮选购；[`application/agents/main_agent.py`](application/agents/main_agent.py) 的工厂构建 AgentScope 主 Agent，必要时通过工具派发检索或交易子 Agent。`MainAgent`、`SearchAgent` 和 `TradeAgent` 是运行角色，不是同名自定义类。
3. Agent 工具调用 [`application/usecases/catalog_search.py`](application/usecases/catalog_search.py) 等用例。用例使用 [`domain/`](domain/) 的业务对象和端口，由 [`infrastructure/`](infrastructure/) 提供商品仓、Embedding、Qdrant、可选精排及持久化实现。检索候选仍经过代码中的预算、国家等硬条件过滤。
4. 订单与取消工具只准备确认；实际变更由买家确认入口和交易存储执行。详情可看 [`application/usecases/order_usecases.py`](application/usecases/order_usecases.py)、[`application/usecases/confirmation_service.py`](application/usecases/confirmation_service.py) 和 [`presentation/confirmations.py`](presentation/confirmations.py)。本项目处理本地意向订单，`infrastructure/commerce/` 是本机沙箱适配。

## 容易混淆的边界

- 网页 AG-UI 请求在 API 进程直接运行，并由 [`application/agent_runtime.py`](application/agent_runtime.py) 与 [`infrastructure/ag_ui_journal.py`](infrastructure/ag_ui_journal.py) 记录运行和事件。服务重启后可处理过期运行、回放已存事件，不会接续中断的模型生成。启用 Redis 队列也不会自动把这条请求交给 [`worker.py`](worker.py)；队列服务于旧 `/commerce/intents` 和异步任务入口。
- [`application/memory/`](application/memory/) 的中间件负责偏好注入；默认语义召回由 [`infrastructure/semantic_memory.py`](infrastructure/semantic_memory.py) 的 `SemanticPreferenceStore` 执行，`PreferenceSelector` 是非语义存储的后备选择策略。本项目没有按“工作／情景／语义／感知”四种记忆划分的实现，也没有 Neo4j 后端。
- Qdrant 商品索引默认使用本地嵌入模式；配置 `QDRANT_URL` 后才连接远端服务。Redis、HTTP 精排及独立 worker 等取决于配置，且独立自动化 worker 要求服务端 Qdrant。查看 [`composition.py`](composition.py) 的条件装配和 [`infrastructure/settings.py`](infrastructure/settings.py) 的实际参数。

阅读建议：先看 `composition.py` 的对象装配，再沿 `presentation/ag_ui.py` → `application/agents/orchestrator.py` → `application/agents/main_agent.py` → `application/tools/` 与 `application/usecases/` 追踪一次请求；需要替换外部服务时，再看 `domain/` 端口和 `infrastructure/` 适配器。
