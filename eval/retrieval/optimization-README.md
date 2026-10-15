# 商品检索算法改进诊断

本目录补充标准 V1 的算法前后证据。所有本轮报告均为 `diagnostic`，与正式 Dense/HTTP 精排收益验收分开。

## 输入及源码隔离

- 改进前 `app`、目录和 v2 查询来自 `eval/verification/retrieval-algorithm-20261014-231640/source-before/`，以同级 `before.json` 核对完整来源清单。
- 运行器父进程不导入 `app`；子进程优先将 `--source-root` 加入 `sys.path`，并记录 `CatalogSearchUseCase` 实际导入文件。
- 当前固定标准计分器对新旧结果计分。源码、标准、评测程序及输入在运行前后均核对指纹；运行中变化会标记失败。
- `keyword` 与 `bm25` 是实际词法代码。`field_hybrid` 只启用字段词法入口，未配置向量服务时没有向量结果，不使用模拟向量，不证明 Hybrid 或 Dense 收益。
- 各输出目录及 JSON 使用独占创建；不得覆盖旧证据。正式运行应换新目录名。

## 独立的合成契约

`optimization-contract-catalog-v1.jsonl` 是 12 个独立合成商品；`optimization-contract-queries-v1.jsonl` 是 18 条固定查询，SHA-256 写入 `optimization-contract-manifest-v1.json`。

覆盖第二个可售 SKU 更便宜、明确 SKU、规格文字、换汇预算、低价缺货、超低预算、商品类型、已知/未知/超重重量、材质、配送和同款去重。金标依据合成字段事实先冻结，不从被测算法排序中选择。它不是实际商品目录人工金标，也不是生产效果样本。

原 v2 的 72 查询另行测量；二者不混在一个平均分里。v2 没有材质正例或预算正例，不能因此满足正式标准的覆盖门槛。

## 开发集选参

`optimization-development-grid-v1.json` 先于开发测量冻结三组字段权重 × 两档候选预算，共六组配置。

只使用 v2 的 18 条 `dev` 查询，与改进后的 BM25 开发报告比较：

1. Recall@5 不低于参考 BM25 − 0.01，返回精度不低于参考 BM25。
2. 合格配置按 NDCG@5、精度、Recall 从高到低选择。
3. 同分选更小候选预算，最后按配置 ID 排序。
4. 六组必须完整测量；缺项、源码变化、不同选集及 `holdout/all` 选参会被拒绝。
5. 选定后写新锁参文件，随后才运行留出集。有限网格胜出不等于全局最优。

## 报告及附加指标

每次运行输出：

- `report.json`：标准 V1 兼容报告，现有只读前端可以加载。
- `additional-diagnostics.json`：MRR@5、同款重复率、候选池 Recall、实际展示默认 SKU 的价格一致率、指定规格/默认 SKU 身份检查、类型与重量检查、预算错误排除、未知新约束、逐例实际阶段名单及按问题类型分组。
- `execution.json`：实际被测源码、输入及 Python 路径，来源清单验证结果；没有凭据或配置原文。

预算错误排除只统计 `filtered_out` 明确以 `over_price_cap` 拒绝、但存在符合预算与规格可售 SKU 的独立相关商品。未被召回或仅排序靠后不能归因于预算。价格一致率检查展示默认 SKU 与其目标币种价格一致，不表示选择了满足请求的规格；规格身份另列。

对于旧源码不支持的类型、重量、规格约束，运行器不悄悄省略证据：在附加逐例记录 `unsupported_constraints`，违规照真实展示结果计。没有适用结果时满足率显示未知，不能补零或当成功。

## 示例命令

```powershell
E:/anaconda3/envs/globex/python.exe -m scripts.eval.retrieval_optimization run --source-root eval/verification/retrieval-algorithm-20261014-231640/source-before --source-manifest eval/verification/retrieval-algorithm-20261014-231640/before.json --mode bm25 --split all --role baseline --output eval/verification/retrieval-standard/algorithm-before-example-new
```

候选使用冻结的改进后 `--source-root`；字段实验增加 `--parameters eval/retrieval/optimization-parameters-focused-32.json --mode field_hybrid`。合成契约增加：

```text
--catalog eval/retrieval/optimization-contract-catalog-v1.jsonl --dataset eval/retrieval/optimization-contract-queries-v1.jsonl
```

开发网格跑完后选择参数，六组 `--candidate` 都需提供：

```text
python -m scripts.eval.retrieval_optimization select-parameters --baseline <改进后BM25-dev/report.json> --candidate <配置1/report.json> ... --candidate <配置6/report.json> --grid eval/retrieval/optimization-development-grid-v1.json --output <新的锁参JSON>
```

真实 Embedding/HTTP 精排评测需要独立的实际调用证据、相同输入候选及模型、成功率、耗时和计费账本；本运行器不调用外部服务。诊断成绩不自动修改默认策略、`.env` 或线上阈值。

## 本轮结果与使用边界

同批真实 Dense 候选的 54 条留出集回放：NDCG@5 0.657131→0.903281，Recall@5 0.656250→0.989583，返回精度 0.241667→0.437500，重复率 59.58%→0。源码前后复用同一次实际 Embedding 与 Qdrant 检索结果，没有使用模拟向量，没有精排；耗时是回放耗时。真实请求 72 次，上报总用量 1662 tokens，金额未知。详见[完整对照](../verification/retrieval-algorithm-20261014-231640/live-dense-final-comparison.json)。

18 个合成合同全部满足预期，预算错误排除 2/4→0/4；它们只证明指定合同，不能代替真实目录的预算/材质正例。字段词法六组开发参数选出 `flat-32` 后，留出集 NDCG 0.841426→0.811850、Recall 0.918403→0.881944、精度 0.400000→0.379167。字段方案未达标，`FIELD_LEXICAL_ENABLED` 继续默认关闭，锁参文件不表示通过收益验收。

真实精排仍缺 `RERANKER_BASE_URL`，需核对 `RERANKER_MODEL`、`RERANKER_PROTOCOL` 与匹配凭据。百炼原生协议应按[官方文本排序 API](https://www.alibabacloud.com/help/zh/model-studio/text-rerank-api)使用完整服务地址和 `dashscope` 协议；兼容平铺协议才使用 `flat`。本机 `.env` 未修改；仅将示例模型名更新为文档支持的 `qwen3-rerank`，不能据此假定账号已开通。

相关性阈值只允许绑定实际评分类型并使用同模型开发集校准，默认关闭。正式启用字段策略或阈值前仍需完整收益门禁。本轮 Harness 受 5 项 Windows 权限合同失败阻断，模型 smoke 未执行，正式效果未验收。

阈值已用 `scripts/eval/retrieval_threshold.py` 完成受控校准：0.0～0.7 网格只在 dev 选择，模型身份与真实候选缓存绑定，再检查一次 holdout。0.7 因排序/召回退化被拒绝；选出的 0.0 没有实际过滤或指标增益，结论为不启用。见[校准结果](../verification/retrieval-algorithm-20261014-231640/threshold-calibration/summary.json)。270 条回放没有新增外部调用；这组参数消融不与源码前后的收益混为一谈。
