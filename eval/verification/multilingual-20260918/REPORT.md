# 多语言商品、词项召回与 qwen-text-rerank 验证（2026-09-18）

## 交付与当前阻塞

已将默认目录从 1,000 件扩为 **3,700 件 / 7,105 SKU**，新增每平台、每语种各 100 件；修复 Unicode 分词，并增加低权重子词匹配来处理复合词。旧商品、SKU、价格等原始字段完整保留。库存初始化改为事务内批量读取，重启不重置已扣减库存。

**指定模型确实按 `qwen-text-rerank` 请求，但网关仍返回 HTTP 200、`success=false`、`未找到模型对应的服务`。远端 embedding 同时返回空响应。真实 Hybrid + Qwen 精排验收尚未通过。** 本次未使用聊天模型精排，也没有用 BGE 的结果替代指定模型。应用保留原有开关；失败时按真实策略标记词项降级，不能显示 `rerank_applied=true`。

本轮只有工程目录修改。核对教程目录 112 个文件，没有修改或新增教程；没有写入真实买家数据库，没有提交或推送 Git。

## 1. 语种选择与数据范围

官方资料可以确认站点、locale 或翻译支持，但没有给出可统一比较的“商品语种热度/销量排名”。下表的常用与长尾是本项目的工程覆盖分组，是选型判断，不是平台商业排名。加拿大法语在 Walmart 中属于本次相对较少覆盖的市场分组，不代表法语本身是冷门语言。

| 平台 | 常用测试语种 | 长尾测试语种 | 新增 / 当前总数 |
| --- | --- | --- | --- |
| Amazon | 英、西、德、法、日 | 荷、波兰、瑞典 | 800 / 1,050 |
| eBay | 英、西、德、法、意 | 荷、波兰 | 700 / 950 |
| Etsy | 英、西、德、法、意、日 | 荷、波兰、葡萄牙 | 900 / 1,150 |
| Walmart | 英、西 | 加拿大法语 | 300 / 550 |

每个新增“平台 × 语种”组合恰好 100 件，共 27 组、2,700 条新 listing。每组是 25 类商品的 4 个规格，涵盖 8 个业务品类；不是同一件商品换 100 个编号。跨平台和语言共享 100 个实物型号的 canonical ID，方便检验同款去重及不同报价。原有每平台 250 件中文历史商品继续保留，不为凑整重写旧数据。

选择依据：

