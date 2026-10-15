"""只读检索评测仪表盘：展示已有证据，不启动模型、任务或业务存储。"""
from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response

from app.infrastructure.retrieval_evaluation_store import (
    EvaluationReadError,
    RetrievalEvaluationStore,
    valid_report_id,
)
from app.presentation.identity import identity_policy, require_buyer, require_metrics_reader


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def register_retrieval_evaluation_routes(api: FastAPI, root: Path | None = None) -> None:
    store = RetrievalEvaluationStore(root or PROJECT_ROOT)

    @api.get("/commerce/retrieval-evaluation")
    async def retrieval_evaluation(
        request: Request,
        response: Response,
        buyer_id: str = Query(default="pao-coder", min_length=1, max_length=128),
        baseline_id: str | None = Query(default=None, min_length=1, max_length=96),
        candidate_id: str | None = Query(default=None, min_length=1, max_length=96),
    ) -> dict[str, Any]:
        # 严格部署中研究证据属于运维读取权限；默认本机演示复用买家身份。
        if identity_policy(request).mode == "hmac":
            await require_metrics_reader(request)
        else:
            await require_buyer(request, buyer_id)
        if any(value is not None and not valid_report_id(value) for value in (baseline_id, candidate_id)):
            raise HTTPException(status_code=422, detail="评测标识格式无效")
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_dashboard, store, baseline_id, candidate_id)


