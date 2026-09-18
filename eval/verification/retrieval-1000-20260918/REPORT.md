# 千件商品目录与专用 reranker 交付记录

日期：2026-09-18。范围：Globex 工程代码、数据和评测；未修改教程。未提交或推送远端。远端 main 核对为 `f0ac5a84ae2092ccf0cfd13e1c7b804b17e80e06`。

## 1. 交付状态与远端故障

**专用 reranker 链路已经实现并通过真实本机服务验证；原远端网关仍然阻塞，不能把替代服务通过说成远端恢复。**

原配置向 `/v1/services/reranker` 请求 `qwen-text-rerank`，网关返回 HTTP 200，但正文为 `success=false`、`未找到模型对应的服务`。更换为官方文档中的模型名、分别使用扁平和 DashScope 嵌套请求均未解决；`/models` 也未提供可用列表。证据不足以进一步区分模型未注册、路由映射或账号可见性问题，需要该网关实际可用的模型 ID / 去密钥调用示例。没有向其它远端供应商转发现有密钥。

同时复现 embedding 单条输入仍返回 HTTP 200 空正文，HTTPS 也一样。因此不能继续把该现象确定归因为“批量超过上限”。[远端预检](remote-preflight.json)退出非零。

已修复客户端和装配：

- 删除聊天模型精排实现；工厂只允许 `http / disabled`，旧 `llm` 配置直接拒绝。
- 增加独立 `RERANKER_API_KEY`、协议 `flat / dashscope`、超时设置及 API/worker 配置传递。
- 保留完整接口路径；识别 HTTP 200 业务错误、空/非法 JSON、缺项、重复索引、非有限分数，不把失败伪装为精排成功。
- 明确请求全部候选；完全相同的文本只打分一次，再恢复原商品位置，不合并 SKU、价格或配送条件。
- 精排调用单独计量；网关未提供的 usage 保持未知。配置对象不再把 API 密钥输出到 repr。

本机替代服务提供 `/v1/embeddings` 和 `/v1/rerank`，没有 `/chat/completions` 路由。使用 `BAAI/bge-small-zh-v1.5` 与 `Xenova/bge-reranker-base` 的 ONNX int8 导出，FastEmbed 执行，CPU 4 线程。它是跨编码器相关性模型，不通过提示词让聊天模型排序。[本机预检](local-preflight.json)中背包得分 6.9021、咖啡杯 -10.1552；这些是模型原始相关性分数，不是概率。

服务只监听本机，不自动修改 `.env`。BGE embedding 长文本按 tokenizer 窗口分块，按 token 数加权聚合且返回 `chunk_counts`，不截掉尾部；reranker 查询/文档对超过 512 token 则显式拒绝。兼容 AgentScope 的 `encoding_format=float`。这不等同于原生长上下文模型。

