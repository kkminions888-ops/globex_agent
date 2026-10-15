export type EvaluationMetric = {
  key: string;
  label: string;
  baseline: number | null;
  candidate: number | null;
  delta: number | null;
  threshold: number | string | null;
  passed: boolean | null;
  unit: string;
};
export type EvaluationReport = {
  id: string;
  label: string;
  created_at?: string | null;
  status: "completed" | "failed" | "not_run";
  scope: "formal" | "diagnostic";
  role: "baseline" | "candidate";
  standard_id?: string;
  latency_scope?: "offline" | "replay_after_frozen_real_retrieval";
  provenance: Record<string, unknown>;
  summary: {
    ndcg_at_5?: number | null;
    recall_at_5?: number | null;
    returned_precision?: number | null;
    empty_accuracy?: number | null;
    hard_constraint_violation_rate?: number | null;
    hard_constraint_violations?: Record<string, number | null>;
    constraint_case_counts?: Record<string, number | null>;
    constraint_positive_case_counts?: Record<string, number | null>;
    hard_constraint_case_failures?: Record<string, number | null>;
    positive_cases?: number | null;
    empty_cases?: number | null;
    p95_ms?: number | null;
  };
};
export type EvaluationCase = {
  id: string;
  kind: string;
  baseline_ndcg: number | null;
  candidate_ndcg: number | null;
  delta: number | null;
  violation_count: number | null;
};
export type RetrievalEvaluationData = {
  standard: {
    id: string;
    version?: string | number;
    title: string;
    description?: string;
    k: number;
    metrics: { key: string; label: string; definition: string; aggregation?: string; unit: string }[];
    gates: Record<string, { min_delta?: number; max_count?: number; required_dimensions?: string[] }>;
    notes?: string[];
  };
  baseline_definition?: Record<string, unknown>;
  reports: EvaluationReport[];
  selected_baseline_id: string | null;
  selected_candidate_id: string | null;
  comparison: {
    status: "passed" | "blocked" | "not_comparable" | "pending" | "diagnostic";
    comparable: boolean;
    reasons: string[];
    metrics: EvaluationMetric[];
    cases: EvaluationCase[];
  };
  warnings: string[];
};

export function evaluationValue(value: unknown, unit = "ratio"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "待测";
  if (unit === "count") return String(value);
  if (unit === "ms") return `${value.toFixed(0)} ms`;
  return `${(value * 100).toFixed(1)}%`;
}

export function evaluationDelta(value: unknown, unit = "ratio"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "待测";
  const sign = value > 0 ? "+" : "";
  return unit === "count" ? `${sign}${value}` : `${sign}${(value * 100).toFixed(1)} 个百分点`;
}

// 展示层保留诊断和不可比边界，避免把导入报告的通过字段误当作正式验收。
export function evaluationStatus(data: RetrievalEvaluationData): RetrievalEvaluationData["comparison"]["status"] {
  const baseline = data.reports.find(report => report.id === data.selected_baseline_id);
  const candidate = data.reports.find(report => report.id === data.selected_candidate_id);
  if (!baseline || !candidate || baseline.status !== "completed" || candidate.status !== "completed") return "pending";
  if (!data.comparison.comparable) return "not_comparable";
  if (baseline.scope === "diagnostic" || candidate.scope === "diagnostic") return "diagnostic";
  return data.comparison.status;
}

export function evaluationPath(baseline: string, candidate: string): string {
  const params = new URLSearchParams();
  if (baseline) params.set("baseline_id", baseline);
  if (candidate) params.set("candidate_id", candidate);
  return `/retrieval-evaluation${params.size ? `?${params.toString()}` : ""}`;
}
