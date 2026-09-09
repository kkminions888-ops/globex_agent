# Prompt 不可变版本与人工发布

2026-09-09 实现机制。本轮没有运行新的真实候选成对评测，也没有发布实验版本，因此不能声称已证明成本或成功率收益。

## 版本如何生效

`DATA_DIR/prompts/registry.sqlite3` 保存不可变 Prompt 内容、工具契约、发布时间、部署历史、会话分组和紧急撤销。版本 ID 是规范化 Prompt 正文与工具契约的完整 SHA256；工具/权限/factory 源码、可选 web 工具状态、Skill/策略工具契约及 capability registry 权限实现共同参与，正文一样但工具集合不同不会共用版本。加载时复核内容 hash 和当前工具契约，不允许伪装同版本。

仅注册表为空时，服务首次启动以当前 `globex.yml` 建立 `bootstrap`，保持原有行为。这是初始现状记录，不是候选发版通过。注册表存在后，修改 YAML 不会自动改变正在服务的版本，必须显式导入、评测和发布。/health 的 `prompt_registry` 返回实际部署和有效版本元数据，不返回正文。

每个新会话在数据库事务内绑定 buyer、version、deployment、variant。A/B 以 experiment 和 buyer 的 SHA256 确定分桶；同一买家新会话稳定分组，同一会话始终使用原版本。主 Agent、子 Agent 从相同 ShoppingContext 取不可变正文；语义缓存 key 带实际版本。Trace、日志上下文、事件和已持久事件流水携带 prompt_version/variant/deployment，便于关联评测。

## 操作员 CLI

所有命令在可信本机执行，无自动发布任务或 HTTP 发布 endpoint。不要复制真实业务数据库去做评测。为隔离评测目录导入相同基线和候选，使用相同代码、同工具、同冻结数据；两个进程分别通过 `PROMPT_PIN_VERSION` 固定版本，使用新的隔离会话。候选 YAML 建议放在数据/实验目录，避免成对评测时更改正在运行的代码与默认 YAML。

```sh
.venv/bin/python -m scripts.prompt_release --data-dir data status
.venv/bin/python -m scripts.prompt_release --data-dir data import --yaml /absolute/path/candidate.yml
# 输出 version_id、内容与工具 hash，不自动发布。

.venv/bin/python -m scripts.prompt_release --data-dir data publish \
  --baseline p-BASELINE_HASH --candidate p-CANDIDATE_HASH \
  --candidate-bps 1000 --experiment shopping-prompt-v2 \
  --baseline-manifest /absolute/path/baseline.manifest.json \
  --candidate-manifest /absolute/path/candidate.manifest.json

.venv/bin/python -m scripts.prompt_release --data-dir data rollback --deployment-id pd-PREVIOUS_DEPLOYMENT
.venv/bin/python -m scripts.prompt_release --data-dir data revoke --version-id p-BAD_VERSION --reason '人工确认的撤销原因'
```

`candidate-bps` 是 0..10000 基点，1000 表示 10%，10000 表示全量新会话。`PROMPT_PIN_VERSION` 仅用于隔离评测或明确配置的固定版本进程；已有会话若曾绑定别的版本会拒绝，不悄悄切换。运行容器 CLI 需要提供脚本；当前主镜像不含管理脚本，可在挂载同一受信数据卷的管理环境运行，不能声称镜像自带发布平台。

回滚只影响新会话；旧会话保持原版本。若候选有紧急问题，先回滚到可信部署，再 revoke 违规版本；已绑定旧会话会明确失败，提示创建新会话，而不是把不同 Prompt 混入原上下文。工具升级后旧版本若契约不符也明确停止；可以将当前基线的相同正文重新绑定新工具形成新基线，再在新工具环境成对评测，通过后发布，不得套用旧工具报告。

## 发布门禁

CLI 消费 Agent runner 的 `.manifest.json`，要求：

- 完整 release 选集、真实执行、COMPLETED/PASS、inputs_unchanged、服务实际源文件 `matched`，且关闭语义缓存。
- 报告明确记录对应 `prompt_registry.effective_version` 的 version/content/toolset hash。旧报告缺字段即阻断，不能用本地 YAML hash 代替实际服务版本。
- baseline/candidate 使用相同冻结代码、数据/知识、选集内容、模型与实际检索策略；工具契约相同。不能把不同实现/降级路径的结果当纯 Prompt 实验。
- 每条用例实际执行且无重复/遗漏，P0 全过、verdict PASS；任务成功率与硬约束率不能下降。
- 每条 `metrics` 必须有完整真实 input/output token、usage_complete、正数 elapsed_ms 与明确时延口径；汇总必须与逐条证据一致。时延不含 Judge，包含 Agent 对话和 HTTP 用户确认动作。
- P95 总时延不得退化超过 20%；同模型的输入+输出 Token 成本代理不得退化超过 10%。该代理不是货币成本；无价表 cost_usd 保持 unknown，不编造美元值。缺模型 usage 时不能靠 unknown 或零值放行。

本地操作员仍需审核真实报告来源；此机制保存报告 SHA256 与最小指标证据，不是远程证明或自动防伪签名平台，也不把完整对话/地址写入发布记录。报告不满足要求时没有发布副作用。

## 验证

`tests/test_prompt_registry.py` 使用临时库和显式测试证据验证：首次并发初始化、不可变 hash、工具契约隔离、跨实例会话原子分组、买家稳定分桶、主/子 loader 同版本、缓存隔离、Registry 重启恢复、回滚/撤销、未知/过期/不完整证据阻断，以及成本和时延退化门禁。真实收益和正式候选发布仍需新的完整成对 release。
