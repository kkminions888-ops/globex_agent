# 买家 Skill 入口与 Langfuse 接入验证

本报告保留 2026-09-09 较早阶段的验证快照，下文“当前”均指该阶段。后续已在本机演示库发布需求梳理 1.0，并通过真实 Langfuse 商品 Trace 与评分 API 回查；最新状态见 [需求梳理发布记录](capabilities/选购需求梳理-1.0-审核与验证.md) 和 [Trace 验收文档](Langfuse与端到端Trace.md)。原 `CONFIGURATION_MISSING`、背包推荐质量未通过以及旧测试数字均保留，不追写为成功。

## 本阶段当时结果

买家页面已增加“选购方案”，首页可查看场景卡，输入框上方可选择、换选或取消。买家直接描述需求时，仍由主 Agent 按需匹配。点击卡片只是填写可编辑的选购需求；只有真实 `load_agent_skill_tool` 读取成功，页面才显示“已读取选购方案”及名称、版本。读取失败、中断、清单加载失败与暂无已发布方案分别处理。选中后自然过期，发送前会移除方案前缀、保留需求正文并刷新清单，不自动发送过期方案。

正式 `data/capabilities.db` 当前没有已发布条目，因此 `http://127.0.0.1:5174/` 如实显示“选购方案筹备中，可直接描述需求”。第一份背包方案仍为待审核草稿，参见 [审核卡](capabilities/周末背包选购-1.0-审核卡.md)。不把测试库的自动化发布当作人工审核或正式发布。

Langfuse 已接通三项配置自动装配、API/worker 共享配置、分数回注和远端只读验收工具。**真实远端仍未接通**：本机未找到 `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，打开的官方 Cloud 页面仍未登录。验收命令返回 `CONFIGURATION_MISSING`、退出码 2，`remote_trace_verified=false`。不能以本地测试通过代替远端验收。

## 验证证据

- 后端本轮相关回归：124 项通过，报告 `eval/verification/skill-langfuse-20260909/backend-tests.xml`。
- 前端 74 项通过，生产构建成功。真实 5174 页面已检查方案按钮、真实空库提示和新选购无错误状态，浏览器控制台无错误；对应测试和构建日志及运行版本见同目录 `frontend-tests.log`、`frontend-build.log`、`accepted-runtime.json`。
- 独立真实浏览器：场景卡选择 → 真实模型 → Skill 读取 → 商品检索 → AG-UI 展示，1 次 RUN_STARTED、1 次 RUN_FINISHED，页面名称/版本与持久事件一致，控制台无错误。证据 `eval/verification/skill-langfuse-20260909/skill-real-browser.json`。
- 本次真实推荐混入腰包/收纳包并估算容量，推荐质量未通过。上述证据只证明入口和 Skill 读取机制，不构成方案质量或正式 release 门禁通过。
- 远端检查结果：`eval/verification/skill-langfuse-20260909/langfuse-remote.json`。
- Compose 配置校验与差异空白检查通过；Langfuse 密钥文件已排除在后端构建上下文外。

## 已知的旧本机历史限制

本轮检查发现原有 v1 本地缓存恢复路径：若缓存会话未在当前后端持久化，旧商品仍可展示，但确认接口返回“会话不存在”。这不是 Skill 功能引入的新请求失败。新选购或已有远端会话可正常继续，无需删除旧历史。旧缓存商品尚未重新验证，当前未强制只读；本轮未进行历史迁移或只读隔离，不应据此记录为已解决。

## 远端接续方式

由账号持有人在已打开的 Langfuse 页面登录，或提供已有本机配置文件路径。密钥不需要发到聊天里。取得目标项目凭据后写入已被 Git 忽略的 `.env`，重启 API/worker，发起真实业务查询，再执行：

```bash
.venv/bin/python -m scripts.verify_langfuse --env-file .env \
  --trace-id <真实32位TraceID> --wait-seconds 30 \
  --output eval/verification/skill-langfuse-20260909/langfuse-remote.json
```

项目鉴权成功只代表能访问项目。Trace 验收还要求正确的祖先关系、必要组件、结束时间与真实模型 usage；队列路径额外要求 worker。模型与工具均直接挂在 API 根下会失败；允许合法包装 span 与嵌套 Agent。分数回注须核对同一 Trace 的分数 ID、名称及数值，不能只凭写入请求成功判断远端可见。命令及配置优先级见 [Langfuse 与端到端 Trace](Langfuse与端到端Trace.md)。
