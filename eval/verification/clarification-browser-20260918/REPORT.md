# 澄清工具完整前后端联调 · 2026-09-18

本报告补充自动化测试之外的实际启动与浏览器验证。使用当前未提交工作区源码（指纹见 source-sha256.json）、FastAPI/Uvicorn、Vite、真实 qwen3-max 网关及 Chromium。没有模拟模型响应、HTTP 接口或 AG-UI 事件。

## 验证范围与结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 完整后端、前端启动及 SQLite 健康检查 | 通过 | 后端 8000，前端 5174 |
| 页面点击澄清入口，Agent 自行生成题目和选项 | 通过 | browser-report.json，01-agent-questions.png |
| 填写八个动态问题，保存答案并让 Agent 继续总结 | 通过 | browser-report.json；后续运行 completed |
| 提交前刷新，恢复未提交的表单定义 | 通过 | browser-report.json；不承诺恢复尚未提交的输入草稿 |
| 提交后刷新，不重发模型请求 | 通过 | browser-report.json |
| 重复继续不重复写入表单 | 通过 | actions POST 仅一次，复用服务端 run_id |
| 后端重启、新浏览器上下文（无 localStorage）恢复已完成对话与答案 | 通过 | restart-search-report.json 第一项，05-restarted-restored.png |
| 指定 P1003/P1003-S1 的首次商品查询 | **失败** | 模型只返回查询计划，未调用工具，未显示商品卡 |
| 对失败查询明确追问后重新查询，商品卡显示并在刷新后恢复 | 复核通过，**不覆盖首次失败** | product-recheck-report.json，06-product-recheck.png |
| 浏览器 JS 异常 | 澄清主流程未捕获异常 | browser-report.json |
| 修改后的前端测试与构建 | 123 项通过，构建通过 | frontend-tests.log，frontend-build.log |

商品复核结果来自真实工具：P1003-S1 石墨黑，商品价 129 CNY、库存 80；配送中国的样例到手价为 154 CNY（运费 25 CNY）。与隔离目录及库存一致，仅为样例数据，未创建订单。

## 关键功能截图

保留原始截图共 **7 张**，不依赖浏览器标签页或 `.pytest_cache`。以下图片均来自本轮真实页面操作，测试买家为 `clarification-browser-test`。截图索引、原始尺寸、采集时间（源文件修改时间）及 SHA-256 见 [screenshots.json](screenshots.json)。截图用于证明页面状态，运行是否完成、是否发生重复写入仍以对应日志和断言为准。

| 验收环节 | 截图 | 结果说明 |
| --- | --- | --- |
| Agent 生成澄清问题 | [01 · 动态澄清表单](01-agent-questions.png) | 问题和选项由真实 Agent 提供 |
| 买家填写条件 | [02 · 填写后的表单](02-answers.png) | 记录提交前选项与文本状态 |
| 提交后继续对话（修复前） | [03 · JSON 展示缺陷](03-agent-continued-before-display-fix.png) | 历史失败样本，不能当成修复后验收图 |
| 刷新恢复（修复前） | [04 · 原始恢复页面](04-reloaded-before-display-fix.png) | 数据恢复正常，当时的展示缺陷仍存在 |
| 重启与清空浏览器缓存后恢复（修复后） | [05 · 对话和答案恢复](05-restarted-restored.png) | 气泡已转换为可读摘要；源码指纹对应此修复后版本 |
| 商品查询首次失败 | [首次查询缺少商品卡](product-first-attempt-failed.png) | 首轮漏调工具仍未解决，保留失败证据 |
| 商品查询追问复核 | [06 · 商品卡与价格](06-product-recheck.png) | 复核成功，不覆盖首轮失败 |

## 本次联调修复

原先提交澄清答案后，买家的聊天气泡直接显示传给 Agent 的 JSON。现在只在展示层转为可读的“题目：答案”摘要；原始结构化消息、单位、未回答状态和持久化内容保持不变。新增回归测试，使用重启后恢复的历史消息复验。

## 隔离及启动配置

测试买家为 clarification-browser-test；数据库、向量目录、Prompt 注册表均在 `.pytest_cache/clarification-browser-20260918/data`。未修改默认买家 pao-coder 的真实数据和工程 .env。

本机 .env 固定了原数据库中的 Prompt 版本，首次用空隔离库启动时找不到该版本，服务拒绝启动（见 backend-first-start.log）。仅对测试进程清空 PROMPT_PIN_VERSION，使用当前源码 bootstrap 的版本。测试进程还关闭 Redis/队列、语义缓存及遥测，使用本地 SQLite/Qdrant；聊天模型和 embedding 网关沿用实际配置。

## 未通过和限制

- embedding 网关实际返回 HTTP 200 空响应，启动日志确认商品向量和品类知识库建库失败。目前使用关键词降级；健康接口的 ok 不能作为向量与知识库可用的证据。
- 商品查询首轮漏调工具仍为未解决的模型执行可靠性失败；追问后的成功不能证明首轮问题已修复。
- 本轮没有重新验收 qwen-text-rerank；此前远端路由失败不能由本次商品 ID 直查成功替代。
- 本次仅验证上表流程，不代表订单、记忆、所有检索场景和生产质量已整体通过。上一轮后端 1,145 项回归结果沿用；本轮只修前端展示，未重复无关后端测试。
