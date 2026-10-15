// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RetrievalEvaluation from "../src/components/RetrievalEvaluation";
import { evaluationDelta, evaluationValue, type RetrievalEvaluationData } from "../src/lib/retrievalEvaluation";

let host: HTMLDivElement, root: Root;
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });
function fixture(): RetrievalEvaluationData {
  const summary = { ndcg_at_5: .60, recall_at_5: .5, returned_precision: .7, hard_constraint_violation_rate: 0,
    hard_constraint_violations: { budget: 0, material: 0, shipping: 0 }, constraint_case_counts: { budget: 4, material: 2, shipping: 8 },
    constraint_positive_case_counts: { budget: 3, material: 2, shipping: 6 }, p95_ms: 120 };
  return {
    standard: { id: "synthetic-test-standard", title: "测试标准", k: 5, version: 1,
      metrics: [{ key: "ndcg_at_5", label: "NDCG@5", definition: "相关商品是否靠前", unit: "ratio" }],
      gates: { ndcg_at_5: { min_delta: .02 }, recall_at_5: { min_delta: -.01 }, returned_precision: { min_delta: .02 } } },
    baseline_definition: { id: "synthetic-frozen", status: "not_run", provenance: { query_mode: "structured_spec" }, selection: { split: "holdout" } },
    reports: [{ id: "b0", label: "测试基线", status: "completed", role: "baseline", scope: "formal", provenance: { catalog_sha256: "a".repeat(64) }, summary },
      { id: "c1", label: "测试候选", status: "completed", role: "candidate", scope: "formal", provenance: { catalog_sha256: "a".repeat(64) }, summary: { ...summary, ndcg_at_5: .65 } },
      { id: "c2", label: "其他候选", status: "not_run", role: "candidate", scope: "formal", provenance: {}, summary: {} }],
    selected_baseline_id: "b0", selected_candidate_id: "c1", warnings: [],
    comparison: { status: "passed", comparable: true, reasons: [], metrics: [
      { key: "ndcg_at_5", label: "NDCG@5", baseline: .6, candidate: .65, delta: .05, threshold: .02, passed: true, unit: "ratio" },
      { key: "recall_at_5", label: "Recall@5", baseline: .5, candidate: .51, delta: .01, threshold: -.01, passed: true, unit: "ratio" },
      { key: "returned_precision", label: "返回结果精度", baseline: .7, candidate: .73, delta: .03, threshold: .02, passed: true, unit: "ratio" },
      { key: "hard_constraint_violation_rate", label: "硬约束违规率", baseline: 0, candidate: 0, delta: 0, threshold: 0, passed: true, unit: "ratio" },
    ], cases: [{ id: "synthetic-improvement", kind: "direct", baseline_ndcg: .5, candidate_ndcg: .7, delta: .2, violation_count: 0 },
      { id: "synthetic-regression", kind: "scenario", baseline_ndcg: .8, candidate_ndcg: .5, delta: -.3, violation_count: 1 }] },
  };
}
async function render(request: ReturnType<typeof vi.fn>) { await act(async () => root.render(<RetrievalEvaluation request={request} />)); }
async function click(text: string) {
  const button = [...host.querySelectorAll("button")].find(item => item.textContent === text)!;
  expect(button).toBeTruthy(); await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}
async function choose(label: string, value: string) {
  const select = [...host.querySelectorAll("label")].find(item => item.textContent?.startsWith(label))!.querySelector("select")!;
  await act(async () => { select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); });
}

it("展示前后四指标、百分点门槛、约束覆盖与可追溯指纹，并筛选退化样本", async () => {
  const request = vi.fn().mockResolvedValue(fixture()); await render(request);
  expect(request).toHaveBeenCalledWith("/retrieval-evaluation");
  expect(host.querySelectorAll(".retrieval-metric")).toHaveLength(4);
  expect(host.textContent).toContain("达到优化标准");
  expect(host.textContent).toContain("65.0%");
  expect(host.textContent).toContain("+5.0 个百分点");
  expect(host.textContent).toContain("变化 ≥ +2.0 个百分点");
  expect(host.textContent).toContain("候选适用正例");
  expect(host.textContent).toContain("a".repeat(64));
  expect(host.querySelectorAll(".retrieval-cases tbody tr")[0].textContent).toContain("synthetic-regression");
  await choose("样本筛选", "regression");
  expect(host.querySelectorAll(".retrieval-cases tbody tr")).toHaveLength(1);
  expect(host.querySelector(".retrieval-cases")!.textContent).not.toContain("synthetic-improvement");
});

