# Claude Code 消息压缩与上下文管理调研

## ——以及在 Globex 电商搜索 Agent 中的落地方案

> 调研日期：2026-09-05  
> 调研范围：Claude Code 官方产品文档、Anthropic API 官方文档、阿里云百炼/Qwen 官方文档、Globex 当前代码与本地 AgentScope 运行时源码。  
> 结论口径：文中明确区分“官方已公开事实”“基于公开行为的推断”和“对 Globex 的设计建议”。

## 1. 执行摘要

Claude Code 并不是只靠一次 `/compact` 解决上下文问题，而是使用了一套分层治理体系：

1. **源头减量**：延迟加载工具/技能，把高体积操作放进独立 subagent，先避免无关内容进入主上下文。
2. **旧工具结果清理**：上下文接近上限时，优先移除已经完成使命的旧工具输出。
3. **会话压缩**：仍然过长时，用单独的模型请求生成结构化摘要，替换活跃消息历史。
4. **确定性重注入**：压缩后重新加载项目规则、持久记忆、计划和有限的近期工作材料，而不是要求摘要包办一切。
5. **完整历史另存**：活跃上下文可以被压缩，但原始 transcript 仍持久化，支持恢复、审计和定点 rewind。
6. **Prompt Cache 独立优化**：把稳定内容放在请求前部，通过精确前缀复用降低重复计算。缓存不缩短上下文，也不替代压缩。

对 Globex 最重要的结论是：**不要把订单、确认卡、商品标识、价格等精确业务事实托付给自然语言摘要。** 应将它们放进由业务代码维护的结构化 `ShoppingTaskState`；摘要只保存对话语义和选择理由。完整工具结果进入冷存储/事件流水，模型只接收决策所需的字段投影。

当前项目已经有不错的基础：AgentScope 自动压缩、`AgentState` 持久化、长期偏好 Store、子 Agent 隔离、语义缓存都已存在。最值得优先补齐的不是另造一套压缩框架，而是：

- 把交易关键状态从摘要中剥离为确定性状态；
- 在工具入口做结果投影和冷数据卸载；
- 将压缩改成“先清工具结果、再做摘要”的两阶段策略；
- 接入 Qwen Prompt Cache 读写指标，再决定是否上显式 `cache_control`；
- 用长对话回放测试事实保真率，而不是只测摘要是否生成。

## 2. 先把三个概念分开

| 机制 | 解决的问题 | 是否减少上下文 | 是否减少重复计算/费用 | 典型风险 |
|---|---|---:|---:|---|
| Prompt Cache | 相同前缀反复 prefill | 否 | 是 | 前缀改变导致 miss；缓存过期 |
| Context Editing | 删除旧工具结果等低价值内容 | 是 | 间接减少 | 清掉仍有用的证据 |
| Compaction | 用摘要替换大段历史 | 是 | 后续请求会降低 | 摘要遗漏、漂移、缓存层重建 |

Anthropic 官方明确指出，Prompt Cache 不会减少模型所见 token，只降低稳定前缀在后续请求中的处理成本；旧 `tool_result` 应由 context editing 清理，完整历史则由 compaction 替换为摘要。[Manage tool context](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context)

因此，“隐式缓存”指的是供应商在服务端自动识别重复前缀并复用计算结果；应用不负责保存 KV，也拿不到缓存内容。Globex 当前使用的 Qwen 百炼接口默认具有隐式缓存，无需传参数且不能关闭，但是否命中不确定。显式缓存才需要在消息 `content` 上写 `cache_control`。[百炼上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)

## 3. Claude Code 的上下文到底由什么组成

一次请求不是只有用户与助手的聊天记录。Claude Code 的上下文还包括：

- system prompt、输出风格和工具定义；
- 根目录与层级化 `CLAUDE.md`、rules；
- auto memory；
- 已加载技能；
- 用户消息、助手消息、工具调用与工具结果；
- 读取过的文件、命令输出、hook 注入内容；
- 当前计划与运行态元数据。