模型来源及具体文件哈希见 [models.json](models.json)。参考：[BGE 官方模型说明](https://huggingface.co/BAAI/bge-reranker-base)、[ONNX 导出](https://huggingface.co/Xenova/bge-reranker-base)、[阿里云 rerank 协议](https://help.aliyun.com/zh/model-studio/text-rerank-api)。这些文档不能证明项目使用的内部网关开通了同名模型。

## 2. 商品数据

| 平台 | 商品条目 |
|---|---:|
| Amazon | 250 |
| eBay | 250 |
| Etsy | 250 |
| Walmart | 250 |
| 合计 | 1,000 |

共 1,705 个 SKU。`data/catalog-v2.jsonl` 是运行时默认目录，Docker 镜像同样打包 v2；`catalog-v1.jsonl` 原文件保留。v2 前 500 条与 v1 完全一致，包括 P1003-S1、已有价格、SKU 和商品 ID。

新增 500 条是 125 个合成型号在四个平台的商品记录，不冒充 500 个不同物理型号。覆盖 32 种商品主题、8 大品类，以及功率、容量、降噪、接口、尺寸、材质、库存、币种和配送差异。同一型号的商品事实相同，报价/库存/配送可不同。生成器可重复运行；`evaluation_*` 元数据不进入领域对象或可检索文本。

全目录仍包含原 v1 的教学模板商品，不是生产目录。新增商品、品牌、评分与平台报价均为合成数据，不是真实抓取或在售保证。

## 3. 实验方法与边界

- 72 场景：开发集 18，留出集 54；按商品主题分组隔离，其中留出集 48 条有结果、6 条预算不可能满足。
- 查询覆盖口语用途、精确规格及硬约束。新增商品查询明确限定 Roamix 系列，避免将其它品牌同用途商品不公平判为错；这也限制了结论的外推范围。
- K=5，召回窗口 32，加权 RRF 为 1:1；只用开发集检查实现和精排文本去重，留出集不调检索参数。
- 纯向量对照与混合策略共用权威过滤、候选窗口和实体去重，仅词项侧权重设为 0。旧词项兜底也是对照之一，但不能视作原远端在线模型基线。
- 真 HTTP embedding、Qdrant 本地稠密索引、BM25、HTTP reranker；没有 Qdrant 稀疏索引，也没有 LLM 精排或 LLM 评审。
- 五组交错执行，全部运行使用隔离索引。耗时是检索模块耗时，包含查询向量化与精排，不是整轮 Agent/网页响应时间；目录建库在测量前完成。
- 同等相关项采用二元 NDCG，不按商品 ID 的金标顺序制造偏好。置信区间按商品主题成对 bootstrap，避免把同主题的两条查询当成完全独立样本。

## 4. 开发集与精排去重

[初次开发集](dev-initial.json)和[去重后的开发集](dev-final.json)均为 Recall/MRR/NDCG=1.0、硬约束违规 0。16 条非空请求实际精排，2 条空结果不精排。

| 指标 | 完整重复文本请求 | 完全相同文本复用分数 |
|---|---:|---:|
| reranker tokenizer 输入量 | 86,191 | 35,049 |
| 模块 P95 | 1,452 ms | 856 ms |

这反映本机模型实际编码 token 数，不是云服务账单节约。单次本机实验的耗时会受 CPU 负载影响。

## 5. 留出原始结果与金标审计

原始 54 条留出输出和未改动的金标分别保存在 [holdout-original.json](holdout-original.json)、[dataset-before-audit.jsonl](dataset-before-audit.jsonl)。原始统计如下：

| 策略 | Recall@5 | MRR | 二元 NDCG@5 |
|---|---:|---:|---:|
| 纯向量 | 94.27% | 0.9132 | 0.9102 |
| 混合召回 | 94.79% | 0.9045 | 0.9134 |
| 混合 + reranker | 98.44% | 0.9757 | 0.9730 |

查看失败样本后，人工逐项核对全部场景，发现四条口语查询的金标未落实查询中已明确的规格：扩展坞需要网口、分装瓶需要防漏、鼠标需要静音、文件夹需要 A4。修正这些标签，没有更改查询、切分、约束、模型输出或检索参数。

`scripts/eval/rescore_retrieval.py`只对原先保存的同批输出重算，新增模型调用为 0；它会拒绝查询或切分被改动的输入。[纠错结果](holdout-audited.json)是**标签纠错敏感性分析，不是新增的独立留出验证**：

| 策略 | Recall@5 | MRR | 二元 NDCG@5 | 模块 P95 |
|---|---:|---:|---:|---:|
| 旧词项兜底 | 65.97% | 0.8125 | 0.7016 | 19.6 ms |
| BM25 | 89.76% | 0.7566 | 0.7989 | 26.1 ms |
| 纯向量 | 94.79% | 0.8889 | 0.8986 | 76.2 ms |
| 混合召回 | 94.97% | 0.8802 | 0.8979 | 71.2 ms |
| 混合 + reranker | **98.96%** | **0.9653** | **0.9682** | **913.8 ms** |

纠错后，完整链路相对纯向量的 NDCG 增量为 +0.0696，场景族配对 95% 区间为 [+0.0188, +0.1294]；Recall 增量为 +4.17 个百分点，区间为 [0, +9.375] 个百分点。只做混合召回的增益区间跨 0，不能宣称混合召回单独已稳定优于纯向量。

54 条均真实执行向量检索；48 条非空请求均真实执行专用 reranker，无精排降级。全部五组硬约束违规及空结果误判均为 0。留出精排记录 113,922 个 tokenizer 输入 token；未测量电力/货币成本，不能按云模型单价换算。

还存在 5 条完整链路排序不完美的样本，详见 [ranking-failures.json](ranking-failures.json)：

- `v2-07-intent` 把“出门手机没电、能放包里补电”的充电器排在移动电源前，Recall=0.5，是仍待改善的实际语义问题。
- 压缩收纳、地铁降噪、扩展坞网口、静音鼠标场景中，正确商品召回齐全，但某些不满足细节的型号仍排在正确型号之间或之前。

不根据这些留出答案改写检索规则。后续可加入新的开发样本或比较更强的专用 reranker，再用新的留出集验证。当前结论是合成目录的模块级收益，不是生产质量保证，也不是完整 Agent 发版验收。

## 6. 验证与复现

- 后端全量 **1,088 passed**，见 [日志](backend-tests.log)。保留原始目录扩容后暴露的 [4 条旧测试夹具失败](backend-initial-failures.log)，修正为固定 v1 契约夹具后全量复测通过。
- 前端 **116 passed / 12 test files**，TypeScript/Vite 生产构建通过，见 [测试](frontend-tests.log)、[构建](frontend-build.log)。
- `uv lock --check` 与 `docker compose config --quiet` 通过；未启动 Docker daemon 或构建镜像。
- 112 个教程文件内容哈希与本轮开始时完全一致；只修改工程及配套数据/说明。
- 有一个 Starlette TestClient 弃用警告，不是测试失败。

[工程组装及重启验证](composition-smoke.json)：使用真实 composition、独立 SQLite，1,000 商品 / 1,705 SKU 初始化成功；P1003-S1 模拟扣减后的库存重启仍保留；45 篇品类知识建库与检索成功；实际选购查询返回 `hybrid_rerank`，聊天模型调用 0。隔离环境禁用 tracing，不以该检查代表远端 Langfuse 可用。

启动集成过程中发现并修复本机服务不接受 SDK `encoding_format`、长知识文本超过模型窗口的问题。商品冻结评测使用的 1,000 条短文本向量与最终服务完全一致，最大绝对差 0，见 [embedding-parity.json](embedding-parity.json)。原单元测试的二元向量桩无法区分新增同类商品，因此链路契约测试固定原 v1 目录；新增 v2 目录由独立规模检查、硬约束测试和上述真实模型对照覆盖，没有为了通过旧断言删除新商品。

运行方式见 [工程 README](../../../README.md)。最小命令：

```bash
uv sync --locked --extra retrieval
uv run --extra retrieval python -m scripts.retrieval_server --port 18081
# 第二个终端按 README 设置本机 embedding/reranker 环境变量
uv run python -m scripts.retrieval_preflight --output .pytest_cache/preflight-new.json
uv run python -m scripts.eval.retrieval_v2 --split dev --live --output .pytest_cache/dev-new
uv run python -m scripts.eval.retrieval_v2 --split holdout --live --output .pytest_cache/holdout-new
```

金标审计复算（不请求模型）：

```bash
uv run python -m scripts.eval.rescore_retrieval \
  --report eval/verification/retrieval-1000-20260918/holdout-original.json \
  --old-dataset eval/verification/retrieval-1000-20260918/dataset-before-audit.jsonl \
  --dataset eval/v2/product_retrieval.jsonl --catalog data/catalog-v2.jsonl \
  --output .pytest_cache/holdout-rescored-new.json
```

`--live` 在服务阻塞时退出非零并保留报告；不允许用健康词项侧代替模型侧宣布通过。新版金标的未来模型重跑不应冒充本报告的原始冻结实验。

## 7. 回滚与待办

远端配置保留，默认 `HYBRID_RECALL_ENABLED=0`，本地替代服务需要按 README 显式启动。更换远端供应商/模型时先通过预检，向量模型变化应使用新的商品和知识集合名，不能混用不同维度。

回滚检索可设置 `HYBRID_RECALL_ENABLED=0`；要停止精排设置 `RERANKER_MODE=disabled`。停止本机服务前恢复相应 endpoint 或接受明确降级，不需要清空会话、偏好、订单等数据库。目录 v1 文件仍保留，但回退代码/目录时应先检查新增商品是否已有订单，不删除库存账本。

仍待外部信息解决的事项：当前远端网关的实际 reranker 服务映射/可用模型和 embedding 空响应原因。本次未自行改成另一个远端服务，也未用 LLM 精排绕过。
