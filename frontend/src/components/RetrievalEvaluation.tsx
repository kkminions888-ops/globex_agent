import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { evaluationDelta, evaluationPath, evaluationStatus, evaluationValue, type EvaluationMetric, type EvaluationReport, type RetrievalEvaluationData } from "../lib/retrievalEvaluation";
import "./retrievalEvaluation.css";

type Props = { request: (path: string, method?: string, body?: any) => Promise<any> };
type CaseFilter = "all" | "regression" | "improvement" | "violation";
const STATUS = {
  passed: { title: "达到优化标准", detail: "本次可比报告达到全部门槛。" },
  blocked: { title: "尚未达到优化标准", detail: "查看未达标指标与退化样本，修复后保留新的候选报告。" },
  not_comparable: { title: "报告不可比", detail: "先统一数据、检索口径与评测配置，再作优化验收。" },
  pending: { title: "当前基线与候选待测", detail: "标准已建立；真实评测完成后，这里展示前后差异。" },
  diagnostic: { title: "诊断对照，尚未完成正式验收", detail: "查看报告来源与约束覆盖；离线算法和冻结真实候选回放均保留为诊断证据。" },
};
const DIMENSIONS = [{ key: "budget", label: "预算" }, { key: "material", label: "材质" }, { key: "shipping", label: "配送" }];
const SOURCE_FIELDS = [
  ["catalog_sha256", "商品目录指纹"], ["dataset_sha256", "标注集指纹"], ["selection_sha256", "查询选择指纹"],
  ["source_sha256", "源码指纹"], ["config_sha256", "配置指纹"], ["embedding_identity", "Embedding 标识"],
  ["query_mode", "查询模式"], ["split", "数据划分"], ["k", "返回上限 K"], ["repetitions", "重复次数"],
  ["catalog_count", "商品数"], ["case_count", "查询数"],
];
function sourceValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "待记录";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function gateText(metric: EvaluationMetric, data: RetrievalEvaluationData): string {
  if (metric.key.includes("hard_constraint")) return "三类违规均为 0，且均有正例覆盖";
  const minimum = data.standard.gates[metric.key]?.min_delta;
  if (typeof minimum === "number") return `变化 ≥ ${evaluationDelta(minimum)}`;
  return typeof metric.threshold === "string" ? metric.threshold : "按版本化标准验收";
}
function metricFor(data: RetrievalEvaluationData, key: string, label: string, baseline?: EvaluationReport, candidate?: EvaluationReport): EvaluationMetric {
  const result = data.comparison.metrics.find(metric => metric.key === key);
  if (result) return data.comparison.comparable ? result : { ...result, delta: null, passed: null };
  const b = baseline?.status === "completed" ? baseline.summary[key as keyof EvaluationReport["summary"]] : null;
  const c = candidate?.status === "completed" ? candidate.summary[key as keyof EvaluationReport["summary"]] : null;
  return { key, label, baseline: typeof b === "number" ? b : null, candidate: typeof c === "number" ? c : null,
    delta: data.comparison.comparable && typeof b === "number" && typeof c === "number" ? c - b : null, threshold: null, passed: null, unit: "ratio" };
}
function MetricCard({ metric, data, canJudge }: { metric: EvaluationMetric; data: RetrievalEvaluationData; canJudge: boolean }) {
  const verdict = canJudge && metric.passed !== null ? (metric.passed ? "达标" : "未达标") : "待验收";
  const descriptions: Record<string, string> = {
    ndcg_at_5: "衡量相关商品是否排在前面，越高越好。",
    recall_at_5: "前五项覆盖了多少标注相关商品，越高越好。",
    returned_precision: "实际展示结果中相关商品的占比，同款重复不加分。",
    hard_constraint_violation_rate: "预算、材质和配送逐项核对；无覆盖不能判定通过。",
  };
  const definition = descriptions[metric.key] ?? data.standard.metrics.find(item => item.key === metric.key)?.definition;
  return <article className={`retrieval-metric ${canJudge && metric.passed === false ? "metric-blocked" : ""}`}>
    <div className="retrieval-card-title"><h3>{metric.label}</h3><span className={`retrieval-verdict ${canJudge && metric.passed === true ? "is-pass" : ""}`}>{verdict}</span></div>
    <p className="retrieval-definition">{definition ?? "在固定查询与商品目录下比较候选和基线。"}</p>
    <div className="retrieval-values"><div><span>基线</span><strong>{evaluationValue(metric.baseline, metric.unit)}</strong></div><div><span>候选</span><strong>{evaluationValue(metric.candidate, metric.unit)}</strong></div></div>
    <div className="retrieval-bars" aria-label={`${metric.label}对照图`}>
      {[{ value: metric.baseline, label: "基线", kind: "baseline" }, { value: metric.candidate, label: "候选", kind: "candidate" }].map(item => <div key={item.kind} className="retrieval-bar-row"><span>{item.label}</span><div className="retrieval-bar-track">{typeof item.value === "number" && Number.isFinite(item.value) && <i className={`retrieval-bar ${item.kind}`} style={{ width: `${Math.max(0, Math.min(100, item.value * (metric.unit === "count" ? 10 : 100)))}%` }} />}</div><span>{evaluationValue(item.value, metric.unit)}</span></div>)}
    </div>
    <div className="retrieval-metric-footer"><strong>变化 {evaluationDelta(metric.delta, metric.unit)}</strong><span>{gateText(metric, data)}</span></div>
  </article>;
}

