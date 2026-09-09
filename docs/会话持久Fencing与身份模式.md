# 会话持久 Fencing 与身份模式

2026-09-09 实现。会话状态不再依赖“调用 save 前租约仍有效”的检查：每轮在 SQLite 事务内领取单调递增 fence，并携带 revision 和 owner；保存用三者作条件更新，快照与 revision 在同一事务提交。新的执行者领取 fence 后，旧执行者即使延迟恢复，也不能覆盖新状态。存储故障、提交失败和取消会回滚；Registry 丢弃失败缓存，下一轮重新读取，不返回虚假保存成功。

## 存储与迁移

- 默认 SQL 使用既有 `agent_session_states`，新增 `session_write_claims` 附属表，不清空历史快照。多实例独立连接通过 `BEGIN IMMEDIATE` 串行化领取和提交。
- `DATABASE_URL=file` 的 AgentState 也改用 `DATA_DIR/sessions/session-state.sqlite3` 为事务权威；原 JSON 只作一次幂等迁移来源，数据库存在状态后旧文件不能回写覆盖。偏好和对话流水仍是文件存储；偏好新文件采用买家 ID 的完整 SHA256，避免清除特殊字符引起身份别名。
- 旧 SQL 会话从 `ConversationSessionRow.buyer_id` 恢复可信 owner；旧文件从对应对话 JSONL 中唯一 buyer_id 恢复。缺归属、多归属或文件名有歧义时明确拒绝自动抢绑。可信离线迁移程序可以调用 `bind_legacy_owner(session_id, buyer_id)`，不能更改已绑定 owner。
- 新任务入队前持久绑定 task、session 和 buyer；GET task 必须验证签名主体和任务归属。升级前没有可信任务归属记录的历史队列任务不能通过新查询接口读取，不猜测所有者。
- 每轮 `ShoppingContext.session_fence` 供其他持久证据识别执行轮次。更新偏好约束时保留该字段。

SQLite 是本机多进程边界，所有 API/worker 必须共享同一数据库文件。Redis 租约与 SQLite 并非跨系统原子事务；数据库 claim/save 是线性化点。如果旧写事务先取得数据库锁并提交，新 claim 随后读到它，属于有序提交。这里保证旧 ticket 不能覆盖新 claim/revision，而不是宣称跨任意存储的全局 fencing。文件偏好/流水不因此获得完整交易事务保证。

## 身份配置

默认 `IDENTITY_MODE=demo` 和 `SESSION_OWNER_BINDING=1`：首次会话绑定 buyer 后不可换 buyer 读写；demo 中 buyer 是客户端声明，**不是登录认证**。隔离开发环境可显式关闭 legacy 会话存储层的 owner 检查，用于可信迁移诊断，但 hmac 严格模式禁止关闭。这个开关不绕过订单/确认、AG-UI journal 和 Prompt 会话分组的买家隔离，也不能作为跨买家读取入口。

严格模式设置服务端环境：

```sh
export IDENTITY_MODE=hmac
export SESSION_OWNER_BINDING=1
# 通过可信环境管理设置 IDENTITY_HMAC_SECRET，至少 32 字节，不提交到仓库。
.venv/bin/python -m scripts.issue_identity_token --buyer-id buyer-001 --ttl-seconds 3600
```

签发命令仅供可信操作员本机调用，服务端没有公开 mint、refresh 或第三方登录 endpoint。CLI 输出的短期令牌是客户端凭据；只传令牌，不传服务端密钥。固定 HS256、显式 `globex-access+jwt` 类型、issuer、audience、sub、iat 和 exp，最长有效期 24 小时；未知算法、类型、附加 claim、受众不匹配、篡改与过期均拒绝。实现使用锁定的 PyJWT 2.13.0。校验依据 [JWT BCP](https://www.rfc-editor.org/rfc/rfc8725.html) 和 [JWT 标准](https://www.rfc-editor.org/rfc/rfc7519.html)。

HTTP：`Authorization: Bearer <token>`；请求 body/query 的 buyer_id 必须等于签名 sub。覆盖 intents、异步任务、订单读取消、确认 prepare/get/list/resolve、AG-UI run/replay/cancel/session。GET `/commerce/tasks/{id}` 现在同时需要 buyer_id。`/health` 保持公开，不输出签名密钥。身份无效 401、主体或 owner 不符 403、归属未知 404、需迁移或快照冲突 409。严格模式缺归属存储返回 503，不降级跳过检查。

浏览器 WebSocket：

```js
const ws = new WebSocket('/commerce/events', ['globex-events', `globex-auth.${token}`]);
ws.onopen = () => ws.send(JSON.stringify({shopping_session_id: sessionId, buyer_id: buyerId}));
```

示例连接地址实际应为 ws/wss 绝对地址；服务端仅回显 `globex-events`，不回显含令牌子协议。不接受 query token，避免 URL/代理日志泄漏。订阅在身份与 owner 校验后建立；失败关闭码 4401/4403/4400。HTTP 与 WS 必须使用同一 buyer。已有脚本支持 `GLOBEX_BUYER_ID` 与 `GLOBEX_API_TOKEN` 环境变量。

生产应使用 HTTPS/WSS、可信服务端密钥管理和现有企业登录入口；本项没有替用户接入未知第三方系统。更换签名密钥使旧 token 全部失效；逐 token 撤销、刷新、多密钥滚动属于后续身份平台集成边界。

## 验证

`tests/test_session_fencing.py` 覆盖新 claim 使旧票据立即失效、跨实例延迟保存、并发 fence 分配、重复 revision、保存失败回滚、历史归属迁移、文件模式和 Registry 重载，以及 context fence 不丢失与偏好 ID 不串读。

`tests/test_identity.py` 使用真实临时 SQLite、真实 FastAPI 和 TestClient WebSocket，覆盖 HTTP 所有买家入口、确认可用闭环、任务持久归属、跨 app WS owner、无 query token、算法/用途/时间/受众约束，不调用外部模型或正式业务数据库。

## 运维指标读取

全进程聚合只在 `/internal/metrics`（Prometheus 文本）和 `/internal/metrics/summary`（滚动指标与阈值告警）返回，公共 `/health` 不返回这些统计。服务端 `METRICS_READER_BUYERS` 逗号分隔允许名单默认空，空时接口 404 关闭；开启后必须使用 hmac 模式和允许名单主体的 Bearer 令牌。普通已登录买家仍返回 403，不能通过 body/query 声明成运维身份；无凭证 401，demo 下误配置名单 503。响应带 `Cache-Control: no-store`，不接收 query token。

指标 scope 是 `process_local_business_turns`；API 与 worker 各自累计，需外部采集端汇总，不能把单进程数值称作全站统计。授权名单仅给可信运维主体，不给普通买家或前端构建配置。