官方的上下文可视化文档展示了这些内容的加载顺序，也说明 subagent 的大文件读取不会进入主上下文，只回传摘要。[Explore the context window](https://code.claude.com/docs/en/context-window)

这说明上下文治理的第一步不是选一个“神奇压缩比例”，而是先回答：**token 花在 system/tool schema、自然语言历史、工具结果，还是按需材料上？** 不同来源必须采用不同策略。

## 4. Claude Code 的核心机制

### 4.1 请求按稳定性分层，缓存只认精确前缀

Claude Code 每次调用模型都会重发完整有效上下文，因为模型本身不跨请求记忆。它把更稳定的内容放在更前面：

```text
稳定                                                         易变
┌────────────────┬────────────────────┬─────────────────────┐
│ system + tools │ project context    │ conversation + 新消息│
└────────────────┴────────────────────┴─────────────────────┘
```

服务端从请求开头做精确前缀匹配；前部任一内容变化，后续缓存也无法复用。工具集合变化会影响 system 层，而普通新消息追加只影响末尾。Claude Code 还会给会话中追加的某些 system context 放缓存标记。[How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)

由此得到两个工程原则：

- 全局规则、工具 schema、序列化顺序要稳定，不要混入时间戳、用户 ID 等动态值。
- 用户偏好、检索结果、当前槽位等个性化内容应放在稳定前缀之后。

### 4.2 接近上限时，先清旧工具结果，再摘要

Claude Code 官方给出的顺序是：**先清理旧工具输出，仍不够再总结对话。** 因为文件内容、搜索结果和日志通常体积大，但模型完成当前步骤后，其原文价值迅速下降。[How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)

Anthropic API 也提供了对应的服务端原语：`clear_tool_uses_20250919` 会在阈值触发后按时间从旧到新清理 `tool_result`，默认保留 tool call 输入，并用占位文字表示结果已被移除；客户端仍可保留完整历史。[Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)

这里需要谨慎表述：**官方并未公开证明 Claude Code 所有运行路径都直接调用这个 API beta。** 可以确认的是产品行为与该设计思想一致，不能把 API 原语直接当成 Claude Code 内部源码事实。

### 4.3 `/compact` 是一次独立的摘要请求

Claude Code 的产品文档公开了如下过程：

1. 用当前会话相同的 system、tools 和 history；
2. 在末尾追加摘要指令；
3. 发起独立的模型请求生成结构化摘要；
4. 用摘要替换活跃消息历史；
5. 下一轮基于更短的摘要继续。

如果原会话的 Prompt Cache 仍然热，摘要请求可以命中旧前缀，因此主要成本在摘要生成；若恢复的是一个缓存已过期的老会话，则压缩请求需要重新处理完整历史，成本更高。[How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)

Claude Platform 现在还提供服务端 `compact_20260112` beta：达到输入 token 阈值后生成 `compaction` block，后续请求传回该 block，API 会忽略它之前的内容。[Compaction API](https://platform.claude.com/docs/en/build-with-claude/compaction) 这可作为自建 Claude Agent 的实现选择，但 Globex 当前是 Qwen + OpenAI 兼容接口，不能直接照搬该参数。

### 4.4 压缩后不是只剩摘要

Claude Code 会按内容类型决定压缩后如何恢复：

| 内容 | 压缩后的处理 |
|---|---|
| system prompt / output style | 保持不变 |
| 根 `CLAUDE.md`、无路径限制的规则 | 从磁盘重新注入 |
| auto memory | 从磁盘重新注入 |
| plan | 从持久介质重新注入 |
| 路径规则、子目录 `CLAUDE.md` | 再次读到相关文件时加载 |
| 最近读写文件 | 最多重读 5 个，优先最近修改者 |
| 已调用技能正文 | 有单技能和总 token 上限地重新注入 |
| 早期 hook 内容、普通对话、旧工具结果 | 进入摘要或被清理 |

也就是说，Claude Code 的摘要只是一个**连续性载体**，权威规则、持久记忆和当前工作材料有独立来源。[Explore the context window](https://code.claude.com/docs/en/context-window)

这正是 Globex 应吸收的核心：把“可重新读取的真相”留在外部状态，只把不可结构化的对话语义交给摘要。

### 4.5 长期记忆采用“小索引 + 按需详情”

Claude Code 区分：

- `CLAUDE.md`：人维护的规则、工作流和项目约束；
- auto memory：Claude 写入的纠正、偏好与难以从代码推导的项目知识。

启动时，auto memory 的 `MEMORY.md` 只加载前 200 行或 25KB，较长详情放入主题文件，需要时再读取。[How Claude remembers your project](https://code.claude.com/docs/en/memory)

这个模式不是“把所有记忆塞进 prompt”，而是：

```text
常驻小索引 → 找到相关主题 → 按需读取详情
```

Globex 的 `PreferenceSelector` 已经在做相似的事情：所有 dislike 作为安全底线保留，like 再按相关性/时间取 Top-K。这一设计应保留并扩展到“会话事实索引”。

### 4.6 Subagent 是上下文隔离器，不只是并发器

Claude Code 建议把测试日志、文档调研、大文件搜索等高体积但自包含的工作交给 subagent。subagent 有独立上下文与工具调用轨迹，主会话只收到结果摘要。其 transcript 独立保存，主会话压缩不影响它，subagent 自己也能自动压缩。[Create custom subagents](https://code.claude.com/docs/en/sub-agents)

这与 Globex 现有 `task_dispatch` 方向一致：检索子 Agent 每次新建独立 `AgentState`，中间工具过程不进入主 Agent，只回传最终结果。后续需要改进的是回传协议：从自由文本 JSON 升级为固定、可验证的 `SearchDecision`，避免把一大批候选再次灌回主上下文。

### 4.7 活跃上下文、完整历史和检查点相互独立

Claude Code 默认把每条消息、工具调用和元数据写入 JSONL transcript。压缩只改变模型后续使用的活跃上下文，不等于删除原始 transcript；`/rewind` 还能在指定用户消息处恢复或局部总结。[Manage sessions](https://code.claude.com/docs/en/sessions) [Checkpointing](https://code.claude.com/docs/en/checkpointing)

它还提供 `PreCompact` / `PostCompact` hooks，后者能拿到生成的 `compact_summary`，便于审计和扩展。[Hooks reference](https://code.claude.com/docs/en/hooks)

因此，成熟 Agent 不应只有一个 `messages` 数组，而应至少分为：

- 可审计的原始事件流；
- 面向当前推理的活跃上下文；
- 可确定性恢复的业务状态；
- 跨会话长期记忆。

## 5. 对 Cache Breakpoint 的准确理解

### 5.1 Breakpoint 是缓存边界，不是压缩边界

缓存标记表达的是：**“从请求开头到这里，如果与过去完全一致，就尝试复用。”** 它不表达“从哪里开始做摘要”，更不保证标记之后的内容可以随意改写而不付代价。

对同一个缓存点来说，修改点后的内容不会破坏该点之前的缓存；但要真正缩小一个持续增长的会话，迟早必须改写旧历史，此时 conversation 层缓存必然重建。Claude Code 官方直接说明 `/compact` 会使 conversation 层失效，但 system 层仍可复用。[Prompt caching：Compacting the conversation](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation)

因此，项目教程中“Breakpoint 之前永远不动、之后自由压缩”的说法只适合解释**局部前缀复用**，不适合当成长会话的完整压缩算法。若旧历史永久不动，它本身就会无限增长，压缩无法释放主要空间。

### 5.2 “最优位置”不是固定最近 K 个工具调用

更可靠的放置原则是按复用半衰期分层：

```text
cache point 1
┌ system prompt + 稳定 tools schema ┐   跨会话/跨轮高度复用

cache point 2（可选）
┌ compact summary + 会话稳定状态 ┐      一次压缩周期内复用

cache point 3（可选）
┌ 已完成的上一轮对话前缀 ┐             同一 AgentLoop 或短间隔对话复用

┌ 当前用户消息 + 新工具结果 ┐           不稳定尾部
```

判断一个点是否值得显式缓存，可以用近似经济条件：

```text
预期收益
= 命中次数 × 缓存段 token ×（普通输入单价 - 命中单价）
- 新建次数 × 缓存段 token ×（创建单价 - 普通输入单价）
```

如果每个购物会话只有一次模型调用、用户经常超过 TTL 才回复，那么会话级显式缓存收益很低；如果一次请求内有多轮 ReAct/tool call，或买家连续追问，则会话摘要和上一轮前缀值得缓存。

### 5.3 Qwen 下的实际约束

Globex 默认使用 `qwen3-max`。百炼官方当前规则包括：

- 无标记时使用隐式缓存；
- 显式缓存通过消息 `content` 的 `cache_control: {"type": "ephemeral"}`；
- 单请求最多 4 个缓存点；
- 最少 1024 token；
- TTL 为 5 分钟，命中后续期；
- 从标记向前最多检查最近 20 个 content block；
- tools 会参与系统前缀，但缓存标记本身必须放在 message content；
- 工具定义的列表顺序、字段顺序和结构要稳定。

详见[百炼上下文缓存官方说明](https://help.aliyun.com/zh/model-studio/context-cache)。

当前 AgentScope `OpenAIChatFormatter` 会重新构造标准消息块，未提供把任意 `cache_control` 从 `Msg` 透传到 content 的通道。因此显式缓存不能只改业务 prompt，需要在 Formatter/Model 适配层实现，并补充回归测试。

## 6. Globex 当前实现审计

### 6.1 已经做对的部分

| 能力 | 当前实现 | 评价 |
|---|---|---|
| 自动压缩 | `trigger_ratio=0.75`，保留最近 15% 原文 | 已有可运行骨架 |
| 业务摘要提示 | 要求保留偏好、product_id、sku_id、价格、订单、待确认动作 | 领域意识正确 |
| 会话恢复 | 每轮持久化/恢复 `AgentState` | 对应活跃会话快照 |
| 原始流水 | ConversationStore 保存对话和事件 | 可承载审计/回放 |
| 长期偏好 | PreferenceStore + 按需 Top-K 注入，dislike 全保留 | 安全策略合理 |
| 子 Agent 隔离 | search/trade 每次独立实例，只回主 Agent 最终结果 | 符合 Claude Code 隔离思路 |
| 语义缓存 | 仅无历史、读请求场景复用最终答案，并按偏好指纹分桶 | 与 Prompt Cache 不冲突，解决的是另一类问题 |
| 压缩事件 | 摘要变化时发布 `context.compressed` | 有基本可观测入口 |

对应代码：

- `app/application/agents/context_policy.py:21-68`
- `app/application/agents/main_agent.py:145-200`
- `app/application/agents/orchestrator.py:141-247,397-437`
- `app/application/tools/task_dispatch_tool.py:42-130`
- `app/application/memory/preference_selector.py:63-127`

### 6.2 主要风险与缺口

#### 风险一：交易精确事实只靠摘要兜底

当前摘要要求模型“逐字保留”商品 ID、价格、订单号和确认动作，但这些字段仍由生成模型写自然语言摘要。压缩模型可能漏字段、合并两次搜索的价格，或把一次性预算误写成长期约束。

交易链路不能把这类错误留给概率模型。订单和确认卡必须从数据库/领域状态重建，摘要只能描述“为何选择”。

#### 风险二：工具结果进入上下文后才截断

`product_search_tool` 把完整 `result` JSON 作为 tool result 返回模型，同时把完整 hits 放入事件流。即使 Top-K 当前较小，`filtered_out`、到手价明细、亮点等字段累积后仍会污染多轮历史。

更优方案是一次检索产生两种视图：

- `raw_result`：完整保存到 ConversationStore/对象存储，用于前端与审计；
- `llm_projection`：只含模型下一步决策所需字段。

#### 风险三：`TOOL_RESULT_LIMIT` 单位认知不一致

Globex 配置注释写“字符上限”，AgentScope 实际用模型 token counter 与 `tool_result_limit` 比较。当前默认 `20000` 实际接近 2 万 token，不是 2 万字符。这会让容量估算偏差很大。

#### 风险四：压缩只有“发生了”，没有“压得对不对”

`context.compressed` 目前只记录摘要字符数和剩余消息数。缺少：

- 压缩前/后 token；
- 被清理的 tool result token；
- 摘要生成 token 与时延；
- 关键事实保留/冲突数量；
- 压缩后任务成功率；
- Prompt Cache 创建/读取 token。

#### 风险五：偏好注入去重是进程内状态

`_injected_preferences` 只存在 orchestrator 内存。服务重启后会重复注入；更重要的是，AgentScope 压缩可能已经把早期偏好 hint 吸收到摘要，但内存仍认为“注入过”，或进程恢复后再次注入，造成状态来源混乱。

应把 `preference_revision`/`injected_revision` 放进可持久化会话状态，并在压缩后依据权威偏好 Store 决定重注入。

#### 风险六：尚未利用显式缓存，也没测隐式缓存

项目当前没有 `cache_control`，依赖百炼隐式缓存。AgentScope 已能读取 `cached_tokens`，但业务层没有输出该指标；`cache_creation_input_tokens` 也未被当前 OpenAI Chat 适配器采集。没有数据前，无法证明显式缓存比隐式缓存更优。

## 7. 推荐的 Globex 六层上下文架构

```text
                         每轮由 ContextAssembler 重新组装

L0 稳定策略层       system prompt + 固定工具 schema              常驻、缓存友好
L1 权威业务状态层   ShoppingTaskState / PendingConfirmation      确定性注入
L2 检索式记忆层     相关 buyer preferences / 必要历史事实         按需注入
L3 滚动摘要层       旧对话语义、选择理由、未结构化上下文           低频更新
L4 热消息尾部       最近若干完整用户/助手/工具交互                 原样保留
L5 冷事件层         完整 transcript、raw tool result、subagent trace 默认不进 prompt
```

### 7.1 L1：新增权威 `ShoppingTaskState`

建议由应用/领域代码维护，持久化到会话快照，而不是让 LLM 直接生成最终真相：

```python
class ShoppingTaskState(BaseModel):
    # 本轮/当前任务状态
    active_goal: str | None
    active_category: str | None
    query_constraints: QueryConstraints

    # 已被工具验证的商品事实
    shortlisted_items: list[ProductRef]
    selected_item: ProductRef | None

    # 交易安全状态
    pending_confirmation: ConfirmationSnapshot | None
    order_refs: list[OrderRef]

    # 上下文治理元数据
    preference_revision: str
    summary_revision: int
    last_compacted_event_id: str | None
```

关键字段说明：

- `QueryConstraints` 必须区分 `scope="current_query"` 与 `scope="session"`，避免把“这次 300 元”错误延续到下一个品类。
- `ProductRef` 保存 `product_id`、`sku_id`、标题、工具验证价格/币种、验证时间和来源事件 ID。
- `ConfirmationSnapshot` 保存确认卡版本、商品/数量、地址摘要、总金额、状态和 hash。只有用户确认与当前 hash 对应，才允许下单。
- `OrderRef` 由订单工具结果写入，不从对话摘要反推。

### 7.2 L3：摘要改成“语义补充”，不再充当数据库

建议把摘要 schema 改成领域结构，同时删除与权威状态重复的精确数值：

```json
{
  "goal_and_rationale": "用户当前想完成什么，为什么筛到这些候选",
  "conversation_commitments": ["已向用户承诺下一步做什么"],
  "rejected_options": [{"ref": "P123/S1", "reason": "用户明确不喜欢外观"}],
  "open_questions": ["颜色是否必须为蓝色"],
  "temporary_context": ["本轮用户说可以接受塑料，但不是长期偏好"],
  "source_event_ids": ["evt_..."],
  "summary_version": 3
}
```

压缩后再执行确定性校验：

- 摘要中出现的 product/order ID 必须在权威状态或事件流中存在；
- 摘要不得自行新增金额；
- pending confirmation 以 `ShoppingTaskState` 为准；
- 发现冲突则记录 `context.summary_validation_failed`，回退到最近安全快照。

### 7.3 L5：冷数据可查但默认不回灌

ConversationStore 应保存：

- 原始用户/助手消息；
- 工具入参、完整工具结果引用、结果 hash；
- 压缩前后 token 与摘要版本；
- 子 Agent 运行轨迹及最终结构化结果；
- 业务状态变更事件。

当用户追问“刚才第 4 个商品是什么”而它不在热尾部和 shortlist 中时，Agent 应通过一个 `conversation_fact_lookup` 工具按事件 ID/语义检索冷数据，而不是把整段历史重新塞回 prompt。

## 8. 推荐的两阶段压缩策略

### 阶段 A：无模型的工具结果清理

每轮推理前，按价值处理旧 tool result：

1. 交易工具结果：保留结构化状态引用，原文可清；
2. 商品检索结果：保留 shortlist/selected，淘汰未选候选详情；
3. 品类知识：保留最终采用的规则与 source ID，清理长文；
4. web search：保留结论、时间与 URL，清理页面正文；
5. 最近一次仍在比较的结果：完整保留在热尾部。

工具返回模型的推荐投影示例：

```json
{
  "result_ref": "search_evt_20260905_001",
  "query": "旅行三件套 轻便 无塑料",
  "hits": [
    {
      "product_id": "P1007",
      "sku_id": "SKU-BLUE",
      "title": "...",
      "price_major": 268,
      "currency": "CNY",
      "stock": 31,
      "landed_total": 296,
      "material_tags": ["金属", "天然纤维"]
    }
  ],
  "omitted_hit_count": 17,
  "raw_result_available": true
}
```

### 阶段 B：低频、离散的 LLM 摘要

只有阶段 A 后仍超过阈值才摘要。摘要后保留：

- 权威 `ShoppingTaskState`；
- 检索出的必要长期偏好；
- 新摘要；
- 最近热尾部；
- 当前用户消息。

不要每轮微调旧摘要。低频离散压缩虽然会让 conversation cache 重建一次，却能让随后的多轮请求共享一个稳定、明显更短的前缀。

## 9. 压缩触发点怎么定

不存在跨模型、跨工具的统一最优百分比。触发点应满足容量约束：

```text
trigger_tokens
≤ context_window
  - 最大单次新工具结果
  - 最大回复预算
  - 压缩摘要输出预算
  - system/tools 变化余量
  - 安全余量
```

对当前 128K 配置：

- 75% 触发即约 96K token；
- 15% 热尾部即约 19.2K token；
- `TOOL_RESULT_LIMIT=20000` 在 AgentScope 中是 token 量级，而不是字符量级。

这意味着在最坏情况下，一次接近 20K token 的工具结果可能让 95K 直接跳到 115K，留给下一轮输出、摘要和系统增长的空间偏紧。

建议的起始实验配置：

| 前置条件 | `trigger_ratio` | `reserve_ratio` | 单工具投影上限 |
|---|---:|---:|---:|
| 尚未做工具投影 | 0.60–0.65 | 0.12–0.15 | 现状 20K，仅作为过渡 |
| 已把主要工具投影到 4K–8K | 0.68–0.72 | 0.12–0.15 | 4K–8K |
| 有真实 P99 数据后 | 由公式与实验确定 | 按热尾部任务保真调 | 按工具分别配置 |

不要直接把这些区间当生产真值。应从 trace 统计 P95/P99 的 system token、tool result token、每轮模型输出和 AgentLoop 深度，再计算安全阈值。

除 token 阈值外，建议加入语义触发：

- 用户明确切换到无关品类/新任务：优先结束旧任务并压缩或开启新会话；
- 订单创建/取消完成：在交易自然边界做一次可选压缩；
- 恢复长时间未活跃且缓存已冷的超长会话：先压缩再继续；
- 连续两次压缩后很快再次越线：停止抖动并报 `context.thrashing`，检查单个超大工具结果。

## 10. Prompt Cache 在 Globex 中如何落位

### 10.1 第一阶段：先测隐式缓存

当前百炼隐式缓存自动生效，优先把可观测补齐：

- `prompt_tokens`
- `cached_tokens`
- `cache_creation_input_tokens`（若响应返回）
- `cache_read_ratio = cached_tokens / prompt_tokens`
- 按 `model + prompt_version + toolset_hash + session_id` 分组
- 压缩发生前后一轮的命中变化

如果稳定 system/tools 前缀已经有良好命中，显式缓存的额外收益可能有限。

### 10.2 第二阶段：按收益加显式点

建议最多先加两个点：

1. **P1：稳定 system prompt 末尾**。目标是让 system + 固定工具 schema 在压缩后仍能复用。
2. **P2：当前 compact summary / 权威状态块末尾**。目标是让一次 ReAct 循环中的多次模型调用复用会话基线。

不建议一开始就维护“最近 K 个工具调用动态断点”。它会增加 formatter、消息合并、20-block 回看限制和失效排查的复杂度，收益必须由实际 `cached_tokens` 证明。

### 10.3 需要改动的适配层

当前业务模型在 `app/infrastructure/llm.py` 构造。显式缓存应封装在自定义 `OpenAIChatFormatter` 或模型请求适配器中：

- 保证工具 schema 顺序和 JSON 序列化稳定；
- 将 system/summary 标为 content block 并加 `cache_control`；
- 兼容不支持该字段的备用模型/网关，失败时自动降级为无标记；
- 不把用户 ID、当前时间或动态预算拼进 P1；
- 补充快照测试，确认消息形状和 cache marker 位置。

## 11. 一个完整电商对话示例

### 第 1 轮：用户提出需求

用户：

> 推荐 300 元以内、不要塑料、能寄美国的旅行三件套。

系统做三件事：

1. `PreferenceStore` 提供长期 dislike，例如“不要塑料”；
2. `ShoppingTaskState.query_constraints` 写入本轮预算 300 CNY、ship_to=US；
3. `product_search_tool` 返回完整结果到冷事件层，只把 Top 候选投影给模型。

状态示意：

```json
{
  "active_goal": "推荐旅行三件套",
  "query_constraints": {
    "price_max_major": 300,
    "currency": "CNY",
    "ship_to": "US",
    "excluded_material_tags": ["合成聚合物"],
    "scope": "current_query"
  },
  "shortlisted_items": [
    {"product_id": "P1007", "sku_id": "SKU-BLUE", "verified_price": 268}
  ]
}
```

### 第 2 轮：用户选择商品

用户：

> 第二个不错，换成蓝色，有货就准备下单。

“第二个”先解析为当前 shortlist 的确定性引用。查询 SKU 后更新 `selected_item`，生成确认卡：

```json
{
  "confirmation_id": "cfm_42",
  "version": 1,
  "item": {"product_id": "P1007", "sku_id": "SKU-BLUE", "quantity": 1},
  "verified_total": {"amount": 296, "currency": "CNY"},
  "status": "awaiting_user_confirmation",
  "payload_hash": "sha256:..."
}
```

### 此时触发压缩

旧候选列表和工具原文被清理；自然语言历史被压成：

```json
{
  "goal_and_rationale": "用户在无塑料、300 元预算和可寄美国约束下选择了蓝色款。",
  "conversation_commitments": ["已展示确认卡，等待明确确认"],
  "open_questions": [],
  "source_event_ids": ["search_evt_001", "sku_evt_004"]
}
```

但真正决定能否下单的仍是 `pending_confirmation`，不是这段摘要。

### 第 3 轮：用户确认

用户：

> 确认，就买这个。

交易护栏读取当前 `pending_confirmation.status` 和 hash，确认没有商品、价格、数量或地址变更，再调用下单工具。即使摘要把价格漏掉，订单仍按权威状态执行；如果确认卡已过期或 SKU 价格变化，则重新查询并要求再次确认。

这就是“自己维护上下文”的核心好处：**模型负责理解和表达，代码负责事实和状态迁移。**

## 12. 分阶段落地路线

### P0：先补正确性与观测，1 个迭代

- 新增 `ShoppingTaskState`，先覆盖 `selected_item`、`pending_confirmation`、`order_refs`。
- 修正 `TOOL_RESULT_LIMIT` 的单位说明，并按 token 配置。
- 为 `product_search_tool` 增加 LLM 投影视图，完整结果仍进事件流水。
- 扩展 `context.compressed`：记录压缩前后 token、摘要版本、耗时。
- 从模型 usage 上报 `cached_tokens`，建立隐式缓存基线。
- 增加压缩前后关键事实一致性测试。

建议涉及文件：

- `app/application/agents/context_policy.py`
- `app/application/agents/orchestrator.py`
- `app/application/tools/product_search_tool.py`
- `app/infrastructure/llm.py`
- `app/infrastructure/settings.py`
- `app/domain/session/` 下新增结构化状态定义与端口

### P1：两阶段压缩与按需回查，1–2 个迭代

- 新增 `ContextAssembler`，每轮从六层来源重建最小上下文。
- 在 LLM 摘要前清理/投影旧工具结果。
- 把原始工具输出保存为可检索事件引用。
- 新增 `conversation_fact_lookup`，只回查必要历史事实。
- 摘要采用领域 schema，并做 ID/金额来源校验。
- 将偏好注入 revision 持久化，压缩/重启后确定性重注入。

### P2：显式缓存实验与自适应阈值，数据证明后再做

- 自定义 Formatter 支持 P1/P2 `cache_control`。
- 记录 cache create/read token、TTL 内复用次数、TTFT 与总费用。
- A/B：隐式缓存 vs system 显式点 vs system+summary 两点。
- 用 P99 工具输出与 AgentLoop 深度计算触发阈值。
- 增加 thrashing detector，连续快速压缩时停止并定位超大来源。

## 13. 评测与验收

### 13.1 正确性指标

| 指标 | 目标 |
|---|---:|
| 硬约束保留率（dislike/目的国） | 100% |
| product_id / sku_id 保真率 | 100% |
| 订单号与状态保真率 | 100% |
| 未确认下单率 | 0 |
| 金额无来源生成率 | 0 |
| 一次性预算跨任务泄漏率 | 0 |

### 13.2 效率指标

| 指标 | 说明 |
|---|---|
| active_context_tokens P50/P95/P99 | 每次真实送模 token |
| tool_result_tokens_by_tool | 找到主要膨胀源 |
| compression_ratio | 压缩前后有效 token 比 |
| compression_interval | 两次压缩间隔，过短表示抖动 |
| cached_tokens / prompt_tokens | Prompt Cache 实际收益 |
| TTFT / total latency | 压缩和缓存对延迟的真实影响 |
| cost_per_successful_task | 不能只看单次 token |

### 13.3 必测回放场景

1. 第一轮说“不要塑料”，20 轮后换品类，仍不得推荐塑料。
2. 第一轮预算 300，完成后新搜耳机，不得默认继续沿用 300。
3. 多次搜索后说“第二个”，必须绑定最新有效 shortlist。
4. 展示确认卡后发生 SKU/价格变化，旧确认不得继续下单。
5. 压缩发生在确认与下单之间，订单字段仍完全一致。
6. 服务重启并恢复 `AgentState`，偏好 revision 不重复/不遗漏。
7. 单个工具返回超大结果，不得触发连续压缩抖动。
8. 压缩前后分别检查 Qwen cache read/create token 与成本。

## 14. 对现有教程的修订建议

当前《05 Cache-Breakpoint 上下文压缩与缓存治理》已有分层治理意识，但建议修正：

1. 将“压缩和缓存是同一个问题的两面”改为“二者相互影响，但解决不同问题”。
2. 删除“Breakpoint 之前永远不动”的绝对说法，说明全量 compaction 会重建 conversation cache。
3. 不再把“最近 K 个工具调用”描述为通用最优位置，改为按内容稳定性、TTL 和复用次数决策。
4. 将示例中的压缩方向、缓存区域和可变尾部统一，避免“最近 K 轮到底在断点前还是后”的文字矛盾。
5. 未附原始实验记录前，把“85%→15%”“降低 35%”标成示例值，而非 Globex 实测事实。
6. 将 AgentScope `tool_result_limit` 明确写成 token 上限。
7. 增加 Claude Code 的“旧工具结果先清理、结构化事实重注入、subagent 隔离、完整 transcript 另存”四个关键机制。

## 15. 最终建议

Globex 不需要复制 Claude Code 的产品细节，而应复制它的治理原则：

> **稳定规则可缓存，精确事实结构化，长期知识按需取，近期消息原样留，旧工具结果先清，旧对话低频摘要，完整轨迹永远另存。**

具体决策是：

- 保留 AgentScope 现有压缩框架；
- 先建设 `ShoppingTaskState` 与工具结果双视图；
- 再实现两阶段压缩和冷历史回查；
- 先量化 Qwen 隐式缓存，后决定显式 Cache Breakpoint；
- 将“压缩后还能正确下单”作为主验收，而不是“摘要变短了”。

## 参考资料

### Claude Code 官方

- [Explore the context window](https://code.claude.com/docs/en/context-window)
- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)
- [How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Create custom subagents](https://code.claude.com/docs/en/sub-agents)
- [Manage sessions](https://code.claude.com/docs/en/sessions)
- [Checkpointing](https://code.claude.com/docs/en/checkpointing)
- [Hooks reference](https://code.claude.com/docs/en/hooks)

### Anthropic API 官方

- [Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Manage tool context](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context)
- [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### Qwen / 百炼官方

- [上下文缓存（Context Cache）](https://help.aliyun.com/zh/model-studio/context-cache)
- [显式缓存最佳实践](https://help.aliyun.com/zh/model-studio/explicit-cache-guide)

## 研究边界

Claude Code 当前公开分发包含原生运行组件，公开文档没有给出全部内部实现源码、所有默认摘要 prompt 与工具结果清理启发式。因此本文只把官方文档明确描述的行为写成事实。Anthropic API 的 context editing 和 server-side compaction 是可用能力，但不能据此断言 Claude Code 的全部版本与供应商路径都直接使用这些 beta 参数。
