"""绑定真实 Embedding 身份，在开发集校准 cosine 阈值并执行一次留出集消融。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS = (0.0, .1, .2, .3, .4, .5, .6, .7)
PROTECTED = {"ndcg_min_delta": 0.0, "precision_min_delta": 0.0,
             "empty_accuracy_min_delta": 0.0, "recall_min_delta": -.01}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grid_definition() -> dict:
    return {"id": "embedding-cosine-threshold-grid-v1", "thresholds": list(THRESHOLDS),
            "score_kind": "embedding", "hybrid_enabled": False, "reranker_enabled": False,
            "selection_split": "dev", "protected_metrics": PROTECTED,
            "selection_order": ["precision_desc", "ndcg_desc", "recall_desc", "threshold_asc"],
            "note": "同源码真实候选参数消融；不作为改进前后算法收益，不修改默认配置。"}


def guard_score_configuration(score_kind: str, hybrid_enabled: bool, reranker_enabled: bool) -> None:
    if score_kind != "embedding" or hybrid_enabled or reranker_enabled:
        raise ValueError("cosine 阈值只能用于同模型 Embedding 分数，禁止 RRF/精排混用")


def validate_cache(cache: dict, cases: list[dict], records: list[dict], *,
                   expected_identity: str, catalog_sha256: str, dataset_sha256: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_identity):
        raise ValueError("Embedding 模型身份指纹无效")
    if cache.get("embedding_identity") != expected_identity:
        raise ValueError("缓存 Embedding 模型身份不匹配")
    if (cache.get("status") != "completed" or cache.get("index_complete") is not True
            or cache.get("original_index_unchanged") is not True
            or cache.get("catalog_sha256") != catalog_sha256
            or cache.get("dataset_sha256") != dataset_sha256 or cache.get("rerank_calls") != 0):
        raise ValueError("真实候选缓存状态、输入或评分来源无效")
    queries = cache.get("queries", [])
    by_case = {row["case_id"]: row for row in queries}
    expected_ids = {row["product_id"] for row in records}
    if len(expected_ids) != len(records) or len(by_case) != len(queries) or set(by_case) != {row["id"] for row in cases}:
        raise ValueError("缓存查询或目录存在缺失/重复")
    dimension = None
    for case in cases:
        item = by_case[case["id"]]
        vector, hits = item.get("vector", []), item.get("hits", [])
        if (item.get("query_sha256") != hashlib.sha256(case["query"].encode()).hexdigest()
                or not vector or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
                or not any(vector)):
            raise ValueError("缓存查询向量不存在或与真实查询不匹配")
        dimension = dimension or len(vector)
        if len(vector) != dimension:
            raise ValueError("缓存查询向量维度不一致")
        if len(hits) != len(expected_ids) or {hit["product_id"] for hit in hits} != expected_ids:
            raise ValueError("缓存有未知、缺失或重复商品候选")
        scores = [hit.get("score") for hit in hits]
        if (any(type(value) not in (int, float) or not math.isfinite(value) or not -1.000001 <= value <= 1.000001 for value in scores)
                or scores != sorted(scores, reverse=True)):
            raise ValueError("候选不是有序且有限的 cosine 分数")
    return by_case


def validate_lock(lock: dict, *, embedding_identity: str, score_kind: str = "embedding") -> None:
    guard_score_configuration(score_kind, False, False)
    if lock.get("embedding_identity") != embedding_identity or lock.get("score_kind") != score_kind:
        raise ValueError("锁定阈值的模型身份或评分类型不匹配")
    if lock.get("threshold") not in THRESHOLDS or lock.get("selection_split") != "dev":
        raise ValueError("阈值未由冻结开发网格选出")
    if lock.get("protected_metrics") != PROTECTED:
        raise ValueError("锁参文件不能放宽保护指标")


def protection_result(baseline: dict, candidate: dict) -> dict:
    if baseline["provenance"]["split"] != candidate["provenance"]["split"]:
        raise ValueError("保护指标不能跨选集比较")
    keys = {"ndcg_at_5": 0, "returned_precision": 0, "empty_accuracy": 0, "recall_at_5": -.01}
    deltas = {key: candidate["summary"][key] - baseline["summary"][key]
              if candidate["summary"].get(key) is not None and baseline["summary"].get(key) is not None else None
              for key in keys}
    complete = (candidate.get("status") == "completed" and baseline.get("status") == "completed"
                and all(not row["error"] and not row["degraded"] for report in (baseline, candidate) for row in report["cases"]))
    passed = complete and all(deltas[key] is not None and deltas[key] >= floor - 1e-12 for key, floor in keys.items())
    return {"passed": passed, "complete_without_errors_or_degradation": complete, "deltas": deltas,
            "protected_metrics": PROTECTED}


def choose_threshold(baseline: dict, candidates: list[dict], grid: dict) -> dict:
    if grid != grid_definition():
        raise ValueError("冻结网格或保护指标被更改")
    if baseline["provenance"]["split"] != "dev" or len(candidates) != len(THRESHOLDS):
        raise ValueError("只能使用完整 dev 网格选择阈值")
    rows = []
    for report in candidates:
        if report["provenance"]["split"] != "dev":
            raise ValueError("禁止使用 holdout/all 选择阈值")
        for key in ("catalog_sha256", "dataset_sha256", "selection_sha256", "source_sha256", "embedding_identity", "evaluator_sha256"):
            if baseline["provenance"][key] != report["provenance"][key]:
                raise ValueError("阈值开发对照条件不一致")
        threshold = report["parameters_safe"]["min_relevance_score"]
        guard_score_configuration(report["parameters_safe"]["relevance_score_kind"],
                                  report["parameters_safe"]["hybrid_enabled"], report["parameters_safe"]["reranker_enabled"])
        rows.append({"threshold": threshold, "report_id": report["id"], "summary": report["summary"],
                     **protection_result(baseline, report)})
    if {row["threshold"] for row in rows} != set(THRESHOLDS):
        raise ValueError("阈值网格缺失、重复或包含未知阈值")
    accepted = [row for row in rows if row["passed"]]
    ranked = sorted(accepted, key=lambda row: (-row["summary"]["returned_precision"],
            -row["summary"]["ndcg_at_5"], -row["summary"]["recall_at_5"], row["threshold"]))
    return {"status": "selected" if ranked else "rejected", "threshold": ranked[0]["threshold"] if ranked else None,
            "selection_split": "dev", "embedding_identity": baseline["provenance"]["embedding_identity"],
            "score_kind": "embedding", "protected_metrics": PROTECTED, "selection_rows": rows}


@contextmanager
def deny_network():
    original_connect, original_connect_ex, original_create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def forbidden(*args, **kwargs):
        raise RuntimeError("阈值回放禁止任何网络连接")
    socket.socket.connect, socket.socket.connect_ex, socket.create_connection = forbidden, forbidden, forbidden
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = original_connect, original_connect_ex, original_create


async def calibrate(args) -> dict:
    sys.path.insert(0, str(ROOT))
    from scripts.eval.retrieval_optimization import (SPEC_KEYS, json_hash, load_scorer,
        read_rows, source_fingerprint, verify_source_manifest, write_new)
    root = args.source_root.resolve()
    sys.path.insert(0, str(root))
    from app.application.usecases.catalog_search import CatalogSearchUseCase
    from app.domain.catalog.product_search_spec import ProductSearchSpec
    from app.domain.catalog.ports.retrieval_ports import VectorHit
    from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository
    from app.infrastructure.persistence.seed_products import _product_from_record

    if not Path(inspect.getfile(CatalogSearchUseCase)).resolve().is_relative_to(root / "app"):
        raise ValueError("实际检索代码未来自冻结 source-root")
    args.output.mkdir(parents=True, exist_ok=False)
    grid = grid_definition()
    write_new(args.output / "grid.json", grid)  # 先落盘冻结网格，再执行任何测量。
    source = source_fingerprint(root)
    verify_source_manifest(root, args.source_manifest, source)
    inspection = json.loads(args.inspection.read_text(encoding="utf-8"))
    if inspection.get("status") != "complete" or inspection.get("original_index_unchanged") is not True:
        raise ValueError("真实索引检查证据无效")
    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    catalog, dataset = root / "data/catalog-v3.jsonl", root / "eval/v2/product_retrieval.jsonl"
    records, cases = read_rows(catalog), read_rows(dataset)
    by_case = validate_cache(cache, cases, records, expected_identity=inspection["embedding_identity"],
                             catalog_sha256=digest(catalog), dataset_sha256=digest(dataset))
    if any(len(item["vector"]) != inspection["dimension"] for item in by_case.values()):
        raise ValueError("缓存向量维度与实际索引检查不符")
    scorer = load_scorer()
    files = {path.relative_to(ROOT).as_posix(): digest(path) for path in
             (Path(__file__).resolve(), ROOT / "scripts/eval/retrieval_optimization.py",
              ROOT / "app/application/retrieval_evaluation.py", ROOT / "eval/retrieval/standard-v1.json")}
    evaluator_sha = json_hash(files)
    cache_sha = digest(args.cache)
    products = [_product_from_record(row) for row in records]
    by_id = {p.product_id: p for p in products}

    class CachedEmbedder:
        current_case = None
        async def embed(self, query):
            item = by_case[self.current_case]
            if hashlib.sha256(query.encode()).hexdigest() != item["query_sha256"]:
                raise ValueError("查询未绑定真实缓存向量")
            return item["vector"]

    class CachedIndex:
        requested_sizes = None
        async def search(self, vector, top_n):
            item = by_case[embedder.current_case]
            if vector != item["vector"]:
                raise ValueError("未知向量禁止进入缓存候选回放")
            self.requested_sizes.append(top_n)
            return [VectorHit(hit["product_id"], hit["score"]) for hit in item["hits"][:top_n]]

    embedder, index = CachedEmbedder(), CachedIndex()
    ledger = []

    async def measure(split, threshold, role):
        selected = [case for case in cases if case["split"] == split]
        tag = "none" if threshold is None else str(threshold).replace(".", "_")
        name = f"threshold-{tag}-{split}"
        config = {"min_relevance_score": threshold, "relevance_score_kind": "embedding",
                  "hybrid_enabled": False, "reranker_enabled": False, "recall_candidates": 32}
        guard_score_configuration("embedding", False, False)
        usecase = CatalogSearchUseCase(InMemoryProductRepository(products), embedder=embedder, vector_index=index,
              hybrid_enabled=False, reranker=None, recall_candidates=32, capture_retrieval_stages=True,
              min_relevance_score=threshold, relevance_score_kind="embedding")
        provenance = {"catalog_sha256": digest(catalog), "dataset_sha256": digest(dataset),
            "selection_sha256": json_hash(selected), "source_sha256": source["sha256"],
            "standard_sha256": scorer.standard_fingerprint(scorer.load_standard()),
            "evaluator_sha256": evaluator_sha, "config_sha256": json_hash(config),
            "embedding_identity": cache["embedding_identity"], "query_mode": "structured_spec", "k": 5,
            "split": split, "repetitions": 1, "catalog_count": len(records), "case_count": len(selected)}
        report = {"id": name, "label": f"同源码 cosine 阈值 {threshold} {split} 参数消融",
            "protocol": scorer.PROTOCOL, "standard_id": scorer.PROTOCOL, "status": "completed",
            "scope": "diagnostic", "role": role, "variant": "dense_threshold_ablation",
            "created_at": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
            "parameters_safe": config, "cases": [], "summary": {}, "removed_candidate_count": 0,
            "evaluator_files": files, "shared_cache_sha256": cache_sha,
            "note": "零新调用的真实候选回放；时间是本地消融时间，不能替代在线P95或计费。"}
        for case in selected:
            embedder.current_case, index.requested_sizes = case["id"], []
            spec = ProductSearchSpec(normalized_query=case["query"], top_k=5,
                         **{key: case[key] for key in SPEC_KEYS if key in case})
            started = time.perf_counter()
            result = await usecase.execute(spec)
            diagnostics = result.get("retrieval_diagnostics", {})
            row = scorer.evaluate_case(case, result["hits"], by_id)
            row["elapsed_ms"] = (time.perf_counter() - started) * 1000
            row["degraded"] = (result.get("recall_strategy") != "embedding_only" or result.get("rerank_applied", False)
                or diagnostics.get("score_kind") != "embedding"
                or diagnostics.get("relevance_filter_applied") != (threshold is not None))
            report["cases"].append(row)
            report["removed_candidate_count"] += diagnostics.get("low_relevance_removed", 0)
            ledger.append({"report_id": name, "case_id": case["id"], "split": split, "threshold": threshold,
                "score_kind": diagnostics.get("score_kind"), "relevance_filter_applied": diagnostics.get("relevance_filter_applied"),
                "low_relevance_removed": diagnostics.get("low_relevance_removed", 0),
                "requested_sizes": index.requested_sizes.copy(), "query_sha256": by_case[case["id"]]["query_sha256"],
                "external_api_calls": 0, "returned_product_ids": [hit["product_id"] for hit in result["hits"]]})
        report["summary"] = scorer.aggregate_cases(report["cases"])
        write_new(args.output / name / "report.json", report)
        print(json.dumps({"report_id": name, "summary": report["summary"], "removed": report["removed_candidate_count"]}), flush=True)
        return report

    with deny_network():
        baseline = await measure("dev", None, "baseline")
        candidates = [await measure("dev", threshold, "candidate") for threshold in THRESHOLDS]
        lock = choose_threshold(baseline, candidates, grid)
        lock.update(cache_sha256=cache_sha, source_sha256=source["sha256"], evaluator_sha256=evaluator_sha,
                    grid_sha256=json_hash(grid), baseline_report_id=baseline["id"], new_external_calls=0)
        write_new(args.output / "locked-parameters.json", lock)
        holdout = None
        if lock["status"] == "selected":
            validate_lock(lock, embedding_identity=cache["embedding_identity"])
            holdout_base = await measure("holdout", None, "baseline")
            holdout_candidate = await measure("holdout", lock["threshold"], "candidate")
            holdout = protection_result(holdout_base, holdout_candidate)
            holdout.update(baseline=holdout_base["summary"], candidate=holdout_candidate["summary"],
                removed_candidate_count=holdout_candidate["removed_candidate_count"],
                enable_recommendation="do_not_enable" if not holdout["passed"] or not holdout_candidate["removed_candidate_count"] else "not_authorized_diagnostic_only")
            write_new(args.output / "holdout-protection.json", holdout)
    stable = (source_fingerprint(root)["sha256"] == source["sha256"] and digest(args.cache) == cache_sha
              and all(digest(ROOT / path) == value for path, value in files.items()))
    summary = {"status": "completed" if stable else "failed", "scope": "diagnostic_parameter_ablation",
               "selected_threshold": lock["threshold"], "embedding_identity": cache["embedding_identity"],
               "source_sha256": source["sha256"], "evaluator_sha256": evaluator_sha,
               "external_api_calls": 0, "rerank_calls": 0, "holdout": holdout,
               "enable_recommendation": holdout["enable_recommendation"] if holdout else "do_not_enable",
               "note": "保护门槛通过不等于收益验收；无实际过滤/指标收益时不建议启用。"}
    write_new(args.output / "ledger.json", {"external_api_calls": 0, "cache_sha256": cache_sha, "rows": ledger})
    write_new(args.output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("calibrate", "_child"))
    for name in ("source-root", "source-manifest", "cache", "inspection", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == "calibrate":
        command = [sys.executable, str(Path(__file__).resolve()), "_child"]
        for name in ("source_root", "source_manifest", "cache", "inspection", "output"):
            command.extend(("--" + name.replace("_", "-"), str(getattr(args, name).resolve())))
        raise SystemExit(subprocess.run(command, cwd=ROOT).returncode)
    result = asyncio.run(calibrate(args))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
