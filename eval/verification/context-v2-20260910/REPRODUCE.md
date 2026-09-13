# 复现与证据口径

在独立目录展开 `frozen-metered-source.tar.gz`，使用项目锁定依赖和 Python 虚拟环境。通过环境变量配置同一网关的 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL=qwen3-max`；归档不含密钥。关闭 Langfuse/OTel 原文上报，评测使用隔离买家与输出目录。

在该冻结源码根目录执行：

```bash
python -m scripts.eval.run_context \
  --split holdout \
  --cases-file eval/context/cases-metered.json \
  --strategies legacy,layered \
  --timing pressure \
  --concurrency 2 \
  --min-interval-seconds 5 \
  --model-retries 0 \
  --repetitions 3 \
  --output /tmp/globex-context-cost-reproduction
```

输出目录必须是本次实验专用的新目录，不能指向生产数据库。不要与其它同网关大批量实验并行，以免独立限流器叠加并发。网络错误或未知 usage 原样保留，不挑选成功回答补齐成本。

6个场景，每个场景A/C各重复3次，共36次。每次24轮选购历史加1轮验收续答；第9、18轮整理，第14轮从数据库恢复。随机交错顺序、目录、偏好、工具夹具与问题由冻结脚本和案例确定。真实请求仍由远端模型执行，结果可有随机波动；同名模型的服务端版本由网关提供，无法保证永远不变。

原始业务模型、摘要模型与回查后的模型 usage 全部计入长对话成本，格式校验失败的调用也计入。缺少 usage 不记零；回查后的调用包含在业务累计中，单独归因未知。表达盲评的评委消耗单列，不能混入买家长对话成本。

正式报告由本目录 `report_metered_cost.py` 生成，函数 `report(input_folder, output_folder)` 可用于新的输入目录；输出目录父级需要匹配的 `metered-cost-protocol.json`。它检查冻结案例、策略、次数、usage完整性和预登记门槛，不应通过修改阈值让复现结果过关。

开发108次、主留出252次、网络整组恢复21次和独立全成本36次是不同阶段，分别报告。旧阶段漏计摘要的成本已标无效，保留原始结果用于审计。匿名辅助盲评的规则、映射、逐次输出与评委成本在 `blind-review/`。

本实验使用隔离工具夹具，不能替代生产检索排序、真实交易写路径、首次推荐独立评分或生产流量P95评测。配对区间以场景为统计单位，不能把同场景重复当作独立场景扩大样本量。