export default function RetrievalEvaluation({ request }: Props) {
  const [data, setData] = useState<RetrievalEvaluationData | null>(null);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<CaseFilter>("all");
  const sequence = useRef(0);
  const selection = useRef({ baseline: "", candidate: "" });
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError("");
    try {
      const result = await request(evaluationPath(selection.current.baseline, selection.current.candidate));
      if (current !== sequence.current) return;
      if (!result?.standard || !Array.isArray(result.reports) || !result.comparison || !Array.isArray(result.comparison.metrics) || !Array.isArray(result.comparison.cases)) throw new Error("检索评测报告格式无效");
      setData(result as RetrievalEvaluationData);
      const next = { baseline: result.selected_baseline_id ?? "", candidate: result.selected_candidate_id ?? "" };
      selection.current = next; setBaselineId(next.baseline); setCandidateId(next.candidate);
    } catch (reason) {
      if (current === sequence.current) setError(reason instanceof Error ? reason.message : "评测报告读取失败，请重试");
    } finally { if (current === sequence.current) setLoading(false); }
  }, [request]);
  useEffect(() => { void refresh(); return () => { sequence.current++; }; }, [refresh]);
  const select = (role: "baseline" | "candidate", value: string) => {
    selection.current = { ...selection.current, [role]: value };
    if (role === "baseline") setBaselineId(value); else setCandidateId(value);
    void refresh();
  };
  const baseline = data?.reports.find(report => report.id === data.selected_baseline_id);
  const candidate = data?.reports.find(report => report.id === data.selected_candidate_id);
  const state = data ? evaluationStatus(data) : "pending";
  const canJudge = !error && !loading && (state === "passed" || state === "blocked");
  const baselineProvenance = data?.baseline_definition?.provenance as Record<string, unknown> | undefined;
  const baselineSelection = data?.baseline_definition?.selection as Record<string, unknown> | undefined;
  const cases = useMemo(() => (data?.comparison.cases ?? []).filter(item => filter === "all" || (filter === "regression" && item.delta !== null && item.delta < 0) || (filter === "improvement" && item.delta !== null && item.delta > 0) || (filter === "violation" && (item.violation_count ?? 0) > 0)).sort((a, b) => (a.delta ?? Infinity) - (b.delta ?? Infinity)), [data, filter]);
  const metrics = data ? [metricFor(data, "ndcg_at_5", "NDCG@5", baseline, candidate), metricFor(data, "recall_at_5", "Recall@5", baseline, candidate), metricFor(data, "returned_precision", "返回结果精度", baseline, candidate), metricFor(data, "hard_constraint_violation_rate", "硬约束违规率", baseline, candidate)] : [];

  return <section className="retrieval-evaluation" aria-label="商品检索评测" aria-busy={loading}>
    <header className="retrieval-heading"><div><span className="retrieval-eyebrow">检索质量 · 前后对照</span><h1>让合适的商品，排在前面</h1><p>固定商品目录与标注查询，用同一套标准衡量每次优化。</p></div><button className="retrieval-refresh" disabled={loading} onClick={() => void refresh()}>{loading ? "读取中…" : "刷新报告"}</button></header>
    {error && <div className="retrieval-error" role="alert">{error}{data && "。当前显示上次读取的报告，尚未确认最新结果。"}<button onClick={() => void refresh()}>重新读取</button></div>}
    {!data ? <div className="retrieval-empty" role="status">{loading ? "正在读取优化标准与评测报告…" : "暂时没有可展示的评测报告。请重新读取。"}</div> : <>
      <div className={`retrieval-status status-${error || loading ? "pending" : state}`} role="status"><span className="retrieval-status-dot" /><div><strong>{error || loading ? "报告尚未确认" : STATUS[state].title}</strong><p>{error || loading ? "等待本次读取完成后再判断验收状态。" : STATUS[state].detail}</p></div><span className="retrieval-standard-id">{data.standard.id}</span></div>
      {data.comparison.reasons?.length > 0 && <ul className="retrieval-reasons">{data.comparison.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
      {data.warnings?.length > 0 && <div className="retrieval-warnings">{data.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}
      <aside className="retrieval-formal-state"><strong>当前模型正式基线</strong><span>{data.reports.some(report => report.role === "baseline" && report.scope === "formal" && report.status === "completed") ? "已有正式报告，请选择同口径候选进行验收" : "待测 · 数据与标准冻结不代表模型检索已通过"}</span>{data.baseline_definition?.id != null && <code>{sourceValue(data.baseline_definition.id)}</code>}</aside>
      <div className="retrieval-selectors"><label>基线报告<select value={baselineId} onChange={event => select("baseline", event.target.value)} disabled={loading}><option value="">选择基线</option>{data.reports.filter(report => report.role === "baseline").map(report => <option key={report.id} value={report.id}>{report.label} · {report.status === "completed" ? (report.scope === "diagnostic" ? "诊断对照" : "已完成") : report.status === "failed" ? "失败" : "待测"}</option>)}</select></label><span className="retrieval-vs">对照</span><label>候选报告<select value={candidateId} onChange={event => select("candidate", event.target.value)} disabled={loading}><option value="">选择候选</option>{data.reports.filter(report => report.role === "candidate").map(report => <option key={report.id} value={report.id}>{report.label} · {report.status === "completed" ? (report.scope === "diagnostic" ? "诊断对照" : "已完成") : report.status === "failed" ? "失败" : "待测"}</option>)}</select></label></div>
      <div className="retrieval-metrics">{metrics.map(metric => <MetricCard key={metric.key} metric={metric} data={data} canJudge={canJudge} />)}</div>
      <div className="retrieval-detail-grid"><article className="retrieval-panel"><h2>硬约束违规检查</h2><p>预算、材质和配送分别检查；缺少正例覆盖不能判定通过。</p><div className="retrieval-table-scroll"><table className="retrieval-table"><thead><tr><th>约束</th><th>基线违规商品</th><th>候选违规商品</th><th>候选适用查询</th><th>候选适用正例</th><th>标准</th></tr></thead><tbody>{DIMENSIONS.map(dimension => <tr key={dimension.key}><th>{dimension.label}</th><td>{evaluationValue(baseline?.status === "completed" ? baseline.summary.hard_constraint_violations?.[dimension.key] : null, "count")}</td><td>{evaluationValue(candidate?.status === "completed" ? candidate.summary.hard_constraint_violations?.[dimension.key] : null, "count")}</td><td>{evaluationValue(candidate?.status === "completed" ? candidate.summary.constraint_case_counts?.[dimension.key] : null, "count")}</td><td>{evaluationValue(candidate?.status === "completed" ? candidate.summary.constraint_positive_case_counts?.[dimension.key] : null, "count")}</td><td>0 违规且有正例覆盖</td></tr>)}</tbody></table></div><p className="retrieval-footnote">违规率按“查询 × 约束维度”计算：至少一件违规商品即该维度失败。违规商品列按商品与约束对计数，分母为 0 时显示待测。</p></article>
      <article className="retrieval-panel"><h2>评测口径与保护指标</h2><dl className="retrieval-protocol"><div><dt>标准版本</dt><dd>{data.standard.version ?? data.standard.id}</dd></div><div><dt>排序与召回</dt><dd>Top-{data.standard.k} · 固定标注查询</dd></div><div><dt>查询模式</dt><dd>{sourceValue(baseline?.provenance.query_mode ?? baselineProvenance?.query_mode)}</dd></div><div><dt>数据划分</dt><dd>{sourceValue(baseline?.provenance.split ?? baselineSelection?.split)}</dd></div><div><dt>无结果准确率</dt><dd>基线 {evaluationValue(baseline?.summary.empty_accuracy)} / 候选 {evaluationValue(candidate?.summary.empty_accuracy)}</dd></div><div><dt>无结果保护门槛</dt><dd>准确率不退化，缺少覆盖待验收</dd></div><div><dt>{baseline?.latency_scope === "replay_after_frozen_real_retrieval" || candidate?.latency_scope === "replay_after_frozen_real_retrieval" ? "回放 P95（非线上）" : "记录耗时 P95"}</dt><dd>基线 {evaluationValue(baseline?.summary.p95_ms, "ms")} / 候选 {evaluationValue(candidate?.summary.p95_ms, "ms")}</dd></div></dl><p className="retrieval-footnote">精度以实际返回数量为分母；同时检查召回与空结果，防止通过少返回商品制造提升。指标差值以百分点展示。离线或回放耗时不能作为线上延迟收益。</p></article></div>
      <article className="retrieval-panel retrieval-cases"><div className="retrieval-panel-heading"><div><h2>逐查询差异</h2><p>按 NDCG 差值从低到高排列，先查看退化样本。</p></div><label>样本筛选<select value={filter} onChange={event => setFilter(event.target.value as CaseFilter)}><option value="all">全部样本</option><option value="regression">排序退化</option><option value="improvement">排序提升</option><option value="violation">约束违规</option></select></label></div>{cases.length ? <div className="retrieval-table-scroll"><table className="retrieval-table"><thead><tr><th>查询编号</th><th>场景</th><th>基线 NDCG@5</th><th>候选 NDCG@5</th><th>变化</th><th>违规数</th></tr></thead><tbody>{cases.map(item => <tr key={item.id}><th>{item.id}</th><td>{item.kind}</td><td>{evaluationValue(item.baseline_ndcg)}</td><td>{evaluationValue(item.candidate_ndcg)}</td><td className={item.delta !== null && item.delta < 0 ? "retrieval-negative" : ""}>{evaluationDelta(item.delta)}</td><td>{evaluationValue(item.violation_count, "count")}</td></tr>)}</tbody></table></div> : <div className="retrieval-empty">{data.comparison.cases.length ? "当前筛选没有匹配样本。" : "暂无逐查询结果；完成评测后展示差异。"}</div>}</article>
      <details className="retrieval-panel retrieval-sources"><summary>数据来源与可比性指纹</summary><p>比较商品目录、标注集、查询选择、模型与评测口径；源码和检索配置允许作为实验变量。</p><div className="retrieval-table-scroll"><table className="retrieval-table"><thead><tr><th>来源</th><th>基线</th><th>候选</th></tr></thead><tbody><tr><th>报告编号</th><td>{baseline?.id ?? "待选择"}</td><td>{candidate?.id ?? "待选择"}</td></tr>{SOURCE_FIELDS.map(([key, label]) => <tr key={key}><th>{label}</th><td><code>{sourceValue(baseline?.provenance[key])}</code></td><td><code>{sourceValue(candidate?.provenance[key])}</code></td></tr>)}</tbody></table></div></details>
      <details className="retrieval-panel retrieval-standard"><summary>指标定义与验收标准</summary><p>{data.standard.description}</p><dl>{data.standard.metrics.map(metric => <div key={metric.key}><dt>{metric.label}</dt><dd>{metric.definition}{metric.aggregation && <small>{metric.aggregation}</small>}</dd></div>)}</dl>{data.standard.notes?.map((note, index) => <p key={index}>{note}</p>)}</details>
      <p className="retrieval-readonly">本页只读取已保存的标准和报告。实际模型评测由开发流程执行，并保留原始证据。</p>
    </>}
  </section>;
}