- Amazon 官方列出了欧洲、日本等站点；商品详情需要按目标站点语言处理。本轮从这些覆盖中选 8 种语言。[全球站点](https://sell.amazon.com/global-selling)、[Listing 翻译说明](https://sell.amazon.com/blog/amazon-listing-translation)。
- eBay 文档提供市场与 locale 的映射，包括荷兰和波兰。[请求头与 locale](https://www.developer.ebay.com/develop/api/sell/request_headers)。
- Etsy 同时提供界面语言列表及商品手动翻译能力；界面语言和商品正文语言是不同字段，不能直接用前者推断销售份额。[语言列表](https://help.etsy.com/hc/en-us/articles/115015651868-How-to-Change-Your-Language-Settings-for-Shopping-on-Etsy)、[商品翻译](https://help.etsy.com/hc/en-gb/articles/360000343048-How-to-Translate-Your-Shop-and-Listings)。
- Walmart 官方市场覆盖美国、加拿大、墨西哥、智利，加拿大支持英法搜索；因此本轮选英、西、法三种，没有虚构它的日语或德语站点。[市场范围](https://corporate.walmart.com/about/international/about/marketplace)、[加拿大英法搜索词](https://www.walmartconnect.ca/en/advertising-help/campaign-setup-and-management/campaign-setup-sponsored-products/step-5-add-select-keywords)。

字段边界：

- `source_language` / `source_locale` 表示商品内容语言及本次模拟的目标 locale；与产地、配送地、报价币种分开。
- 标题、说明、功能属性、材料名称及 SKU 规格均提供对应语言正文。`localized_category` / `localized_material` 用于检索；中文标准分类保留给确定性过滤，不把隐藏中文译文混进外语召回。
- 精确容量、功率、重量、数量、尺寸来自共享数值表，同款不能因翻译而改变事实。库存、配送地、币种、平台报价可以不同。
- 新增两档 SKU，覆盖缺货、部分缺货和目的地不支持；材质、报价过滤继续由业务代码负责。
- 数据均标为 `synthetic`，评分和商品规格也为演示设定，不是平台真实抓取、真实榜单或经认证的产品参数。语言文案为本次编写，未经母语编辑审核。

数量见 [catalog-manifest.json](catalog-manifest.json)。构建入口为 `scripts/generate_catalog_multilingual.py`；词表在 `scripts/catalog_multilingual_data.py`，默认目录为 `data/catalog-v3.jsonl`。v1、v2 目录及其旧评测文件均保留。

## 2. 实际代码改动

| 部位 | 变更及验证重点 |
| --- | --- |
| 商品与卡片 | 读取语言和本地化检索字段，卡片返回内容语言及合成来源；旧记录缺省字段向后兼容 |
| BM25 | NFKC + casefold 保留重音字母；汉字/假名二元词项；完整词项辅以 0.25 权重的字母三元子词，并按扩展量归一化 |
| 型号 | 数字、连接符型号不参与子词拆分，避免 WH-1000XM5 与 WH-1000XM4 因公共片段误召回 |
| 缓存 | LRU 只缓存正文派生词项，按正文和分词函数区分；不缓存商品库存或价格 |
| 库存启动 | 同一 `BEGIN IMMEDIATE` 事务内一次读取库存、一次聚合有效历史订单占用，再补新 SKU 和更新报价；维持重复 SKU、越界占用及 SKU 归属校验 |
| 运行打包 | Docker 在数据卷之外打包 v3 目录；运行清单记录 v3 哈希 |
| 旧实验 | v2 检索脚本显式加载 v2 目录，避免默认目录升级悄悄改变旧实验底座 |
| Qwen 配置 | `.env.example` 对齐指定模型 `qwen-text-rerank`；实际 `.env` 原本就是该名称，本轮未改凭据或替换模型 |

没有新增 Qdrant 稀疏索引。词项索引在应用层，稠密向量仍走现有 Qdrant 接口。

## 3. 评测方法与结果

### 3.1 范围与公平性

最终固定集共 **616 条**：216 条同语种，184 条英语查其他语种商品，216 条中文查外语商品。每条指定一个平台及一个目标商品语种，候选 100 件；跨语言候选中没有英语/中文译文来替外语商品答题。对照共 1,232 次实际词项检索。

这批是**模块诊断集**，覆盖 8 个购物意图族，不是全目录 3,700 件检索的留出验收，也不等同于 Agent 多轮选购成功率。查询为单独编写的自然表达，金标按品类意图、SKU 库存及配送资格构造；未表达容量偏好时，不能把某一档位伪造为唯一正确答案。100 件切片实验的低延迟也不能当成全目录 P95。

旧基线复用当前相同的过滤、RRF、候选数和去重，只换回旧 ASCII/Han 分词且关闭子词；它不是历史版本整个系统。两组按案例交错执行。固定 top_k=5、候选预算 32、RRF 权重 1:1、子词权重 0.25；没有训练或调用 LLM。

先完成 400 条诊断时发现“只改 Unicode”在德语、瑞典语复合词上回退，之后增加通用子词字段，保留原始结果。修复后又补中文跨语言案例，因此本批不能宣称是完全未见过的盲测集。原始 400 条查询和对应脚本快照一起保留。

### 3.2 完整结果

| 场景 | 条数 | 旧词项 Recall@5 | 新词项 Recall@5 | 旧 → 新 NDCG@5 |
| --- | ---: | ---: | ---: | ---: |
| 同语种 | 216 | 82.33% | **94.68%** | 0.8041 → **0.9357** |
| 英语 → 其他语种 | 184 | 13.27% | **26.22%** | 0.1209 → 0.2548 |
| 中文 → 外语 | 216 | 9.26% | **9.26%** | 0.0926 → 0.0926 |

三组均无库存/配送硬约束违规。中文跨语言几乎没有改善，不能通过词项优化宣称多语言语义检索已完成。英语跨语言的少量改善还包含同源词及共用技术词的作用。

同语种按 8 个商品意图族聚类后，Recall 差异 +12.35 个百分点，bootstrap 95% 区间为 [+4.94, +19.44]；NDCG 差异 +0.1316，区间 [+0.0535, +0.2075]。平台、翻译复本没有被当作独立意图样本；簇数小，结果仅供诊断。早期英语跨语言差异区间跨零，不据此声称稳定提升。

修复后各语种同语种 Recall：英 100%、西 92.19%、德 94.44%、法 100%、日 100%、荷 75%、波 97.22%、瑞典 87.5%、意 100%、葡 100%。仍有荷兰语等意图表达漏召回，保留完整失败明细，不隐藏未达标案例。

新词项在 100 件切片的同语种 P95 为 2.79 ms，旧词项 1.05 ms；只是本机一次模块测量。真实组合容器在 3,700 件目录的 5 次查询为约 58–118 ms，没有足够样本宣称该值是生产 P95。

数据和原始结果：

- [最终 616 条结果](evaluation-616.json)：冻结哈希、每条命中、分语种统计、聚类区间及远端预检。
- [所有失败明细](failures.json)：包括旧/新两组的 Recall、NDCG 不满分样本；不是仅记录抛异常。
- [仅 Unicode 的第一轮结果](unicode-only-400.json)、[子词修复后的 400 条结果](subword-400.json)、[原始 400 条固定查询](dataset-400.jsonl)。

### 3.3 指定远端模型

本轮实际向当前同一网关发起请求，模型名称始终为 `qwen-text-rerank`：

- 配置地址 `/v1/services/reranker`：flat 和 DashScope 格式都返回业务失败，`model_unavailable`。
- 已知常见替代路由 `/v1/rerank`、`/v1/reranker`、原生 rerank/text-rerank 路由均为 404；没有把这些猜测写成生产回退链。
- 同地址附带 `model` 查询参数仍失败。
- 远端 `text-embedding-v4` 的单文本请求也无有效向量返回。

证据：[路由检查](qwen-route-probes.json)、[参数检查](qwen-param-probes.json)、最终结果中的 `dependencies`。结果不包含密钥，不上传真实买家原文。

下一步需要该网关对 `qwen-text-rerank` 的有效路由/权限或成功调用示例；仅靠重复改客户端模型字符串无法证明服务已开通。没有获得真实 scores/usage，因此本次远端精排质量、token 消耗、实际账单成本均为未知，不能记作零。`all_requested_live_paths_executed=false`，在线命令按预期返回非零。

## 4. 测试、恢复与边界

- 全量后端：**1,110 passed**，94.06 秒；一个既有 Starlette TestClient 弃用提醒。[日志](backend-tests.log)
- 前端：**116 passed**，构建通过。[测试](frontend-tests.log)、[构建](frontend-build.log)
- 交易、语言与召回专项：82 passed；补充中文评测数据后的相关测试：37 passed。这些是全量测试的重叠子集，不与 1,110 累加。[专项](focused-tests.log)、[数据复核](dataset-tests.log)
- 真容器组装、隔离 SQLite：由上一轮隔离 v2 库升级，库存行 1,705 → 7,105；P1003-S1 的已保存库存 79 保留，测试改为 78 后重启仍为 78。五条德语、波兰语、日语、瑞典语查询返回真实新商品。[结果](composition-smoke.json)
- 该组合测试主动关闭远端精排，故意使用不可用的本机 embedding 地址验证词项降级。品类知识库也会告警降级；它证明目录、SQLite 和词项链路可用，不证明远端向量/知识库可用。
- 第一轮全量测试因定位目录扩容的逐 SKU 查询瓶颈主动中断，修复后重新完整跑过；[中断日志](backend-initial-interrupted.log)不计为通过。
- 教程 112 个文件哈希未变，未新增教程。真实买家数据未写入；只操作本轮独立测试库。[验证清单](verification.json)

## 5. 复现与回滚

在工程根目录执行：

```bash
uv run python -m scripts.generate_catalog_multilingual
uv run python -m scripts.generate_retrieval_multilingual
uv run python -m pytest tests/test_multilingual_retrieval.py tests/test_trade_store.py

# 仅词项对照，不要求模型服务可用
uv run python -m scripts.eval.retrieval_multilingual --output .pytest_cache/ml-offline-new

# 专门检查指定模型；不能换 BGE 后声称 Qwen 验收成功
export RERANKER_MODEL=qwen-text-rerank RERANKER_MODE=http
uv run python -m scripts.retrieval_preflight --output .pytest_cache/qwen-preflight-new.json
uv run python -m scripts.eval.retrieval_multilingual --live --output .pytest_cache/ml-live-new
```

地址、协议、独立密钥沿用有效 `.env`，示例不提供真实凭据。每次评测指定新输出目录，本地 Qdrant 索引与买家库隔离。模型不通会留下词项结果和 BLOCKED 状态并退出非零；不把降级当作线上验收通过。

回滚时恢复旧检索代码/开关，以及 `seed_products.py`、Dockerfile、运行清单对 v2 的引用即可回到千件目录。保留 v3、既有证据和 SQLite 中已生成的订单、确认及库存行，不删除使用数据。不得通过重置数据库来“回滚商品”。使用 v2 目录回查新 v3 商品前应先确认业务是否已经产生相关订单，必要时继续保留这些商品的只读查询入口。
