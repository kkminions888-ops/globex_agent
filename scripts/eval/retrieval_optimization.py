"""在指定源码快照中运行检索优化诊断；不调用 Embedding、精排或聊天服务。

父进程只编排，子进程首先将 --source-root 放在 sys.path 首位，然后导入被测 app。
标准计分器单独从当前工程加载，因此代码前后使用同一计分口径。
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
from statistics import mean
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SCORER = ROOT / "app/application/retrieval_evaluation.py"
STANDARD = ROOT / "eval/retrieval/standard-v1.json"
SPEC_KEYS = (
    "category", "ship_to", "target_currency", "price_max_major",
    "required_material_tags", "excluded_material_tags", "product_id", "sku_id",
    "product_type", "max_weight_kg", "sku_spec", "preferred_product_type", "prefer_lightweight",
)
# 独立冻结的静态报价快照；不调用被测系统的换汇代码生成判据。
RATES_TO_CNY = {"CNY": 1.0, "USD": 7.10, "EUR": 7.80, "GBP": 9.10,
                "JPY": .048, "HKD": .91, "AUD": 4.70, "CAD": 5.20, "SGD": 5.30}


def json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def source_fingerprint(root: Path) -> dict:
    entries = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((root / "app").rglob("*"))
               if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    if not entries:
        raise ValueError("source-root 没有 app 源码")
    for name in ("uv.lock", "pyproject.toml"):
        if (root / name).is_file():
            entries[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    return {"sha256": json_hash(entries), "files": entries}


def evaluator_fingerprint() -> dict:
    names = (SCORER, Path(__file__).resolve(), STANDARD)
    entries = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in names}
    return {"sha256": json_hash(entries), "files": entries}


def verify_source_manifest(root: Path, path: Path | None, actual: dict) -> dict:
    if path is None:
        return {"provided": False, "verified": False}
    supplied = json.loads(path.read_text(encoding="utf-8"))
    expected = supplied.get("source_files", supplied.get("files", {}))
    expected = {name: digest for name, digest in expected.items()
                if name.startswith("app/") or name in {"uv.lock", "pyproject.toml"}}
    if not expected:
        raise ValueError("source-manifest 没有可核对的 app 源码指纹")
    differences = [name for name, digest in expected.items() if actual["files"].get(name) != digest]
    extras = set(actual["files"]) - set(expected)
    if differences or extras:
        raise ValueError("source-root 与来源清单不一致，拒绝冒充冻结基线")
    return {"provided": True, "verified": True,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "file_count": len(expected)}


def converted_major(sku: dict, target_currency: str) -> float:
    # 与原工程的最小单位四舍五入规则一致，常量本身独立冻结。
    source_minor = round(float(sku["price_major"]) * 100)
    return round(source_minor * RATES_TO_CNY[sku["currency"]] / RATES_TO_CNY[target_currency]) / 100


def additional_case_metrics(case: dict, hits: list[dict], records_by_id: dict[str, dict],
                            result: dict, unsupported: list[str]) -> dict:
    displayed = hits[:5]
    gold = set(case.get("relevant_canonical_ids", []))
    canonical = [records_by_id[hit["product_id"]].get("canonical_product_id") or hit["product_id"]
                 for hit in displayed]
    first = next((rank for rank, identity in enumerate(canonical, 1) if identity in gold), None)
    price_checks, sku_checks, selected_checks, type_checks, weight_checks = [], [], [], [], []
    for hit in displayed:
        product = records_by_id[hit["product_id"]]
        sku = next((item for item in product["skus"] if item["sku_id"] == hit.get("default_sku_id")), None)
        price_checks.append(sku is not None and sku["stock"] > 0
                            and hit.get("currency") == case.get("target_currency", "CNY")
                            and abs(float(hit.get("price_major", math.inf))
                                    - converted_major(sku, case.get("target_currency", "CNY"))) < .005
                            and hit.get("source_currency") == sku["currency"]
                            and abs(float(hit.get("source_price_major", math.inf)) - sku["price_major"]) < .005)
        if case.get("sku_spec"):
            sku_checks.append(sku is not None and case["sku_spec"].casefold() in sku["spec"].casefold())
        expected_sku = case.get("expected_default_sku_id") or case.get("sku_id")
        if expected_sku:
            selected_checks.append(hit.get("default_sku_id") == expected_sku)
        if case.get("product_type"):
            type_checks.append(product.get("product_type") == case["product_type"])
        if case.get("max_weight_kg") is not None:
            weight = product.get("weight_kg")
            weight_checks.append(isinstance(weight, (int, float)) and 0 < weight <= case["max_weight_kg"])
    trace = result.get("retrieval_stages", {})
    merged = trace.get("merged_candidates")
    pool = ({records_by_id[pid].get("canonical_product_id") or pid for pid in merged}
            if merged is not None else None)
    # 错误排除只统计明确以预算原因拒绝的独立金标，不把召回/排序损失冒充预算错误。
    false_excluded, budget_eligible = set(), set()
    if case.get("price_max_major") is not None:
        for pid in case.get("relevant", []):
            product = records_by_id[pid]
            eligible = [sku for sku in product["skus"] if sku["stock"] > 0
                        and (not case.get("sku_id") or sku["sku_id"] == case["sku_id"])
                        and (not case.get("sku_spec") or case["sku_spec"].casefold() in sku["spec"].casefold())
                        and converted_major(sku, case.get("target_currency", "CNY")) <= case["price_max_major"]]
            if eligible:
                budget_eligible.add(pid)
        false_excluded = {item["product_id"] for item in result.get("filtered_out", [])
                          if item.get("reason") == "over_price_cap" and item["product_id"] in budget_eligible}
    return {"id": case["id"], "kind": "empty" if case.get("expected_empty") else case.get("kind", "positive"), "family": case.get("family", "unspecified"),
            "mrr_at_5": None if case.get("expected_empty") else 1 / first if first else 0.0,
            "duplicate_count": len(canonical) - len(set(canonical)), "displayed_count": len(displayed),
            "display_price_checks": price_checks, "sku_spec_checks": sku_checks,
            "selected_sku_checks": selected_checks,
            "type_checks": type_checks, "weight_checks": weight_checks,
            "candidate_recall": len(pool & gold) / len(gold) if pool is not None and gold else None,
            "candidate_canonical_count": len(pool) if pool is not None else None,
            "contract_expectation_met": (not displayed if case.get("expected_empty")
                                         else bool(set(canonical) & gold) and all(selected_checks)
                                         and all(sku_checks) and all(type_checks) and all(weight_checks)),
            "budget_eligible_gold_product_count": len(budget_eligible),
            "budget_false_excluded_product_ids": sorted(false_excluded),
            "unsupported_constraints": unsupported,
            "retrieved": [{"product_id": hit["product_id"], "canonical_product_id": canonical[index],
                           "default_sku_id": hit.get("default_sku_id"), "price_major": hit.get("price_major"),
                           "currency": hit.get("currency"), "score": hit.get("score")}
                          for index, hit in enumerate(displayed)],
            "recall_strategy": result.get("recall_strategy"),
            "rerank_applied": result.get("rerank_applied", False), "retrieval_stages": trace}


def aggregate_additional(rows: list[dict]) -> dict:
    def average(values):
        return mean(values) if values else None
    def checks(name):
        values = [value for row in rows for value in row[name]]
        return {"checks": len(values), "violations": sum(not value for value in values), "satisfaction": average(values)}
    displayed = sum(row["displayed_count"] for row in rows)
    budget_gold = sum(row["budget_eligible_gold_product_count"] for row in rows)
    budget_errors = sum(len(row["budget_false_excluded_product_ids"]) for row in rows)
    return {"mrr_at_5": average([row["mrr_at_5"] for row in rows if row["mrr_at_5"] is not None]),
            "candidate_recall": average([row["candidate_recall"] for row in rows if row["candidate_recall"] is not None]),
            "candidate_trace_case_count": sum(row["candidate_canonical_count"] is not None for row in rows),
            "duplicate_rate": sum(row["duplicate_count"] for row in rows) / displayed if displayed else None,
            "display_price_consistency": checks("display_price_checks"), "sku_spec_constraint": checks("sku_spec_checks"),
            "selected_sku_identity": checks("selected_sku_checks"),
            "product_type_constraint": checks("type_checks"), "weight_constraint": checks("weight_checks"),
            "budget_false_exclusion_rate": budget_errors / budget_gold if budget_gold else None,
            "budget_eligible_gold_product_count": budget_gold, "budget_false_excluded_count": budget_errors,
            "unsupported_constraint_cases": sum(bool(row["unsupported_constraints"]) for row in rows),
            "contract_expectation_accuracy": average([row["contract_expectation_met"] for row in rows]),
            "rerank_calls": 0, "external_api_calls": 0,
            "call_cost": {"amount": 0, "currency": "CNY", "scope": "offline_no_external_api"}}


def load_scorer():
    module_spec = importlib.util.spec_from_file_location("_fixed_retrieval_scorer", SCORER)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def select_parameters(baseline: dict, candidates: list[dict], grid: dict) -> dict:
    """仅使用完整开发集选参；门槛先保护召回和精度，再比较 NDCG。"""
    scorer = load_scorer()
    baseline = scorer.validate_report(baseline, scorer.load_standard(STANDARD))
    if baseline["status"] != "completed" or baseline["provenance"]["split"] != "dev":
        raise ValueError("选参基线必须是完整 dev 报告")
    by_config = {json_hash({key: row[key] for key in ("field_weights", "recall_candidates")}): row for row in grid["configurations"]}
    rows, accepted = [], []
    for report in candidates:
        report = scorer.validate_report(report, scorer.load_standard(STANDARD))
        if report["status"] != "completed" or report["provenance"]["split"] != "dev":
            raise ValueError("候选选参报告只能来自完整 dev，不允许 holdout/all")
        for key in ("catalog_sha256", "dataset_sha256", "selection_sha256", "source_sha256", "evaluator_sha256", "standard_sha256"):
            if report["provenance"][key] != baseline["provenance"][key]:
                raise ValueError("开发集选参报告条件不一致")
        if report["parameters_safe"]["mode"] != "field_hybrid":
            raise ValueError("开发集候选不是字段词法配置")
        config = {key: report["parameters_safe"][key] for key in ("field_weights", "recall_candidates")}
        item = by_config.get(json_hash(config))
        if item is None:
            raise ValueError("配置不在预先冻结网格中")
        summary, base = report["summary"], baseline["summary"]
        ok = (summary["recall_at_5"] >= base["recall_at_5"] - .01 - 1e-12
              and summary["returned_precision"] >= base["returned_precision"] - 1e-12
              and not any(row["error"] or row["degraded"] for row in report["cases"]))
        row = {"id": item["id"], "report_id": report["id"], "eligible": ok,
               "ndcg_at_5": summary["ndcg_at_5"], "recall_at_5": summary["recall_at_5"],
               "returned_precision": summary["returned_precision"], "parameters": config}
        rows.append(row)
        if ok:
            accepted.append(row)
    if len(rows) != len(grid["configurations"]) or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("必须完整测量预先冻结网格，不能只挑好结果选参")
    ranked = sorted(accepted, key=lambda row: (-row["ndcg_at_5"], -row["returned_precision"],
                    -row["recall_at_5"], row["parameters"]["recall_candidates"], row["id"]))
    return {"id": "optimization-parameters-locked-v1", "status": "selected" if ranked else "rejected",
            "selection_split": "dev", "baseline_report_id": baseline["id"], "grid_sha256": json_hash(grid),
            "selection_provenance": baseline["provenance"], "selection_rows": rows,
            "selected_configuration": ranked[0]["id"] if ranked else None,
            **(ranked[0]["parameters"] if ranked else {}),
            "note": "仅从冻结开发集网格按预设顺序选参；不是全局最优，不证明Dense/Hybrid或生产效果。"}


async def run_child(args) -> dict:
    root = args.source_root.resolve()
    if args.role == "baseline" and args.source_manifest is None:
        raise ValueError("基线必须提供冻结 source-manifest，不能从可变工程伪造改进前结果")
    sys.path.insert(0, str(root))
    # 此处之前不导入任何 app，避免父进程模块缓存混淆新旧实现。
    from app.application.usecases.catalog_search import CatalogSearchUseCase
    from app.domain.catalog.product_search_spec import ProductSearchSpec
    from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository
    from app.infrastructure.persistence.seed_products import _product_from_record

    imported = Path(inspect.getfile(CatalogSearchUseCase)).resolve()
    if not imported.is_relative_to(root / "app"):
        raise ValueError("实际被测 app 未来自 source-root")
    scorer = load_scorer()
    source = source_fingerprint(root)
    manifest = verify_source_manifest(root, args.source_manifest, source)
    scorer_fp = evaluator_fingerprint()
    catalog = args.catalog or root / "data/catalog-v3.jsonl"
    dataset = args.dataset or root / "eval/v2/product_retrieval.jsonl"
    records, cases = read_rows(catalog), read_rows(dataset)
    selected = [case for case in cases if args.split == "all" or case.get("split") == args.split]
    if not selected or len({case["id"] for case in selected}) != len(selected):
        raise ValueError("选集为空或用例 ID 重复")
    params = json.loads(args.parameters.read_text(encoding="utf-8")) if args.parameters else {}
    kwargs = {"hybrid_enabled": args.mode != "keyword", "capture_retrieval_stages": True,
              "recall_candidates": params.get("recall_candidates", 32)}
    if args.mode == "field_hybrid":
        kwargs["field_lexical_enabled"] = True
        if params.get("field_weights") is not None:
            kwargs["field_weights"] = params["field_weights"]
    for name in ("min_relevance_score", "relevance_score_kind"):
        if params.get(name) is not None:
            kwargs[name] = params[name]
    usecase = CatalogSearchUseCase(InMemoryProductRepository([_product_from_record(row) for row in records]), **kwargs)
    products = await usecase._product_repo.list_all()
    by_id, records_by_id = {p.product_id: p for p in products}, {r["product_id"]: r for r in records}
    config = {"mode": args.mode, **kwargs, "external_services": False, "parameters_sha256": json_hash(params)}
    provenance = {"catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                  "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                  "selection_sha256": json_hash(selected), "source_sha256": source["sha256"],
                  "standard_sha256": scorer.standard_fingerprint(scorer.load_standard(STANDARD)),
                  "evaluator_sha256": scorer_fp["sha256"], "config_sha256": json_hash(config),
                  "embedding_identity": "none:lexical", "query_mode": "structured_spec", "k": 5,
                  "split": args.split, "repetitions": 1, "catalog_count": len(records),
                  "sku_count": sum(len(r["skus"]) for r in records), "case_count": len(selected)}
    report = {"id": args.output.name, "label": args.label or f"{args.mode} {args.split} 离线诊断",
              "protocol": scorer.PROTOCOL, "standard_id": scorer.PROTOCOL,
              "created_at": datetime.now(timezone.utc).isoformat(), "status": "completed",
              "scope": "diagnostic", "role": args.role, "variant": args.mode,
              "provenance": provenance, "parameters_safe": config, "source_manifest": manifest,
              "source_files": source["files"], "evaluator": scorer_fp,
              "cases": [], "summary": {},
              "note": "真实执行指定源码；纯词法，无向量替身。合成契约与目录v2均只作诊断，不证明Dense/精排或生产效果。"}
    extras = []
    accepted = inspect.signature(ProductSearchSpec).parameters
    for case in selected:
        unsupported = [key for key in SPEC_KEYS if key in case and key not in accepted]
        spec = ProductSearchSpec(normalized_query=case["query"], top_k=5,
                                 **{key: case[key] for key in SPEC_KEYS if key in case and key in accepted})
        started = time.perf_counter()
        try:
            result = await usecase.execute(spec)
            row = scorer.evaluate_case(case, result["hits"], by_id)
            row["elapsed_ms"] = (time.perf_counter() - started) * 1000
            row["degraded"] = result.get("vector_available") is True or result.get("rerank_applied", False)
            report["cases"].append(row)
            extras.append(additional_case_metrics(case, result["hits"], records_by_id, result, unsupported))
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = {"case_id": case["id"], "error_type": type(error).__name__}
            break
    report["summary"] = scorer.aggregate_cases(report["cases"])
    if (source_fingerprint(root)["sha256"] != source["sha256"]
            or evaluator_fingerprint()["sha256"] != scorer_fp["sha256"]
            or hashlib.sha256(catalog.read_bytes()).hexdigest() != provenance["catalog_sha256"]
            or hashlib.sha256(dataset.read_bytes()).hexdigest() != provenance["dataset_sha256"]):
        report["status"] = "failed"
        report["failure"] = {"error_type": "InputsChangedDuringRun"}
    args.output.mkdir(parents=True, exist_ok=False)
    write_new(args.output / "report.json", report)
    write_new(args.output / "additional-diagnostics.json", {
        "report_id": report["id"], "provenance": provenance, "scope": "diagnostic",
        "label_source": "synthetic_contract" if any(c.get("provenance") == "synthetic_contract_v1" for c in selected) else "frozen_v2",
        "summary": aggregate_additional(extras), "cases": extras,
        "slices": {kind: {"quality": scorer.aggregate_cases([row for row in report["cases"] if row["kind"] == kind]),
                          "additional": aggregate_additional([row for row in extras if row["kind"] == kind])}
                   for kind in sorted({row["kind"] for row in extras})},
        "limitations": ["价格一致率只核对展示默认SKU；不证明付款或实时汇率。",
                        "预算错误排除率只统计明确over_price_cap拒绝；未召回不归因预算。",
                        "无向量服务时field_hybrid为字段词法诊断，不能声称Hybrid/Dense收益。"]})
    write_new(args.output / "execution.json", {"source_root": str(root), "catalog": str(catalog),
              "dataset": str(dataset), "imported_usecase": str(imported), "manifest": manifest,
              "python": sys.executable, "source_sha256": source["sha256"], "external_services": False})
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    child = sub.add_parser("_child", help=argparse.SUPPRESS)
    for item in (run, child):
        item.add_argument("--source-root", type=Path, required=True)
        item.add_argument("--source-manifest", type=Path)
        item.add_argument("--catalog", type=Path)
        item.add_argument("--dataset", type=Path)
        item.add_argument("--parameters", type=Path)
        item.add_argument("--mode", choices=("keyword", "bm25", "field_hybrid"), required=True)
        item.add_argument("--split", choices=("dev", "holdout", "all"), default="holdout")
        item.add_argument("--role", choices=("baseline", "candidate"), required=True)
        item.add_argument("--label")
        item.add_argument("--output", type=Path, required=True)
    select = sub.add_parser("select-parameters", help="仅使用冻结开发集选择参数并锁定")
    select.add_argument("--baseline", type=Path, required=True)
    select.add_argument("--candidate", type=Path, action="append", required=True)
    select.add_argument("--grid", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    return parser


def main():
    args = build_parser().parse_args()
    if args.command == "select-parameters":
        selected = select_parameters(json.loads(args.baseline.read_text(encoding="utf-8")),
                   [json.loads(path.read_text(encoding="utf-8")) for path in args.candidate],
                   json.loads(args.grid.read_text(encoding="utf-8")))
        write_new(args.output, selected)
        print(json.dumps({"status": selected["status"], "selected_configuration": selected["selected_configuration"]}))
        return
    if args.command == "run":
        forwarded = []
        for name in ("source_root", "source_manifest", "catalog", "dataset", "parameters", "mode", "split", "role", "label", "output"):
            value = getattr(args, name)
            if value is not None:
                forwarded.extend(("--" + name.replace("_", "-"), str(value.resolve()) if isinstance(value, Path) else value))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_child", *forwarded],
                                cwd=args.source_root.resolve(), check=False)
        raise SystemExit(result.returncode)
    report = asyncio.run(run_child(args))
    print(json.dumps({"status": report["status"], "output": str(args.output), "summary": report["summary"]}, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
