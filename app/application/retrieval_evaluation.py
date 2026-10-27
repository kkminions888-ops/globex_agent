"""商品检索标准 V1：独立的离线计分与验收，不改变商品检索策略。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean
from typing import Any, Mapping, Sequence

PROTOCOL = "retrieval-standard-v1"
ROOT = Path(__file__).resolve().parents[2]
DIMENSIONS = ("budget", "material", "shipping")
QUALITY_KEYS = ("ndcg_at_5", "recall_at_5", "returned_precision")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def load_standard(path: str | Path | None = None) -> dict:
    value = json.loads(Path(path or ROOT / "eval/retrieval/standard-v1.json").read_text(encoding="utf-8"))
    return validate_standard(value)


def validate_standard(value: dict) -> dict:
    if value.get("id") != PROTOCOL or value.get("k") != 5:
        raise ValueError("检索标准版本或 K 无效")
    return value


def standard_fingerprint(standard: dict) -> str:
    """同一标准 ID 的内容变更也必须产生新证据，防止旧成绩套新阈值。"""
    return hashlib.sha256(json.dumps(standard, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_baseline_definition(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_baseline_definition(value)


def validate_baseline_definition(value: dict) -> dict:
    if value.get("protocol") != PROTOCOL or value.get("status") != "frozen":
        raise ValueError("不是有效的冻结基线定义")
    if not isinstance(value.get("provenance"), dict) or not isinstance(value.get("selection"), dict):
        raise ValueError("冻结基线缺少输入指纹与选集")
    return value


def _number(value: Any, name: str, *, ratio: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} 缺失或不是有限数字")
    if value < 0 or (ratio and value > 1):
        raise ValueError(f"{name} 超出合法范围")
    return float(value)


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} 缺失或不是非负整数")
    return value


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def evaluate_case(case: dict, hits: Sequence[dict], products_by_id: Mapping[str, Any], k: int = 5) -> dict:
    """先截实际展示列表再计分；同款重复不加分、不补位。"""
    if k != 5:
        raise ValueError("V1 固定实际前5条")
    displayed = list(hits[:k])
    gold = set(case.get("relevant_canonical_ids", []))
    expected_empty = case.get("expected_empty", False)
    if type(expected_empty) is not bool or (expected_empty and gold) or (not expected_empty and not gold):
        raise ValueError("正例必须有 canonical 金标，无结果例必须无金标")
    applicable = {
        "budget": case.get("price_max_major") is not None,
        "material": bool(case.get("excluded_material_tags") or case.get("required_material_tags")),
        "shipping": bool(case.get("ship_to")),
    }
    violations = dict.fromkeys(DIMENSIONS, 0)
    seen: set[str] = set()
    gains: list[int] = []
    for hit in displayed:
        product = products_by_id.get(hit.get("product_id"))
        if product is None:
            raise ValueError("展示商品不存在于冻结目录")
        canonical = _field(product, "canonical_product_id") or _field(product, "product_id")
        gains.append(int(canonical in gold and canonical not in seen))
        seen.add(canonical)
        # 预算核对实际展示价格及币种，不使用不同规格的最低价代替。
        if applicable["budget"]:
            price = _number(hit.get("price_major"), "展示价格")
            if hit.get("currency") != case.get("target_currency", "CNY") or price > case["price_max_major"]:
                violations["budget"] += 1
        materials = set(_field(product, "material_tags", []))
        if applicable["material"] and (materials & set(case.get("excluded_material_tags", []))
                or set(case.get("required_material_tags", [])) - materials):
            violations["material"] += 1
        if applicable["shipping"] and case["ship_to"] not in _field(product, "ships_to", []):
            violations["shipping"] += 1
    dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
    idcg = sum(1 / math.log2(rank + 2) for rank in range(min(k, len(gold))))
    return {
        "id": str(case["id"]), "family": case.get("family", "unspecified"),
        "kind": "empty" if expected_empty else case.get("kind", "positive"),
        "ndcg_at_5": None if expected_empty else dcg / idcg,
        "recall_at_5": None if expected_empty else sum(gains) / len(gold),
        "returned_precision": None if expected_empty else sum(gains) / len(displayed) if displayed else 0.0,
        "empty_ok": not displayed if expected_empty else None,
        "violations": violations, "applicable_constraints": applicable,
        "returned_count": len(displayed), "elapsed_ms": 0.0, "error": None, "degraded": False,
    }


def aggregate_cases(cases: Sequence[dict]) -> dict:
    """只重算逐例证据，不信任报告携带的汇总。缺失不得补0。"""
    positives: list[dict] = []
    empties: list[dict] = []
    violations = dict.fromkeys(DIMENSIONS, 0)
    coverage = dict.fromkeys(DIMENSIONS, 0)
    positive_coverage = dict.fromkeys(DIMENSIONS, 0)
    failures = dict.fromkeys(DIMENSIONS, 0)
    elapsed: list[float] = []
    identifiers: set[str] = set()
    for case in cases:
        identifier = case.get("id")
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier) or identifier in identifiers:
            raise ValueError("用例 ID 缺失或重复")
        identifiers.add(identifier)
        if not isinstance(case.get("kind"), str) or not SAFE_ID.fullmatch(case["kind"]):
            raise ValueError("用例类型无效")
        if not isinstance(case.get("family"), (str, int)) or isinstance(case.get("family"), bool):
            raise ValueError("用例分组无效")
        if isinstance(case["family"], str) and not SAFE_ID.fullmatch(case["family"]):
            raise ValueError("用例分组无效")
        if case.get("kind") == "empty":
            if type(case.get("empty_ok")) is not bool:
                raise ValueError(f"{identifier} 无结果证据缺失")
            if any(case.get(key) is not None for key in QUALITY_KEYS):
                raise ValueError("无结果例不能计入正例相关性指标")
            empties.append(case)
        else:
            for key in QUALITY_KEYS:
                _number(case.get(key), key, ratio=True)
            if case.get("empty_ok") is not None:
                raise ValueError("正例不能冒充无结果用例")
            positives.append(case)
        count = _count(case.get("returned_count"), "returned_count")
        if count > 5:
            raise ValueError("实际展示计分最多5条")
        if not count and case.get("kind") != "empty" and any(case[key] != 0 for key in QUALITY_KEYS):
            raise ValueError("正例无展示结果时相关性指标必须为0")
        if case.get("kind") == "empty" and case["empty_ok"] != (count == 0):
            raise ValueError("无结果判断与实际展示条数矛盾")
        for dimension in DIMENSIONS:
            applicable = case.get("applicable_constraints", {}).get(dimension)
            if type(applicable) is not bool:
                raise ValueError(f"{dimension} 适用性证据缺失")
            value = _count(case.get("violations", {}).get(dimension), dimension)
            if value > count or (value and not applicable):
                raise ValueError("违规计数与展示/适用约束矛盾")
            coverage[dimension] += int(applicable)
            positive_coverage[dimension] += int(applicable and case.get("kind") != "empty")
            failures[dimension] += int(value > 0)
            violations[dimension] += value
        elapsed.append(_number(case.get("elapsed_ms"), "elapsed_ms"))
        if "error" not in case or type(case.get("degraded")) is not bool:
            raise ValueError("缺少错误或降级状态")
    denominator = sum(coverage.values())
    return {
        **{key: mean(case[key] for case in positives) if positives else None for key in QUALITY_KEYS},
        "empty_accuracy": mean(case["empty_ok"] for case in empties) if empties else None,
        "hard_constraint_violations": violations,
        "constraint_case_counts": coverage, "hard_constraint_case_failures": failures,
        "constraint_positive_case_counts": positive_coverage,
        "hard_constraint_violation_rate": sum(failures.values()) / denominator if denominator else None,
        "positive_cases": len(positives), "empty_cases": len(empties),
        "p95_ms": sorted(elapsed)[math.ceil(len(elapsed) * .95) - 1] if elapsed else None,
    }


def validate_report(raw: dict, standard: dict | None = None) -> dict:
    standard = standard or load_standard()
    if not isinstance(raw, dict) or raw.get("protocol") != PROTOCOL or raw.get("standard_id") != standard["id"]:
        raise ValueError("报告协议或标准版本不匹配")
    if raw.get("status") not in {"completed", "failed", "not_run"} or raw.get("scope") not in {"formal", "diagnostic"}:
        raise ValueError("报告运行状态或范围无效")
    if raw.get("role") not in {"baseline", "candidate"}:
        raise ValueError("报告角色无效")
    if not isinstance(raw.get("provenance"), dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("报告缺少来源或逐例证据")
    for name in ("embedding_identity", "query_mode", "split"):
        value = raw["provenance"].get(name)
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ValueError(f"{name} 无效")
    result = deepcopy(raw)
    result["summary"] = aggregate_cases(result["cases"])
    expected = result["provenance"].get("case_count")
    if result["status"] == "completed" and (not result["cases"] or expected != len(result["cases"])):
        raise ValueError("已完成报告与冻结用例数量不一致")
    return result


def compare_reports(baseline: dict, candidate: dict, standard: dict | None = None) -> dict:
    """验证可比性后执行冻结门禁；诊断成绩永不标记验收通过。"""
    standard = standard or load_standard()
    result = {"status": "pending", "reasons": [], "metrics": [], "cases": [], "comparable": False}
    if baseline.get("status") == "not_run" or candidate.get("status") == "not_run":
        result["reasons"] = ["基线或候选尚未运行，不能计算优化收益"]
        return result
    try:
        baseline, candidate = validate_report(baseline, standard), validate_report(candidate, standard)
    except (ValueError, KeyError, TypeError) as error:
        result.update(status="not_comparable", reasons=[f"报告证据不完整：{error}"])
        return result
    problems = []
    if baseline["role"] != "baseline" or candidate["role"] != "candidate":
        problems.append("比较必须分别选择基线和候选报告")
    bp, cp = baseline["provenance"], candidate["provenance"]
    for name in ("catalog_sha256", "dataset_sha256", "selection_sha256", "source_sha256", "config_sha256",
                 "standard_sha256", "evaluator_sha256"):
        if any(not isinstance(p.get(name), str) or not re.fullmatch(r"[a-f0-9]{64}", p[name]) for p in (bp, cp)):
            problems.append(f"缺少有效 {name}")
    for name in ("catalog_sha256", "dataset_sha256", "selection_sha256", "standard_sha256", "evaluator_sha256",
                 "embedding_identity", "query_mode", "k", "split", "repetitions", "catalog_count", "case_count"):
        if bp.get(name) is None or cp.get(name) is None or bp.get(name) != cp.get(name):
            problems.append(f"对照条件 {name} 不一致或缺失")
    if any(p.get("standard_sha256") != standard_fingerprint(standard) for p in (bp, cp)):
        problems.append("标准内容指纹与当前冻结标准不符，不能沿用相同 ID 冒充同一标准")
    if bp.get("k") != 5 or type(bp.get("repetitions")) is not int or bp.get("repetitions", 0) < 1:
        problems.append("K 或重复次数不符合 V1")
    bcases, ccases = {row["id"]: row for row in baseline["cases"]}, {row["id"]: row for row in candidate["cases"]}
    if set(bcases) != set(ccases):
        problems.append("逐例 ID 不一致，不能用不同选集比较")
    elif any((bcases[key]["kind"], bcases[key].get("family"), bcases[key]["applicable_constraints"]) !=
             (ccases[key]["kind"], ccases[key].get("family"), ccases[key]["applicable_constraints"]) for key in bcases):
        problems.append("逐例类型、分组或约束适用性不一致")
    if problems:
        result.update(status="not_comparable", reasons=problems)
        return result
    result["comparable"] = True
    reasons = []
    if any(report["scope"] == "formal" and report["provenance"]["split"] != "holdout"
           for report in (baseline, candidate)):
        reasons.append("正式验收必须使用冻结 holdout 留出集；all/dev 仅用于开发或诊断")
    labels = {metric["key"]: metric["label"] for metric in standard["metrics"]}
    for key in (*QUALITY_KEYS, "empty_accuracy"):
        before, after = baseline["summary"][key], candidate["summary"][key]
        threshold = standard["gates"][key]["min_delta"]
        delta = after - before if before is not None and after is not None else None
        passed = delta is not None and delta >= threshold - 1e-12
        result["metrics"].append({"key": key, "label": labels[key], "baseline": before, "candidate": after,
                                  "delta": delta, "threshold": threshold, "passed": passed, "unit": "ratio"})
        if not passed:
            reasons.append(f"{labels[key]} 缺少覆盖或增量未达到 {threshold:+.2f}")
    for dimension in DIMENSIONS:
        value = candidate["summary"]["hard_constraint_violations"][dimension]
        if value or any(report["summary"]["constraint_case_counts"][dimension] == 0
                        or report["summary"]["constraint_positive_case_counts"][dimension] == 0
                        for report in (baseline, candidate)):
            reasons.append(f"{ {'budget':'预算', 'material':'材质', 'shipping':'配送'}[dimension]}约束存在违规或缺少覆盖")
    for key in ("hard_constraint_violation_rate", "p95_ms"):
        before, after = baseline["summary"][key], candidate["summary"][key]
        result["metrics"].append({"key": key, "label": labels[key], "baseline": before, "candidate": after,
                                  "delta": after - before if before is not None and after is not None else None,
                                  "threshold": 0 if key == "hard_constraint_violation_rate" else None,
                                  "passed": (after == 0 and all(candidate["summary"]["constraint_positive_case_counts"][d] > 0
                                                              for d in DIMENSIONS)) if key == "hard_constraint_violation_rate" else None,
                                  "unit": "ratio" if key == "hard_constraint_violation_rate" else "ms"})
    for identifier in bcases:
        before, after = bcases[identifier], ccases[identifier]
        result["cases"].append({"id": identifier, "kind": before["kind"], "baseline_ndcg": before["ndcg_at_5"],
                                "candidate_ndcg": after["ndcg_at_5"],
                                "delta": after["ndcg_at_5"] - before["ndcg_at_5"] if before["ndcg_at_5"] is not None else None,
                                "violation_count": sum(after["violations"].values())})
    if any(report["status"] != "completed" or any(row["error"] or row["degraded"] for row in report["cases"])
           for report in (baseline, candidate)):
        reasons.append("报告未完整完成，或存在错误/显式降级，不能验收")
    if any(report["scope"] != "formal" for report in (baseline, candidate)):
        result["status"] = "diagnostic"
        reasons.insert(0, "当前为诊断范围；离线对照不代表正式基线或收益验收")
    else:
        result["status"] = "blocked" if reasons else "passed"
    result["reasons"] = reasons
    return result
