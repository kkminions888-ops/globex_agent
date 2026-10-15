# 商品检索优化标准 V1

本目录建立商品检索准确性的前后对照标准。标准配置见 [`standard-v1.json`](standard-v1.json)，冻结的当前项目基线定义见 [`baseline-v1.json`](baseline-v1.json)。基线定义保存代码、商品目录、查询集合和安全配置投影的指纹；完成冻结不代表已经测出基线指标。

## 指标与默认验收目标

| 指标 | 固定口径 | 候选方案验收目标 |
| --- | --- | --- |
| NDCG@5 | 二元相关性，按实际展示的前五条的位置折损计算；同款重复只在首次出现时获得相关性得分 | 比基线增加至少 0.02 |
| Recall@5 | 前五条覆盖的不同相关同款数 / 全部相关同款数 | 比基线最多下降 0.01，即 1 个百分点 |
| 返回结果精度 | 前五条中不同的相关同款数 / 实际展示条数；不足五条不补空位，重复展示占分母 | 比基线增加至少 0.02，即 2 个百分点 |
| 硬约束违规 | 分别核对预算、材质和配送条件；记录违规商品数、适用场景数及正例覆盖 | 三类违规均为 0，且三类均有有效覆盖与正例 |
| 空结果判断准确率 | 仅在标注为应返回空结果的场景中统计，和正例指标分开 | 不低于基线 |

这些数值是本轮冻结的工程验收目标，不是已实现的优化收益。目标修改应建立新标准版本，不能在看到候选结果后悄悄放宽旧门槛。无样本、未知指标不按 0 处理。

## 对照实验约束

- 先冻结当前工作树、商品目录及库存快照、查询和金标、Embedding 身份、Top-K、切分和重复次数。代码与算法配置允许不同，这正是被测的优化变量；商品、金标、查询选择和实验条件必须一致。
- 相同需求的直接、同义和场景问法按场景族分组，避免开发集与留出集泄漏。开发集用于调参，正式收益验收使用冻结的留出集；开发集结果保留为诊断。
- 指标按实际展示前五条计算，不可在评测时从第六条之后补齐去重结果。正例按场景宏平均，同款身份来自商品目录的 `canonical_product_id`。
- `not_run`、失败、缺失、显式降级、证据不完整或条件不一致均不能得到“验收通过”。只显示原始差值不足以证明优化成立，必须同时满足全部门禁。
- 当前 `eval/v2/product_retrieval.jsonl` 共 72 例。它没有材质约束覆盖，预算约束也缺少满足条件的正例；本轮保留这一事实。新增的离线诊断仅验证标准和图表链路，不能代替当前 Dense 模型的正式基线。
- 本轮 `baseline-v1.json` 与待测报告冻结的选集为 `all`，用于现状登记和全量诊断。正式测量前须补齐金标、另建 `holdout` 冻结定义和对应基线，并保留本轮定义；不能将 `all` 报告直接改标为正式验收。看板选用的冻结定义也须随该正式实验更新。

## 使用方式

以下为首次生成示例，均从项目根目录执行。本轮产物已存在时，复跑须换用新的文件名或目录；程序拒绝覆盖已有证据。Windows 本机可将 `python` 换为 `E:/anaconda3/envs/globex/python.exe`。

```powershell
python -m scripts.eval.retrieval_standard freeze --split all --output eval/retrieval/baseline-v1.json --not-run-output eval/verification/retrieval-standard/current-baseline-pending/report.json
python -m scripts.eval.retrieval_standard run --split all --variant keyword --role baseline --scope diagnostic --output eval/verification/retrieval-standard/offline-keyword-baseline
python -m scripts.eval.retrieval_standard run --split all --variant bm25 --role candidate --scope diagnostic --output eval/verification/retrieval-standard/offline-bm25-candidate
python -m scripts.eval.retrieval_standard compare --baseline eval/verification/retrieval-standard/offline-keyword-baseline/report.json --candidate eval/verification/retrieval-standard/offline-bm25-candidate/report.json --output eval/verification/retrieval-standard-20261014-01/offline-comparison.json
```

本轮 CLI 只执行不调用模型的 keyword/BM25 诊断，使用商品快照内存仓，不连接买家数据库，也不修改 `.env`、运行态商品数据或默认检索策略。真实模型的正式基线与候选报告应遵循相同报告协议，并另行完成适用的 Harness 与收益门禁。

## 前端与只读接口

主页面导航新增“检索评测”。独立工作台入口为 `/?view=retrieval-evaluation`，不初始化买家选购会话。页面从 `GET /commerce/retrieval-evaluation` 读取标准、冻结定义及报告，支持选择基线和候选、查看对照图、指标差值、约束矩阵和逐场景变化。

接口只读取固定目录下的协议报告。开发身份模式允许本机查看；严格 HMAC 身份模式要求运维指标读取权限。它不提供启动模型调用、修改标准或发布策略的 HTTP 操作。

只查看此工作台时，可分别运行独立只读 API 和前端，不初始化买家数据库或模型：

```powershell
python -m uvicorn app.presentation.retrieval_evaluation:build_preview_app --factory --host 127.0.0.1 --port 8001
# 在另一个 PowerShell 窗口中运行前端：
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix frontend run dev -- --host 127.0.0.1
```

预览地址为 `http://127.0.0.1:5173/?view=retrieval-evaluation`。此模式仅提供评测读取接口；完整选购流程仍应使用正常业务后端。关闭对应进程或终端即可撤销预览，配置文件无需修改。

后续算法改进、真实候选回放、字段消融与阈值校准见[算法评测说明](optimization-README.md)。看板默认选择最新已完成且可比的报告对，诊断或回放数据不自动成为正式验收通过。
