# 品类知识库召回评测报告（2026-09-09 13:39:45）

标注集 `eval/v1/knowledge_retrieval.jsonl`，正例 12 条。标注单位为知识文档名。

| 指标 | 值 | 阈值 |
|---|---|---|
| Recall@3 | 1.000 | ≥ 0.85（阻断） |
| Precision@3 | 0.472 | 观察项（未穷举金标，不阻断） |
| MRR | 1.000 | ≥ 0.85（阻断） |
| NDCG@3 | 1.000 | ≥ 0.85（阻断） |
| 不可回答准确率 | 0.000 | ≥ 1.0（阻断） |
| 政策拒答准确率 | 1.000 | ≥ 1.0（阻断） |

门禁结论：**BLOCK**

未达标项：
- 无结果准确率 0.0 < 1.0

| query | Recall | Precision | MRR | NDCG | 召回文档 | 标注文档 |
|---|---|---|---|---|---|---|
| 选购旅行装备 参数判断时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-travel-gear-参数判断.md,eval-travel-gear-避坑与合规.md,eval-travel-gear-概览.md | eval-travel-gear-参数判断.md |
| 选购旅行装备 价格与预算时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-travel-gear-价格与预算.md,eval-travel-gear-避坑与合规.md,eval-travel-gear-参数判断.md | eval-travel-gear-价格与预算.md |
| 选购家居生活 避坑与合规时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-home-living-避坑与合规.md,eval-home-living-参数判断.md,eval-kitchen-dining-避坑与合规.md | eval-home-living-避坑与合规.md |
| 选购户外运动 概览时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-outdoor-sports-概览.md,eval-outdoor-sports-避坑与合规.md,eval-outdoor-sports-参数判断.md | eval-outdoor-sports-概览.md |
| 选购户外运动 参数判断时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-outdoor-sports-参数判断.md,eval-outdoor-sports-避坑与合规.md,eval-outdoor-sports-概览.md | eval-outdoor-sports-参数判断.md |
| 选购户外运动 避坑与合规时，应该优先核对哪些可验证字段？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-outdoor-sports-避坑与合规.md,eval-outdoor-sports-参数判断.md,eval-outdoor-sports-概览.md | eval-outdoor-sports-避坑与合规.md |
| 同时比较旅行装备 参数判断和户外运动 避坑与合规时，哪些证据不能只凭标题判断？ | 1.00 | 0.67 | 1.00 | 1.00 | eval-travel-gear-参数判断.md,eval-outdoor-sports-避坑与合规.md,eval-travel-gear-避坑与合规.md | eval-travel-gear-参数判断.md,eval-outdoor-sports-避坑与合规.md |
| 同时比较旅行装备 价格与预算和美妆个护 概览时，哪些证据不能只凭标题判断？ | 1.00 | 0.67 | 1.00 | 1.00 | eval-travel-gear-价格与预算.md,eval-beauty-care-概览.md,eval-beauty-care-价格与预算.md | eval-travel-gear-价格与预算.md,eval-beauty-care-概览.md |
| 同时比较家居生活 避坑与合规和美妆个护 参数判断时，哪些证据不能只凭标题判断？ | 1.00 | 0.67 | 1.00 | 1.00 | eval-home-living-避坑与合规.md,eval-beauty-care-参数判断.md,eval-beauty-care-避坑与合规.md | eval-home-living-避坑与合规.md,eval-beauty-care-参数判断.md |
| 同时比较户外运动 概览和美妆个护 避坑与合规时，哪些证据不能只凭标题判断？ | 1.00 | 0.67 | 1.00 | 1.00 | eval-outdoor-sports-概览.md,eval-beauty-care-避坑与合规.md,eval-outdoor-sports-避坑与合规.md | eval-outdoor-sports-概览.md,eval-beauty-care-避坑与合规.md |
| 同时比较户外运动 参数判断和厨房餐饮 参数判断时，哪些证据不能只凭标题判断？ | 1.00 | 0.67 | 1.00 | 1.00 | eval-outdoor-sports-参数判断.md,eval-kitchen-dining-参数判断.md,eval-home-living-参数判断.md | eval-outdoor-sports-参数判断.md,eval-kitchen-dining-参数判断.md |
| US 的政策快照若没有权威来源或已经超过有效期，还能当作确定事实回答吗？ | 1.00 | 0.33 | 1.00 | 1.00 | eval-policy-us.md,eval-policy-eu.md,eval-policy-jp.md | eval-policy-us.md |


## 执行证据

- 选集：`release`，15/50 条；完整选集：True。
- 选集内容 SHA-256：`8103ce48356c4d8a7d5d392a86cfc5e4e8ef7f739b05e9c108351b75356b5c7f`。
- 工作区内容 SHA-256：`3ef9cb7200d6a361afd1d1188296544e6b70ef387fbc8a12f167e4a0d2e1f300`（包含未提交源文件；详细范围见同名 manifest）。
- 执行状态：**COMPLETED**；门禁：**BLOCK**；门禁范围：`release`。
- 实际策略：`["category_vector_document_scope_v1"]`。
- 模型、Prompt、数据文件、依赖版本及逐项 hash 均保存在同名 `.manifest.json`。
