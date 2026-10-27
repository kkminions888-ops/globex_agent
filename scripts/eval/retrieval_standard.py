"""冻结检索基线定义、运行无模型诊断并生成 V1 对照报告。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from app.application.retrieval_evaluation import PROTOCOL, aggregate_cases, compare_reports, evaluate_case, load_standard, standard_fingerprint
from app.application.usecases.catalog_search import CatalogSearchUseCase
from app.domain.catalog.product_search_spec import ProductSearchSpec
from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository
from app.infrastructure.persistence.seed_products import _product_from_record
from app.infrastructure.settings import load_settings
from scripts.eval.run_manifest import json_hash, public_endpoint

ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # x模式在最终写入时保护已有证据，避免先检查后覆盖的竞争窗口。
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _catalog_path() -> Path:
    packaged = ROOT / "catalog/catalog-v3.jsonl"
    return packaged if packaged.is_file() else ROOT / "data/catalog-v3.jsonl"


def _source_fingerprint() -> dict:
    names = ("app/application/usecases/catalog_search.py", "app/application/tools/product_search_tool.py",
             "app/domain/catalog/product.py", "app/domain/catalog/product_search_spec.py",
             "app/domain/catalog/product_attributes.py", "app/infrastructure/settings.py", "app/composition.py",
             "app/infrastructure/retrieval/bm25.py", "app/infrastructure/vector/qdrant_product_index.py",
             "app/infrastructure/embedding/openai_embedding_client.py", "app/infrastructure/rerank/http_reranker.py",
             "app/infrastructure/rerank/factory.py", "app/infrastructure/persistence/seed_products.py", "uv.lock")
    entries = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    return {"sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(), "files": entries}


def _evaluator_fingerprint() -> dict:
    names = ("app/application/retrieval_evaluation.py", "scripts/eval/retrieval_standard.py", "eval/retrieval/standard-v1.json")
    entries = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    return {"sha256": json_hash(entries), "files": entries}


def _safe_config() -> dict:
    settings = load_settings(require_llm_key=False)
    fields = ("embedding_model", "embedding_dim", "embedding_version", "hybrid_recall_enabled",
              "field_lexical_enabled", "field_lexical_weights",
              "hybrid_lexical_weight", "hybrid_vector_weight", "recall_candidates", "reranker_mode",
              "reranker_model", "reranker_protocol", "reranker_timeout_seconds", "qdrant_collection")
    safe = {field: getattr(settings, field) for field in fields}
    for name in ("embedding_base_url", "reranker_base_url", "qdrant_url"):
        safe[name] = public_endpoint(getattr(settings, name))
    return safe


def freeze_definition(*, dataset: Path, split: str) -> dict:
    catalog = _catalog_path()
    records, all_cases = _rows(catalog), _rows(dataset)
    selected = [case for case in all_cases if split == "all" or case.get("split") == split]
    if not selected or len({case["id"] for case in selected}) != len(selected):
        raise ValueError("选集为空或用例 ID 重复")
    config = _safe_config()
    source = _source_fingerprint()
    evaluator = _evaluator_fingerprint()
    now = datetime.now(timezone.utc).isoformat()
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_head = None
    return {
        "id": "current-baseline-definition", "protocol": PROTOCOL, "status": "frozen", "created_at": now,
        "standard_id": PROTOCOL, "frozen_at": now, "report_id": "current-baseline-pending", "git_head": git_head,
        "provenance": {
            "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "selection_sha256": json_hash(selected), "source_sha256": source["sha256"],
            "standard_sha256": standard_fingerprint(load_standard()), "evaluator_sha256": evaluator["sha256"],
            "config_sha256": json_hash(config),
            "embedding_identity": json_hash({key: config[key] for key in ("embedding_model", "embedding_dim", "embedding_version", "embedding_base_url")}),
            "query_mode": "structured_spec", "k": 5, "split": split, "repetitions": 1,
            "catalog_count": len(records), "case_count": len(selected), "sku_count": sum(len(row["skus"]) for row in records),
        },
        "config_safe": config, "source_files": source["files"], "evaluator": evaluator,
        "selection": {"case_ids": [case["id"] for case in selected], "split": split, "complete_split": True,
                      "dataset": dataset.relative_to(ROOT).as_posix(), "catalog": catalog.relative_to(ROOT).as_posix()},
        "note": "当前配置与输入已冻结，尚未执行当前向量主链；本文件不是测量报告或优化收益结论。",
    }


def not_run_report(definition: dict) -> dict:
    return {"id": "current-baseline-pending", "label": "当前配置正式基线（未运行）", "protocol": PROTOCOL,
            "created_at": definition["created_at"], "status": "not_run", "scope": "formal", "role": "baseline",
            "standard_id": PROTOCOL, "variant": "configured", "provenance": definition["provenance"],
            "summary": aggregate_cases([]), "cases": [], "note": definition["note"]}


async def run_diagnostic(args) -> dict:
    args.output.mkdir(parents=True, exist_ok=False)
    definition = freeze_definition(dataset=args.dataset, split=args.split)
    records, selected = _rows(_catalog_path()), _rows(args.dataset)
    selected = [case for case in selected if args.split == "all" or case.get("split") == args.split]
    products = [_product_from_record(row) for row in records]
    by_id = {product.product_id: product for product in products}
    usecase = CatalogSearchUseCase(InMemoryProductRepository(products), hybrid_enabled=args.variant == "bm25")
    config = {"variant": args.variant, "external_services": False, "k": 5}
    provenance = {**definition["provenance"], "embedding_identity": "none:lexical", "config_sha256": json_hash(config)}
    report = {"id": args.output.name, "label": args.label or ("关键词离线诊断" if args.variant == "keyword" else "BM25 离线诊断"),
              "protocol": PROTOCOL, "created_at": datetime.now(timezone.utc).isoformat(), "status": "completed",
              "scope": "diagnostic", "role": args.role, "standard_id": PROTOCOL, "variant": args.variant,
              "provenance": provenance, "summary": {}, "cases": [],
              "evaluator": _evaluator_fingerprint(),
              "parameters_safe": config,
              "note": "真实运行当前完整商品快照；不调用Embedding或模型，不代表当前向量主链，不作为正式收益验收。"}
    inputs = {"catalog": provenance["catalog_sha256"], "dataset": provenance["dataset_sha256"], "source": provenance["source_sha256"]}
    raw_rows = []
    for case in selected:
        spec_keys = ("category", "ship_to", "target_currency", "price_max_major", "required_material_tags", "excluded_material_tags", "product_id", "sku_id")
        spec = ProductSearchSpec(normalized_query=case["query"], top_k=5,
                                 **{key: case[key] for key in spec_keys if key in case})
        started = time.perf_counter()
        try:
            result = await usecase.execute(spec)
            row = evaluate_case(case, result["hits"], by_id)
            row["elapsed_ms"] = (time.perf_counter() - started) * 1000
            # 有意选择离线词法算法不是向量失败降级；实际链路与期望不符则标记降级。
            expected = "keyword_2gram" if args.variant == "keyword" else "bm25"
            row["degraded"] = result["recall_strategy"] not in {expected, "exact_id_lookup"}
            raw_rows.append({"id": case["id"], "retrieved_product_ids": [hit["product_id"] for hit in result["hits"]],
                             "recall_strategy": result["recall_strategy"]})
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = {"case_id": case["id"], "error_type": type(error).__name__}
            break
        report["cases"].append(row)
    report["summary"] = aggregate_cases(report["cases"])
    if (hashlib.sha256(_catalog_path().read_bytes()).hexdigest() != inputs["catalog"]
            or hashlib.sha256(args.dataset.read_bytes()).hexdigest() != inputs["dataset"]
            or _source_fingerprint()["sha256"] != inputs["source"]
            or _evaluator_fingerprint()["sha256"] != report["evaluator"]["sha256"]):
        report["status"] = "failed"
        report["failure"] = {"error_type": "InputsChangedDuringRun"}
    _write_new(args.output / "report.json", report)
    _write_new(args.output / "retrieved.json", {"cases": raw_rows})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    run = sub.add_parser("run")
    for command in (freeze, run):
        command.add_argument("--dataset", type=Path, default=ROOT / "eval/v2/product_retrieval.jsonl")
        command.add_argument("--split", choices=("all", "dev", "holdout"), default="holdout")
        command.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--not-run-output", type=Path, help="可选：写入未运行的正式基线报告（新文件）")
    run.add_argument("--variant", choices=("keyword", "bm25"), required=True)
    run.add_argument("--role", choices=("baseline", "candidate"), required=True)
    run.add_argument("--scope", choices=("diagnostic",), default="diagnostic")
    run.add_argument("--label")
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze_definition(dataset=args.dataset, split=args.split)
        _write_new(args.output, result)
        if args.not_run_output:
            _write_new(args.not_run_output, not_run_report(result))
    elif args.command == "run":
        result = asyncio.run(run_diagnostic(args))
    else:
        result = compare_reports(json.loads(args.baseline.read_text(encoding="utf-8")),
                                 json.loads(args.candidate.read_text(encoding="utf-8")), load_standard())
        _write_new(args.output, result)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