it("未运行与空值显示待测，不渲染零分或验收通过", async () => {
  const data = fixture();
  data.reports.forEach(report => { report.status = "not_run"; report.summary = {}; });
  data.comparison = { status: "pending", comparable: false, reasons: ["基线或候选尚未运行"], metrics: [], cases: [] };
  await render(vi.fn().mockResolvedValue(data));
  expect(host.textContent).toContain("当前基线与候选待测");
  expect(host.querySelector(".retrieval-metrics")!.textContent).toContain("待测");
  expect(host.querySelector(".retrieval-metrics")!.textContent).not.toContain("0.0%");
  expect(host.querySelector(".status-passed")).toBeNull();
  expect(host.querySelector(".is-pass")).toBeNull();
  expect(host.textContent).toContain("暂无逐查询结果");
});

it.each(["diagnostic", "not_comparable"] as const)("%s 即使携带 passed 字段也不能展示绿色通过", async state => {
  const data = fixture();
  if (state === "diagnostic") data.reports[0].scope = "diagnostic";
  else data.comparison.comparable = false;
  await render(vi.fn().mockResolvedValue(data));
  expect(host.querySelector(".status-passed")).toBeNull();
  expect(host.querySelector(".is-pass")).toBeNull();
  expect(host.textContent).toContain(state === "diagnostic" ? "诊断对照，尚未完成正式验收" : "报告不可比");
  expect(host.textContent).toContain("65.0%");
});

it("诊断报告数据不可比时优先标记不可比，保留独立数值但不展示计算差值", async () => {
  const data = fixture();
  data.reports[0].scope = "diagnostic";
  data.reports[1].scope = "diagnostic";
  data.comparison.comparable = false;
  data.comparison.status = "not_comparable";
  data.comparison.metrics = [];
  data.comparison.cases = [];
  await render(vi.fn().mockResolvedValue(data));
  expect(host.querySelector(".status-not_comparable")).not.toBeNull();
  expect(host.querySelector(".status-diagnostic")).toBeNull();
  const metric = host.querySelector(".retrieval-metric")!;
  expect(metric.textContent).toContain("60.0%");
  expect(metric.textContent).toContain("65.0%");
  expect(metric.querySelector(".retrieval-metric-footer")!.textContent).toContain("变化 待测");
  expect(metric.textContent).not.toContain("+5.0 个百分点");
});

it("选择候选携带报告 ID，刷新保留服务端确认的选择", async () => {
  const request = vi.fn(async (path: string) => {
    const data = fixture();
    if (path.includes("candidate_id=c2")) { data.selected_candidate_id = "c2"; data.comparison.status = "pending"; }
    return data;
  });
  await render(request); await choose("候选报告", "c2");
  expect(request.mock.calls.at(-1)?.[0]).toBe("/retrieval-evaluation?baseline_id=b0&candidate_id=c2");
  await click("刷新报告");
  expect(request.mock.calls.at(-1)?.[0]).toBe("/retrieval-evaluation?baseline_id=b0&candidate_id=c2");
});

it("读取失败可以重试，刷新失败保留旧数据并撤销通过显示", async () => {
  const request = vi.fn().mockRejectedValueOnce(new Error("服务暂不可用")).mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new Error("最新报告读取失败"));
  await render(request);
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("服务暂不可用");
  await click("重新读取"); expect(host.querySelector(".status-passed")).not.toBeNull();
  await click("刷新报告");
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("当前显示上次读取的报告");
  expect(host.querySelector(".status-passed")).toBeNull();
  expect(host.querySelector(".is-pass")).toBeNull();
  expect(host.textContent).toContain("65.0%");
});

it("未知与非法数值不会冒充 0，差值使用绝对百分点", () => {
  expect(evaluationValue(null)).toBe("待测");
  expect(evaluationValue(Number.NaN)).toBe("待测");
  expect(evaluationDelta(undefined)).toBe("待测");
  expect(evaluationValue(0)).toBe("0.0%");
  expect(evaluationDelta(-.01)).toBe("-1.0 个百分点");
});