def _dashboard(
    store: RetrievalEvaluationStore,
    baseline_id: str | None,
    candidate_id: str | None,
) -> dict[str, Any]:
    # 延迟导入纯评测引擎；此路径不导入 composition / server 或模型工厂。
    from app.application.retrieval_evaluation import (
        compare_reports,
        validate_baseline_definition,
        validate_report,
        validate_standard,
    )

    warnings: list[str] = []
    standard = None
    baseline_definition = None
    frozen_definition = None
    try:
        standard = validate_standard(store.standard())
    except (EvaluationReadError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        warnings.append("检索评测标准缺失或格式无效")
    try:
        frozen_definition = validate_baseline_definition(store.baseline_definition())
        baseline_definition = _baseline_public(frozen_definition)
    except (EvaluationReadError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        warnings.append("冻结基线定义缺失或格式无效")
    raw_reports, read_warnings = store.reports()
    warnings.extend(read_warnings)
    reports: list[dict[str, Any]] = []
    validated: dict[str, dict[str, Any]] = {}
    if standard is not None:
        for report_id, raw in raw_reports:
            try:
                report = validate_report(raw, standard)
                validated[report_id] = report
                reports.append(_report_public(report_id, report))
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                warnings.append(f"报告 {report_id} 校验失败，已排除")
    if any(value is not None and value not in validated for value in (baseline_id, candidate_id)):
        raise HTTPException(status_code=404, detail="所选评测报告不存在或不可用")
    # 无显式选择时优先展示已有测量；正式待测报告仍保留在列表中。
    reports.sort(key=lambda item: (str(item.get("created_at", "")), item["id"]))
    baseline_options = [item for item in reversed(reports) if item.get("role") == "baseline"]
    measured_baselines = [item for item in baseline_options if item.get("status") == "completed"]
    default_baseline = next(iter(measured_baselines or baseline_options or reports), None)
    candidate_options = [item for item in reversed(reports)
                         if item.get("role") == "candidate" and item["id"] != baseline_id]
    measured_candidates = [item for item in candidate_options if item.get("status") == "completed"]
    default_candidate = next(iter(measured_candidates or candidate_options), None)
    selected_baseline = baseline_id or (default_baseline["id"] if default_baseline else None)
    selected_candidate = candidate_id or (default_candidate["id"] if default_candidate else None)

    def matching_pair(before_id: str, after_id: str) -> bool:
        before, after = validated[before_id], validated[after_id]
        if before["status"] != "completed" or after["status"] != "completed":
            return False
        try:
            comparable = compare_reports(before, after, standard)["comparable"]
            if before["scope"] == after["scope"] == "formal":
                comparable = comparable and not _frozen_baseline_mismatches(before, frozen_definition)
            return bool(comparable)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return False

    # 仅替用户补全未选的一侧。多轮报告按最新候选优先，不能混用旧数据/模型口径。
    if baseline_id is not None and candidate_id is None:
        selected_candidate = next((item["id"] for item in measured_candidates
                                   if matching_pair(baseline_id, item["id"])), selected_candidate)
    elif candidate_id is not None and baseline_id is None:
        selected_baseline = next((item["id"] for item in measured_baselines
                                  if matching_pair(item["id"], candidate_id)), selected_baseline)
    elif baseline_id is None and candidate_id is None:
        latest_pair = next(((before["id"], after["id"])
                            for after in measured_candidates for before in measured_baselines
                            if matching_pair(before["id"], after["id"])), None)
        if latest_pair is not None:
            selected_baseline, selected_candidate = latest_pair
    comparison: dict[str, Any] = {
        "status": "pending", "scope": None, "comparable": False, "metrics": [], "cases": [],
        "reasons": ["需要两份可比较的评测报告；冻结定义不代表已运行基线"],
    }
    if selected_baseline and selected_candidate and standard is not None:
        try:
            baseline, candidate = validated[selected_baseline], validated[selected_candidate]
            comparison = compare_reports(baseline, candidate, standard)
            if baseline["scope"] == candidate["scope"] == "formal":
                mismatches = _frozen_baseline_mismatches(baseline, frozen_definition)
                if mismatches:
                    comparison = {
                        "status": "not_comparable", "scope": "formal", "comparable": False,
                        "metrics": [], "cases": [], "reasons": mismatches,
                    }
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            warnings.append("所选报告无法安全比较")
    return {
        "standard": _standard_public(standard) if standard is not None else None,
        "baseline_definition": baseline_definition,
        "reports": reports,
        "selected_baseline_id": selected_baseline,
        "selected_candidate_id": selected_candidate,
        "comparison": comparison,
        "warnings": warnings,
    }


def _frozen_baseline_mismatches(
    baseline: dict[str, Any], definition: dict[str, Any] | None,
) -> list[str]:
    """正式收益必须相对当前冻结 B0；诊断对照可以使用独立检索链路。"""
    if definition is None:
        return ["缺少当前冻结 B0 定义，无法确认基线报告与当前冻结 B0 一致"]
    expected = definition["provenance"]
    actual = baseline["provenance"]
    fields = (
        "catalog_sha256", "dataset_sha256", "selection_sha256", "embedding_identity", "query_mode",
        "k", "split", "repetitions", "source_sha256",
    )
    changed = [field for field in fields
               if expected.get(field) is None or actual.get(field) is None or expected[field] != actual[field]]
    if changed:
        return [f"基线报告与当前冻结 B0 不一致：{', '.join(changed)}"]
    return []


def _report_public(report_id: str, report: dict[str, Any]) -> dict[str, Any]:
    result = {"id": report_id, "provenance": _provenance_public(report["provenance"]), "summary": report["summary"]}
    for key in ("label", "protocol", "status", "scope", "role", "created_at", "standard_id", "variant"):
        if isinstance(report.get(key), str):
            result[key] = report[key][:200]
    parameters = report.get("parameters_safe")
    parameters = parameters if isinstance(parameters, dict) else {}
    if parameters.get("latency_scope") == "replay_after_frozen_real_retrieval":
        result["latency_scope"] = "replay_after_frozen_real_retrieval"
    elif parameters.get("latency_scope") == "offline" or parameters.get("external_services") is False:
        result["latency_scope"] = "offline"
    else:
        result["latency_scope"] = "unknown"
    return result


def _baseline_public(definition: dict[str, Any]) -> dict[str, Any]:
    result = {key: definition[key][:200] for key in ("id", "protocol", "status", "created_at")
              if isinstance(definition.get(key), str)}
    result["provenance"] = _provenance_public(definition["provenance"])
    selection = definition["selection"]
    result["selection"] = {key: selection[key] for key in ("case_count", "repetitions")
                           if type(selection.get(key)) is int and selection[key] >= 0}
    if isinstance(selection.get("split"), str) and _safe_token(selection["split"]):
        result["selection"]["split"] = selection["split"]
    if isinstance(selection.get("case_ids"), list):
        result["selection"]["case_ids"] = [item for item in selection["case_ids"][:1024]
                                              if isinstance(item, str) and _safe_token(item)]
    files = definition.get("source_files", {})
    result["source_file_count"] = len(files) if isinstance(files, (dict, list)) else 0
    return result


def _provenance_public(provenance: dict[str, Any]) -> dict[str, Any]:
    result = {key: provenance[key] for key in (
        "catalog_sha256", "dataset_sha256", "selection_sha256", "source_sha256", "config_sha256",
        "standard_sha256", "evaluator_sha256",
    ) if isinstance(provenance.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", provenance[key])}
    result.update({key: provenance[key] for key in ("k", "repetitions", "catalog_count", "case_count")
                   if type(provenance.get(key)) is int and provenance[key] >= 0})
    result.update({key: provenance[key] for key in ("query_mode", "split")
                   if isinstance(provenance.get(key), str) and _safe_token(provenance[key])})
    # Embedding 身份用于比较和追踪即可，无需公开网关配置等原始身份信息。
    identity = provenance.get("embedding_identity")
    if isinstance(identity, str):
        result["embedding_identity"] = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return result


def _safe_token(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value))


def _standard_public(standard: dict[str, Any]) -> dict[str, Any]:
    """只投影标准定义字段；不把附加日志或未知配置经仪表盘透传。"""
    result = {key: standard[key] for key in ("id", "version", "title", "k", "description")
              if isinstance(standard.get(key), (str, int)) and not isinstance(standard[key], bool)}
    metrics = standard.get("metrics", [])
    result["metrics"] = [
        {key: metric[key][:3000] for key in ("key", "label", "definition", "aggregation", "unit")
         if isinstance(metric.get(key), str)}
        for metric in (metrics if isinstance(metrics, list) else []) if isinstance(metric, dict)
    ][:32]
    result["gates"] = {}
    gates = standard.get("gates", {})
    if isinstance(gates, dict):
        for key in ("ndcg_at_5", "recall_at_5", "returned_precision", "empty_accuracy", "hard_constraint_violations"):
            raw = gates.get(key, {})
            if not isinstance(raw, dict):
                continue
            gate = {field: raw[field] for field in ("min_delta", "max_count")
                    if type(raw.get(field)) in (int, float) and math.isfinite(raw[field])}
            if isinstance(raw.get("required_dimensions"), list):
                gate["required_dimensions"] = [value for value in raw["required_dimensions"]
                                                if value in ("budget", "material", "shipping")]
            result["gates"][key] = gate
    definition = standard.get("baseline_definition", {})
    if isinstance(definition, dict):
        result["baseline_definition"] = {key: definition[key][:3000] for key in (
            "catalog", "dataset", "query_mode", "split", "scope", "algorithm"
        ) if isinstance(definition.get(key), str)}
        if type(definition.get("repetitions")) is int:
            result["baseline_definition"]["repetitions"] = definition["repetitions"]
    for key in ("comparability", "warnings"):
        if isinstance(standard.get(key), list):
            result[key] = [value[:3000] for value in standard[key][:32] if isinstance(value, str)]
    return result


def build_preview_app() -> FastAPI:
    """仅用于本机仪表盘预览，不执行完整业务服务的生命周期。"""
    api = FastAPI(title="Globex 检索评测只读预览")
    api.state.retrieval_evaluation_preview = True
    register_retrieval_evaluation_routes(api)

    @api.get("/health")
    async def preview_health() -> dict[str, str]:
        return {"status": "ok", "mode": "retrieval-evaluation-preview", "business_services": "not_started"}

    return api
