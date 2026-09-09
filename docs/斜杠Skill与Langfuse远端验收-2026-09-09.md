# 斜杠 Skill 与 Langfuse 远端验收

日期：2026-09-09。本报告记录本轮新增的 `/` 选择与确定读取、Langfuse 真实远端接通，以及本机 5174 页面验收。此前阶段见[买家入口历史报告](买家Skill入口与Langfuse接入验证-2026-09-09.md)。

## 买家如何使用

打开 [Globex 页面](http://127.0.0.1:5174/)，刷新后点击“开启一次新选购”。在输入框输入 `/`，选择“选购需求梳理”，补充需求并发送。可输入 `/需求` 过滤；支持上下键选择、Enter 确认、Esc 关闭，也可点击方案。选中标签旁可以取消。

示例：通勤背包，300 元人民币以内，寄到中国，20 到 25 升、不要真皮，优先黑色。先整理需求，不搜索商品。

当前本机已启用 `shopping-needs-clarification / 1.0`，负责整理需求、预算币种、目的地、硬约束和偏好。它不负责商品检索或下单；其他需求仍可直接对话。原“周末背包选购”质量未通过，保持未发布。新方案改变了能力清单版本，已有旧会话请使用“新选购”。

## 前后端怎样执行

前端通过 AG-UI 的 `forwardedProps.selectedSkill` 传递 id、version、contentHash，正文保留买家原始需求。服务端先核验会话归属、发布状态、能力清单摘要、内容哈希和实际工具条件，再从权威库读取正文；随后才调用模型。正文作为本轮参考步骤传给 Agent，不能覆盖买家硬约束、扩大工具权限或替代交易确认。

页面通过真实 `skill.preload` 事件显示“读取中 / 已读取 / 失败”，并标识来源 `server_preload`；不会伪造模型的工具调用。读取失败时终止本轮，不静默降级为普通搜索。换选或取消后不沿用上轮正文，同一运行重连只重放已有事件。

本机方案发布基于用户要求落地功能的授权与自动化检查；发布记录明确为本机演示，没有声称用户亲自审核或正式质量门禁通过。参见[方案审核与验证](capabilities/选购需求梳理-1.0-审核与验证.md)。

## 验证结果

| 项目 | 实际结果 |
| --- | --- |
| 后端全量 | 918 passed，0 skipped；仅现有 Starlette/httpx 迁移提示 |
| 前端 | 85 passed，生产构建成功 |
| 真实模型 Skill 用例 | 缺币种、条件完整、预算冲突与无依据承诺，共 3 例通过；无商品查询或交易调用 |
| 正式页面 5174 | 真实 `/` 菜单、选中、发送、后台读取、最终回答全部完成；控制台无错误 |
| 运行版本 | 8000 的 /health 为 200，源代码指纹与实际运行一致 |
| Langfuse | 真实业务 Trace、父子关系、模型用量、同 Trace 评分均经远端 GET 回查通过 |

正式页面运行 `b95c0cd0-489f-4798-b231-5868e804a1b2` 正确保留 300 CNY、中国、20–25 升、不要真皮和优先黑色；返回“需求已整理清楚，无需进一步确认”。事件含一次 RUN_STARTED、一次 RUN_FINISHED，以及真实 server_preload reading → used。模型网关发生连接重试与回退提示，最终成功；本次不是延迟达标验收。

该页面运行对应的生产 Trace `dd5beffa7372b3c10fafc062f53c81e6` 也已在远端查到，含 8 个 observation，父子关系和模型用量完整。由于连接重试保留了 4 个错误 span，严格的无错误检查返回 INCOMPLETE；这与最终回答成功并不矛盾。下文 VERIFIED 指另一次完整商品查询及其评分，不将此次重试记录改写为无错误通过。详见 [正式页面远端回查](../eval/verification/slash-skill-langfuse-20260909/production-browser-remote.json)。

运行代码指纹：`8079d781aa73d6c1336952bde1bc354ebc9ebce1117290bd7f0ce6740d367b8a`。

## Langfuse 配置与远端证据

用户提供的三项配置已写入工程 `.env`，BASE_URL 已规范为 `https://cloud.langfuse.com`。文件权限为 0600，已被 Git 和 Docker 构建上下文忽略；报告和前端不包含密钥。8000 重启后已启用 OTel 导出。

真实商品查询 Trace：`2e86df81c08bccf146fe559fb5b2a71e`。远端共 5 个 observation：API 1、Agent 1、模型 2、工具 1；1 个根节点、4 条父子关系，无缺失父节点、未结束节点或错误。模型真实 input/output tokens 为 13969 / 184。

本次查询只返回 P1001-S2 的权威商品信息；通过检查唯一 SKU、商品价、预算、无交易调用和成功结束，为该 Trace 回注 `eval.task_success=1.0`。再次 flush 没有重复发送，远端回查核对了评分 ID、Trace 归属、名称与数值。

远端未返回可用成本，`cost_usd=null`，不能解释为零费用。队列 worker 的远端链路本轮未启用验收；网页登录态不作为 API 接通的判断依据。三条 Skill 用例和一次商品验收也不代表正式全套检索质量门禁通过。

复查命令（不输出密钥）：

```bash
.venv/bin/python -m scripts.verify_langfuse --env-file .env \
  --trace-id 2e86df81c08bccf146fe559fb5b2a71e \
  --require-components api,agent,model,tool \
  --score-id 16d9bebc16f00bf7391f9baf3bff2ac0b48862f20d735eaffdd2b099680118ea \
  --score-name eval.task_success --score-value 1 --wait-seconds 30
```

证据位于 [slash-skill-langfuse-20260909](../eval/verification/slash-skill-langfuse-20260909/)：

- [运行环境](../eval/verification/slash-skill-langfuse-20260909/accepted-runtime.json) 与 [正式页面持久化运行](../eval/verification/slash-skill-langfuse-20260909/production-browser.json)。
- [3 条真实用例审读](../eval/verification/slash-skill-langfuse-20260909/semantic-review.json) 与 [本机发布记录](../eval/verification/slash-skill-langfuse-20260909/local-skill-publication.json)。
- [Trace 与评分远端回查](../eval/verification/slash-skill-langfuse-20260909/trace-and-score-remote.json)、[评分发送记录](../eval/verification/slash-skill-langfuse-20260909/score-delivery.json)。
- [后端全量报告](../eval/verification/slash-skill-langfuse-20260909/backend-full-tests.xml)、[前端测试](../eval/verification/slash-skill-langfuse-20260909/frontend-tests.log)、[前端构建](../eval/verification/slash-skill-langfuse-20260909/frontend-build.log)。
