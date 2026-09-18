# Globex 工程升级交付与评测（2026-09-18）

> 阶段快照：下文记录当天最初一轮实验。随后已撤除 LLM 精排，改为仅允许专用 HTTP reranker；固定选购表单已升级为 Agent 通过 `show_shopping_form` 动态定义问题。旧实验结果继续保留，当前状态及发布汇总见[每日工程更新](../../../docs/设计演进记录.md#每日工程更新)；“未提交或推送”指本阶段验收时状态。当前脚本的 `--live-rerank` 使用 HTTP reranker，不能用来原样复现已撤除的 LLM 精排试验。

本轮只更新 `线上版本/项目工程/globex-agent`。实施前已获取远端 `origin/main`（`f0ac5a84`）并核对差异，保留本地前序修复。未提交或推送。`2026-09-12` 教程目录的 112 个文件与实施前 SHA-256 完全一致。

## 交付范围

| 项目 | 实现内容 | 使用及发布状态 |
| --- | --- | --- |
| AgentScope 2.0.8 | 锁定依赖；兼容原生审批、压缩、usage、结构化生成策略回退；每次回退前释放流和限流名额 | 功能回归通过；保留 LoopDetector、现有记忆和上下文治理 |
| 混合检索 | 应用层 BM25 + Qdrant 稠密向量 + 可配权重 RRF；权威目录 ID 过滤；旧适配器渐进补召回；严格校验精排索引和分数 | `HYBRID_RECALL_ENABLED=0`，未使用 Qdrant 稀疏索引，权重 `1/1` 是初始值，不是最优参数 |
| 可选 LLM 精排 | AgentScope 结构化输出，共用线上网关限流和预算；单次至多 64 件/64,000 字符，超出或失败整批回退，逐次记录 usage | `RERANKER_MODE=http` 默认不变；`llm` 需显式选择 |
| A2UI 选购表单 | 自定义 ShoppingForm 目录；预算、币种、配送地和材质填写；AG-UI 扩展事件、SQLite 存储、CAS、幂等 run/message ID、按买家恢复 | 页面入口已接入；只保存本次需求，不执行订单或记忆写操作；不是完整通用 A2UI 渲染器 |
| GEPA 离线优化 | 优化搜索 Prompt 或公共 Skill 正文；训练/选择/留出分离；真实工具运行反馈；候选导出、反思计量；错误不算成功 | 可选依赖 `gepa==0.1.4`；不自动导入、审核或发布；本次没有选出更好的候选 |
| ACP 沙箱 | 固定 `2026-04-17` 契约子集；独立商家服务、报价、持久审批、金额绑定、幂等、库存事务、断线查询回执 | 仅回环 HTTP + 固定测试凭据；两件合成商品、CNY、每 SKU 一件；不接真实支付/物流，不加入生产 Agent 工具集 |

未实现多模态、降价/补货持续任务；未更换记忆存储，也未创建额外账号系统。

## 测试与实际链路

后端全量 **1073 通过**，前端 **116 通过**，类型检查及生产构建通过。最终源码与证据文件哈希记录在 [verification.json](verification.json)。对应日志：`backend-tests.log`、`frontend-tests.log`、`frontend-build.log`。

- 后端全量回归包含真实本地 Redis、SQLite、Qdrant 嵌入实例，以及 AgentScope 原生 Agent/权限/状态序列化。模型模拟测试与真实模型效果评测分开统计。
- 浏览器使用真实 Chrome、当前 React 页面、正式 FastAPI/AG-UI 路由和独立 SQLite；仅模型响应可控。通过 7 项操作检查，以及 3 项服务重启/无浏览器缓存/买家隔离检查。见 [browser.json](browser.json)、[browser-restart.json](browser-restart.json)。
- A2UI 服务端消息与动作由官方 v0.9 JSON Schema 加本项目目录校验；ACP 会话及完成回执通过官方 `2026-04-17` Schema。上游 Schema 和许可证保存在 `tests/contracts/`。
- ACP 还在真实回环 HTTP 上完成准备、批准、回执查询与重复批准，见 [acp-http.json](acp-http.json)。并发确认、归属/商家绑定、报价改变、拒绝、提交后断线分别有回归测试。
- `uv lock --check`、前端类型检查/生产构建、Compose 配置展开通过。当前主机没有可用 Docker daemon，未宣称完成容器镜像构建或真实第三方支付认证。

## 检索评测结果

数据来自既有冻结目录 `eval/v1/product_retrieval.jsonl`，K=8。具体数据、代码哈希、逐条输出和场景配对 bootstrap 95% 区间见 [retrieval.json](retrieval.json)。两个实验用途不同，不能混合汇总。

### 150 场景本地词项诊断

| 策略 | 平均 Recall@8 | MRR | NDCG@8 |
| --- | ---: | ---: | ---: |
| 原关键词 2-gram | 0.7333 | 0.7400 | 0.7331 |
| 应用层 BM25 | 0.7333 | 0.7356 | 0.7235 |

BM25 的 MRR 配对差为 -0.0044（95% 区间约 `[-0.0133, 0]`），NDCG 配对差为 -0.0096（约 `[-0.0186, -0.0035]`）。硬约束和空结果断言没有新增失败，但这不能证明 BM25 整体优于原路径。该实验包含既有 dev/test/release 数据，仅作模块诊断，没有用其挑选权重后再声称独立留出通过。

### 12 场景真实 LLM 精排试验

固定取 release 集前 12 项，使用已配置的 `qwen3-max`，每场景交错运行 BM25 与 BM25+LLM。

| 策略 | Recall@8 | MRR | NDCG@8 | 模块 P95 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 1.0000 | 0.9444 | 0.9583 | 约 12 ms |
| BM25 + LLM | 1.0000 | 1.0000 | 1.0000 | 约 6.64 s |

MRR 配对增量 0.0556，但 95% 区间约 `[0, 0.1667]`，不足以确认稳定收益。12 次精排中 11 次实际执行，一次遭遇 429 后按 BM25 降级；这是端到端实际表现，不能把它写成 12 次精排全部成功。

已知精排消耗小计 15,980 输入 / 2,562 输出 token，另有一次未知 usage，因此总实际消耗记为未知，不用零补齐。运行期间存在其他离线评测流量，延迟只能作为此轮诊断，不作为线上容量基线。相关统计见 [summary.json](summary.json)。

完整混合检索效果仍为 **BLOCKED**：当前 embedding 网关返回空响应；原 HTTP reranker 预检返回“未找到模型对应的服务”（见 [依赖预检](dependency-preflight.json)）。没有用确定性向量桩或 LLM 精排代替真实 embedding 后宣称混合收益。ID 过滤、补召回及权重算法有确定性回归，语义质量仍须在依赖恢复后验证。

## GEPA 真实实验

[gepa.json](gepa.json) 使用 12 个合成场景（train/val/holdout 各 4 个）、优化预算 12 次场景评估、随机种子 20260918。优化及留出复测共 20 次场景评估，另有 2 次真实反思调用；底层共记录 76 次模型请求。

这次评的是**搜索工具参数约束保留**：预算、币种、国家、材质与精确商品标识。运行真实 AgentScope Agent 和 `product_search_tool`，但工具范围固定为只读检索，没有假装覆盖完整主 Agent、知识库、订单或记忆链路。调用轨迹显示部分空结果查询会擅自放宽国家/预算，相关样本按失败处理。

最终选中的正文与基线相同。留出两次生成分数出现 1/4 与 2/4 的差异，这是同一提示词的随机波动，**不是 +25% 的优化收益**。优化器没有满足候选接受条件，不发布。

已知消耗小计 269,901 输入 / 19,381 输出 token，包含反思和留出复测；另有一次未知 usage，总量仍记未知。此数据是本次实验消耗，不是成本节省证明。

公共 Skill 的导出保持 `scope`、`allowed_tools` 等元数据，仅产生新 body、候选版本和报告引用。通过现有 `CapabilityRegistry` 校验，导入后仍为 draft；不能把修改提示词变成扩权。公共 Skill 导出链路有确定性测试，未单独宣称完成真实 Skill 收益评测。

## 本轮发现并修复的问题

| 失败 | 修复 | 验证 |
| --- | --- | --- |
| 新 SDK 在格式错误后切换生成策略，上一条流仍占据限流名额 | 每次结构化生成尝试结束即关闭所属资源，逐次结算 | 流式/非流式失败、3 次策略回退、取消及预算回归 |
| LLM 精排读取了错误的 SDK 返回层级 | 从 `StructuredResponse.content` 解析，并验证每个索引仅出现一次 | 单测及真实模型重跑 |
| GEPA 反思回调可能传字符串 | 统一为 AgentScope Msg；记录反思异常，不再将吞掉的失败算作成功 | 适配器单测及 2 次真实反思 |
| 空会话表单没有聊天历史可供定位，刷新丢失入口 | 按买家查询数据库最新表单并恢复会话；已有选中历史优先 | 真实浏览器刷新、重启、无缓存恢复 |
| 旧 AG-UI 快照可能覆盖表单已提交状态 | 从数据库重新读取权威版本，异步请求以 generation 排除过期返回 | 浏览器提交、重复继续及前端回归 |
| 精排返回重复/负索引、缺失/非有限分数时可能静默误排 | 整批校验失败后降级，不补零、不悄悄丢商品 | 参数化回归 |

首轮集成失败记录单独保留为 `gepa-initial-invalid.json`、`retrieval-initial-invalid.json`；它们不计为有效效果证据。没有抹去原始失败，也没有用后续单测结果覆盖质量结论。

## 复现与后续门禁

项目根目录执行；输出目录必须新建，测试不能使用真实 DATA_DIR：

```bash
uv sync --locked --extra optimization
uv run --extra optimization pytest tests -q
npm --prefix frontend test
npm --prefix frontend run build
uv run python -m scripts.eval.retrieval_upgrade --output .pytest_cache/retrieval-new
# 下面两项会真实调用已配置模型
uv run python -m scripts.eval.retrieval_upgrade --output .pytest_cache/rerank-new --live-rerank
uv run --extra optimization python -m scripts.optimize_prompts --output .pytest_cache/gepa-new --max-calls 24
```

真实 Redis 测试需要本机 `redis-server`；可通过 `GLOBEX_REDIS_SERVER_BIN` 指定可执行文件。页面表单和 ACP 的使用命令见 [工程 README](../../../README.md#本轮工程升级与使用2026-09-18)。

后续先恢复 embedding / HTTP reranker 服务，在开发集选权重与窗口，再冻结配置执行完整 hybrid-experimental 门禁。GEPA 应先补足开发集中的空结果后约束保留样本，再用新的独立留出和完整 Agent 成对评测验证候选。现有 12 个场景的小试验不能直接批准上线。

## 回滚与数据边界

- 检索回滚：`HYBRID_RECALL_ENABLED=0`，`RERANKER_MODE=http` 或 `disabled`，重启服务。不迁移 Qdrant collection，不建立稀疏索引。
- SDK 回滚必须同时恢复本轮前的 `pyproject.toml`、`uv.lock` 和模型适配代码，再 `uv sync --locked`；不能只降包版本、留下新版适配。数据库中原始对话和交易不删除。
- A2UI 表单保存在 `DATA_DIR/shopping_forms.db`；恢复旧前后端即可停用新入口，保留数据库以便后续恢复。表单不是订单授权。
- GEPA 未修改在线注册表、当前 Prompt、个人 Skill 或生产数据库；候选须人工审核，回滚无需撤销线上版本。
- ACP 数据只在显式给出的沙箱目录，停掉本机商家服务即可；没有真实扣款，也没有把沙箱订单混入页面订单账本。

上游依据：[AgentScope 2.0.8](https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.8)、[A2UI v0.9](https://a2ui.org/specification/v0.9-a2ui/)、[GEPA](https://github.com/gepa-ai/gepa)、[ACP 规范](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol/tree/7fdd78df677a94dce04c770644b0fbbb1401272b/spec/2026-04-17)。这些资料说明协议与机制，不替代本项目评测。
