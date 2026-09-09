# 指标与 BadCase 闭环

`operational_metrics` 在同一业务回合内聚合真实模型 attempt、工具结果和回退事件。回合开始于取得会话执行锁后，结束于持久化收尾后；这不是 HTTP 请求耗时，也不包含排队时间。取消独立计数，错误不会计为成功。真实模型请求开始时登记 active attempt；若业务回合先结束、流在后台晚清理，summary 仍保留未结算次数并把 usage/cost 记为 unknown，不能误报为零调用零成本。指标只有数值和固定枚举，不采集对话、地址、买家、会话、令牌或工具自由文本。

## 观测与告警

`registry.prometheus()` 输出进程累计计数器/时延直方图；`snapshot()` 输出最近最多 1000 回合的 P95、错误/取消/回退率；`alerts()` 默认至少 20 个样本后检查 P95 >60秒、错误率 >5%、回退率 >10%。这些是显式运维默认值，不是此次评测门槛。跨 worker 或 API/worker 分离需由采集端合并计数器，不能把单进程摘要当全站指标。告警接口输出待处理状态，本实现没有冒充已配置外部告警通知通道。

模型 usage 缺失时完整 input/output 和成本为 `null`，只另保留已观察 token；不按零成本计入完整统计。当前没有批准的模型价目表，美元成本为 unknown。此处 usage 仅指已埋点的 Agent LLM attempt；embedding、reranker、外部工具及评测 Judge 的费用不在该口径，不能称为整条业务的总成本。真实 TTFT 和持续时间由 LLM 层计时，不从文本或 token 数反推。每业务回合发布 `usage.summary`，评测 runner 只有收到全部轮次且 usage 完整才计算完整 token 汇总。逐 case 时延是 Agent 对话加显式 HTTP 动作的 wall time，排除 Judge；故和服务业务回合时间不同。旧冻结报告没有这些字段，不追填或冒充可用于 Prompt 发布。

队列指标由 Lua 确认实际新增或状态转移后计数；重复 ack 不重复计完成/DLQ。归档只有维护提交和实际删除成功后计数，没有按任务/身份做 label，也没有估算耗时。

## 分数回注

`python -m scripts.eval.feedback --db /独立路径/feedback.db import --manifest /路径/report.manifest.json` 生成 SQLite 分数发件箱与未审核 BadCase。只有 trace_events 中唯一、有效、非零的 32 位十六进制 trace ID 才能关联分数；缺失或多 trace 保留 unlinked，不用 session ID 猜关联。分数仅包含 traceId、幂等 ID、名称、类型和数值，不导出 transcript。

在本机 `.env` 或环境变量中设置 `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 后显式执行；命令行不接收密钥，独立回注不要求配置 LLM：

```bash
python -m scripts.eval.feedback --db /独立路径/feedback.db flush --env-file .env
```

省略 `--env-file` 时读取工程根 `.env` 并优先使用同名环境变量；显式文件中的三项配置覆盖环境同名项，文件内容不作变量插值。可用 `--base-url` 显式覆盖目标地址。缺项仅输出缺失字段名，保留 pending，不发请求。

使用官方 `POST /api/public/scores`，同一 manifest/case/trace/metric 的 body.id 恒定，并发送 Idempotency-Key。响应不确定时保留 pending 重试；HTTP 确认成功只代表送达，不声称远端查询已可见。当前验证是本地 HTTP 接收端模拟“已保存后 500”再重启重发，证明同 ID 重试和持久 outbox；未提供真实 Langfuse 凭据，云端端到端验收仍 BLOCKED。[Langfuse 分数 API](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)、[读取与创建端点](https://langfuse.com/docs/api-and-data-platform/features/scores-api)。

## BadCase 审核

真实失败候选只保存来源报告路径/hash、case ID 和分类；商品/知识 gold 不会被自动覆盖。浏览器发现的“背包查询出现腰包”以假设保存，仍是 unreviewed，不是批准的相关性标注。人工核对证据后执行 `review --candidate ID --decision accept|reject --reviewer 姓名 --note 理由`；accepted 才能 `propose --candidate ID --output /新路径/proposal.json`。提案必须用新文件，不覆盖已有 gold；补充可判定断言、去重分组、dev/release 隔离审核后才可另行纳入正式集。

`eval/proposals/confirmation-preapproval-injection.json` 是明确标注的合成故障回归：注入 HTTP 点击前已有订单的轨迹，程序断言必须失败；pending 对照轨迹通过。它验证检测器，不宣称真实生产越权，也没有改变正式选集。
