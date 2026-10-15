"""隔离复制商品向量索引，冻结真实查询向量，再回放新旧检索源码。

只读取公开评测目录；不复制凭据、买家数据库或其它 Qdrant 集合。
索引不完整时最多探测一次查询 Embedding，绝不自动重建商品向量。
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = "globex_products"


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path: Path) -> dict[str, str]:
    return {item.relative_to(path).as_posix(): digest(item) for item in sorted(path.rglob("*"))
            if item.is_file()}


def safe_failure(error: Exception) -> dict:
    value = {"error_type": type(error).__name__}
    response = getattr(error, "response", None)
    if response is not None:
        value["http_status"] = response.status_code
        try:
            body = response.json()
            code = body.get("error", body)
            code = code.get("code") if isinstance(code, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
                value["code"] = code
        except (ValueError, AttributeError):
            pass
    return value


def catalog_path() -> Path:
    packaged = ROOT / "catalog/catalog-v3.jsonl"
    return packaged if packaged.is_file() else ROOT / "data/catalog-v3.jsonl"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluator_fingerprint() -> dict:
    names = (Path(__file__).resolve(), ROOT / "scripts/eval/retrieval_optimization.py",
             ROOT / "app/application/retrieval_evaluation.py", ROOT / "eval/retrieval/standard-v1.json")
    from scripts.eval.retrieval_optimization import json_hash
    files = {name.relative_to(ROOT).as_posix(): digest(name) for name in names}
    return {"sha256": json_hash(files), "files": files}


async def inspect_index(evidence: Path) -> dict:
    from app.infrastructure.persistence.seed_products import _product_from_record
    from app.infrastructure.settings import load_settings
    from app.infrastructure.vector.qdrant_product_index import QdrantProductIndex

    source = ROOT / "data/qdrant"
    before = fingerprint(source)
    report = {"status": "failed", "created_at": datetime.now(timezone.utc).isoformat(),
              "original_index_before": before, "external_api_calls": 0,
              "copied_collection": COLLECTION, "catalog_sha256": digest(catalog_path()),
              "copy_scope": ["meta.json", f"collection/{COLLECTION}/"],
              "note": "仅商品集合隔离副本；不创建或更新商品向量，不连接原索引。"}
    index = None
    try:
        meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
        if set(meta.get("collections", {})) != {COLLECTION} or meta.get("aliases"):
            raise ValueError("原索引元信息并非单一无别名商品集合")
        vector = meta["collections"][COLLECTION].get("vectors", {})
        if vector.get("size") != 1024 or vector.get("distance") != "Cosine":
            raise ValueError("商品向量维度或距离与检查约定不同")
        for item in [source, source / "meta.json", *(source / "collection" / COLLECTION).rglob("*")]:
            if item.is_symlink() or getattr(item.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("拒绝复制链接或重解析点")
        isolated = evidence / "isolated-live-index"
        target = isolated / "qdrant"
        target.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source / "meta.json", target / "meta.json")
        shutil.copytree(source / "collection" / COLLECTION, target / "collection" / COLLECTION)
        copied = fingerprint(target)
        if any(before.get(name) != value for name, value in copied.items()):
            raise ValueError("隔离副本与原文件指纹不一致")
        settings = replace(load_settings(require_llm_key=False), data_dir=isolated,
                           qdrant_url="", qdrant_collection=COLLECTION)
        products = [_product_from_record(record) for record in rows(catalog_path())]
        index = QdrantProductIndex(settings)
        pending = await index.products_needing_embeddings(products)
        point_count = (await index._client.count(COLLECTION, exact=True)).count
        report.update(status="complete" if not pending else "incomplete", product_count=len(products),
                      existing_points=point_count, reusable_products=len(products) - len(pending),
                      missing_or_stale_products=len(pending), embedding_identity=index.embedding_key,
                      dimension=vector["size"], isolated_files=copied,
                      credential_fields_available={"EMBEDDING_API_KEY": bool(settings.embedding_api_key),
                                                   "EMBEDDING_BASE_URL": bool(settings.embedding_base_url),
                                                   "EMBEDDING_MODEL": bool(settings.embedding_model)})
    except Exception as error:
        report["failure"] = safe_failure(error)
    finally:
        if index is not None:
            await index.close()
        after = fingerprint(source)
        report.update(original_index_after=after, original_index_unchanged=before == after)
        if before != after:
            report["status"] = "failed"
            report["failure"] = {"error_type": "OriginalIndexChangedDuringInspection"}
    write_new(evidence / "live-index-inspection.json", report)
    return report


async def collect_vectors(evidence: Path) -> dict:
    from app.infrastructure.embedding.openai_embedding_client import OpenAIEmbeddingClient
    from app.infrastructure.settings import load_settings
    from app.infrastructure.vector.qdrant_product_index import QdrantProductIndex

    inspection = json.loads((evidence / "live-index-inspection.json").read_text(encoding="utf-8"))
    if inspection["status"] not in ("complete", "incomplete") or not inspection["original_index_unchanged"]:
        raise ValueError("索引检查失败，禁止继续外部调用")
    dataset = ROOT / "eval/v2/product_retrieval.jsonl"
    cases = rows(dataset)
    complete = inspection["status"] == "complete"
    if len(cases) > 72:
        raise ValueError("查询数超过本轮 72 次调用预算")
    selected = cases if complete else cases[:1]
    settings = load_settings(require_llm_key=False)
    from app.infrastructure.vector.embedding_identity import embedding_identity
    if embedding_identity(settings) != inspection["embedding_identity"]:
        raise ValueError("索引检查后模型身份已改变")
    # 在外部调用前独占本轮预算；失败也不删除，重复运行必须另建并检查证据目录。
    write_new(evidence / "live-query-started.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "maximum_calls": len(selected),
        "embedding_identity": inspection["embedding_identity"], "automatic_retry": False,
    })
    report = {"status": "completed", "scope": "diagnostic", "created_at": datetime.now(timezone.utc).isoformat(),
              "index_complete": complete, "embedding_identity": inspection["embedding_identity"],
              "catalog_sha256": inspection["catalog_sha256"], "dataset_sha256": digest(dataset),
              "external_api_calls": 0, "rerank_calls": 0, "call_cost": None,
              "usage_tokens": None, "ledger": [], "queries": [],
              "note": "真实查询Embedding与商品向量召回；未调用精排。索引不完整时仅可用作服务探针。"}
    class RecordedEmbeddingClient(OpenAIEmbeddingClient):
        last_usage: dict

        async def _embed_chunk(self, client, chunk):
            self.last_usage = {}

            async def observe(response):
                await response.aread()
                try:
                    body = response.json()
                    usage = body.get("usage") if isinstance(body, dict) else None
                    if isinstance(usage, dict):
                        for key in ("prompt_tokens", "input_tokens", "total_tokens"):
                            value = usage.get(key)
                            if type(value) is int and value >= 0:
                                self.last_usage[key] = value
                except ValueError:
                    pass

            client.event_hooks["response"].append(observe)
            try:
                return await super()._embed_chunk(client, chunk)
            finally:
                client.event_hooks["response"].remove(observe)

    embedder = RecordedEmbeddingClient(settings, timeout_seconds=20)
    index = None
    original_before = fingerprint(ROOT / "data/qdrant")
    try:
        if complete:
            index = QdrantProductIndex(replace(settings, data_dir=evidence / "isolated-live-index",
                                             qdrant_url="", qdrant_collection=COLLECTION))
        for case in selected:
            started = time.perf_counter()
            ledger = {"case_id": case["id"], "kind": "embedding", "success": False,
                      "input_tokens": None, "output_tokens": None, "reported_total_tokens": None,
                      "reported_cost": None}
            report["external_api_calls"] += 1
            try:
                vector = await asyncio.wait_for(embedder.embed(case["query"]), timeout=20)
                if (len(vector) != inspection["dimension"]
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)):
                    raise ValueError("查询向量维度或数值无效")
                hits = await index.search(vector, top_n=inspection["product_count"]) if index is not None else []
                if complete and len(hits) != inspection["product_count"]:
                    raise ValueError("完整索引的实际召回数量不足")
                report["queries"].append({"case_id": case["id"], "query_sha256": hashlib.sha256(case["query"].encode()).hexdigest(),
                                          "vector": vector, "hits": [{"product_id": hit.product_id, "score": hit.score} for hit in hits]})
                ledger.update(success=True, vector_dimension=len(vector), retrieved_count=len(hits))
            except Exception as error:
                ledger["failure"] = safe_failure(error)
                report["status"] = "failed"
                report["failure"] = {"case_id": case["id"], **safe_failure(error)}
            finally:
                observed = getattr(embedder, "last_usage", {})
                ledger["input_tokens"] = observed.get("input_tokens", observed.get("prompt_tokens"))
                ledger["reported_total_tokens"] = observed.get("total_tokens")
                ledger["elapsed_ms"] = (time.perf_counter() - started) * 1000
                report["ledger"].append(ledger)
                if len(report["ledger"]) == 1 or len(report["ledger"]) % 8 == 0 or not ledger["success"]:
                    print(json.dumps({"completed_calls": len(report["ledger"]), "maximum_calls": len(selected),
                                      "last_success": ledger["success"]}), flush=True)
            if report["status"] == "failed":
                break
        if not complete and report["status"] == "completed":
            report["status"] = "probe_only"
        observed_totals = [item["reported_total_tokens"] for item in report["ledger"]]
        report["usage_tokens"] = sum(observed_totals) if all(value is not None for value in observed_totals) else None
    finally:
        if index is not None:
            await index.close()
        original_after = fingerprint(ROOT / "data/qdrant")
        report.update(original_index_before=original_before, original_index_after=original_after,
                      original_index_unchanged=original_before == original_after)
        if original_before != original_after:
            report["status"] = "failed"
            report["failure"] = {"error_type": "OriginalIndexChangedDuringCollection"}
    write_new(evidence / "live-query-vectors.json", report)
    return report


async def replay_child(args) -> dict:
    # 被测 app 只能在 source-root 生效后导入；冻结计分器来自当前工程。
    sys.path.insert(0, str(ROOT))
    from scripts.eval.retrieval_optimization import (
        SPEC_KEYS, additional_case_metrics, aggregate_additional, json_hash, load_scorer,
        source_fingerprint, verify_source_manifest,
    )
    root = args.source_root.resolve()
    sys.path.insert(0, str(root))
    from app.application.usecases.catalog_search import CatalogSearchUseCase
    from app.domain.catalog.product_search_spec import ProductSearchSpec
    from app.domain.catalog.ports.retrieval_ports import VectorHit
    from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository
    from app.infrastructure.persistence.seed_products import _product_from_record

    if not Path(inspect.getfile(CatalogSearchUseCase)).resolve().is_relative_to(root / "app"):
        raise ValueError("实际被测代码未来自指定源码快照")
    source = source_fingerprint(root)
    manifest = verify_source_manifest(root, args.source_manifest, source)
    if args.role == "baseline" and not manifest["verified"]:
        raise ValueError("旧源码基线必须绑定冻结指纹")
    cache_path = args.evidence_root / "live-query-vectors.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    if (cached["status"] != "completed" or not cached["index_complete"]
            or not cached["original_index_unchanged"]):
        raise ValueError("真实向量证据不完整，不能回放为 Dense 对照")
    dataset = ROOT / "eval/v2/product_retrieval.jsonl"
    selected = [row for row in rows(dataset) if args.split == "all" or row.get("split") == args.split]
    if not selected or cached["dataset_sha256"] != digest(dataset) or cached["catalog_sha256"] != digest(catalog_path()):
        raise ValueError("冻结向量与评测目录不一致")
    by_case = {row["case_id"]: row for row in cached["queries"]}
    if len(by_case) != len(cached["queries"]) or set(by_case) != {row["id"] for row in rows(dataset)}:
        raise ValueError("真实查询向量必须覆盖全部原始评测用例且不重复")
    records = rows(catalog_path())
    expected_ids = {record["product_id"] for record in records}
    for case in selected:
        item = by_case[case["id"]]
        if (item["query_sha256"] != hashlib.sha256(case["query"].encode()).hexdigest()
                or len(item["hits"]) != len(expected_ids)
                or {hit["product_id"] for hit in item["hits"]} != expected_ids
                or any(type(hit["score"]) not in (int, float) or not math.isfinite(hit["score"]) for hit in item["hits"])):
            raise ValueError("真实查询或召回候选证据无效")

    class FrozenRealEmbedder:
        current_case: str | None = None
        async def embed(self, query):
            item = by_case[self.current_case]
            if hashlib.sha256(query.encode()).hexdigest() != item["query_sha256"]:
                raise ValueError("回放查询未匹配冻结向量")
            return item["vector"]

    class FrozenRealIndex:
        requested_sizes: list[int]
        def __init__(self):
            self.requested_sizes = []
        async def search(self, embedding, top_n):
            item = by_case[embedder.current_case]
            if embedding != item["vector"]:
                raise ValueError("回放向量未匹配真实请求证据")
            self.requested_sizes.append(top_n)
            return [VectorHit(hit["product_id"], hit["score"]) for hit in item["hits"][:top_n]]

    embedder, index = FrozenRealEmbedder(), FrozenRealIndex()
    products = [_product_from_record(record) for record in records]
    by_id, records_by_id = {product.product_id: product for product in products}, {r["product_id"]: r for r in records}
    usecase = CatalogSearchUseCase(InMemoryProductRepository(products), embedder=embedder,
                                  vector_index=index, hybrid_enabled=False, recall_candidates=32,
                                  capture_retrieval_stages=True)
    scorer, evaluator = load_scorer(), evaluator_fingerprint()
    config = {"mode": "dense_frozen_real", "reranker_enabled": False, "hybrid_enabled": False,
              "recall_candidates": 32, "vector_cache_sha256": digest(cache_path),
              "latency_scope": "replay_after_frozen_real_retrieval"}
    provenance = {"catalog_sha256": digest(catalog_path()), "dataset_sha256": digest(dataset),
                  "selection_sha256": json_hash(selected), "source_sha256": source["sha256"],
                  "standard_sha256": scorer.standard_fingerprint(scorer.load_standard()),
                  "evaluator_sha256": evaluator["sha256"], "config_sha256": json_hash(config),
                  "embedding_identity": cached["embedding_identity"], "query_mode": "structured_spec",
                  "k": 5, "split": args.split, "repetitions": 1, "catalog_count": len(records),
                  "sku_count": sum(len(row["skus"]) for row in records), "case_count": len(selected)}
    report = {"id": args.output.name, "label": args.label, "protocol": scorer.PROTOCOL,
              "standard_id": scorer.PROTOCOL, "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "completed", "scope": "diagnostic", "role": args.role,
              "variant": "dense_frozen_real", "provenance": provenance, "parameters_safe": config,
              "source_files": source["files"], "source_manifest": manifest, "evaluator": evaluator,
              "cases": [], "summary": {},
              "note": "新旧源码复用同批真实Embedding/Qdrant候选，无模拟向量；未精排。耗时仅回放，不能作为在线P95收益。"}
    extras, accepted = [], inspect.signature(ProductSearchSpec).parameters
    for case in selected:
        embedder.current_case = case["id"]
        index.requested_sizes = []
        spec = ProductSearchSpec(normalized_query=case["query"], top_k=5,
                                 **{key: case[key] for key in SPEC_KEYS if key in case and key in accepted})
        started = time.perf_counter()
        try:
            result = await usecase.execute(spec)
            row = scorer.evaluate_case(case, result["hits"], by_id)
            row["elapsed_ms"] = (time.perf_counter() - started) * 1000
            row["degraded"] = result.get("recall_strategy") != "embedding_only" or result.get("rerank_applied", False)
            report["cases"].append(row)
            extra = additional_case_metrics(case, result["hits"], records_by_id, result, [])
            extra["frozen_vector_requested_sizes"] = index.requested_sizes.copy()
            extras.append(extra)
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = {"case_id": case["id"], **safe_failure(error)}
            break
    report["summary"] = scorer.aggregate_cases(report["cases"])
    if source_fingerprint(root)["sha256"] != source["sha256"] or evaluator_fingerprint() != evaluator:
        report["status"] = "failed"
        report["failure"] = {"error_type": "SourcesChangedDuringReplay"}
    args.output.mkdir(parents=True, exist_ok=False)
    write_new(args.output / "report.json", report)
    additional = aggregate_additional(extras)
    additional.update(external_api_calls=0, call_cost={"amount": None, "currency": None,
                      "scope": "replay_zero_new_calls_original_collection_cost_unknown"})
    write_new(args.output / "additional-diagnostics.json", {"report_id": report["id"],
              "scope": "diagnostic", "summary": additional, "cases": extras,
              "shared_live_call_ledger": "../live-query-vectors.json",
              "limitations": ["共享真实查询及全目录候选，未调用精排。", "回放耗时不能代表在线总耗时。"]})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "collect", "replay", "_replay_child"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--role", choices=("baseline", "candidate"))
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="holdout")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", default="真实 Dense 冻结候选回放")
    args = parser.parse_args()
    evidence = args.evidence_root.resolve()
    if not evidence.is_relative_to(ROOT / "eval/verification"):
        parser.error("证据必须落在工程 eval/verification 内")
    if args.command in ("replay", "_replay_child"):
        if args.source_root is None or args.role is None or args.output is None:
            parser.error("回放必须指定 source-root、role、output")
        args.evidence_root = evidence
        if args.command == "replay":
            forwarded = []
            for key in ("evidence_root", "source_root", "source_manifest", "role", "split", "output", "label"):
                value = getattr(args, key)
                if value is not None:
                    forwarded.extend(("--" + key.replace("_", "-"), str(value.resolve()) if isinstance(value, Path) else value))
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_replay_child", *forwarded], cwd=ROOT)
            raise SystemExit(child.returncode)
        report = asyncio.run(replay_child(args))
    else:
        report = asyncio.run(inspect_index(evidence) if args.command == "inspect" else collect_vectors(evidence))
    print(json.dumps({key: report.get(key) for key in ("status", "product_count", "existing_points", "reusable_products",
                     "missing_or_stale_products", "external_api_calls", "original_index_unchanged", "failure")}, ensure_ascii=False))
    raise SystemExit(0 if report["status"] not in ("failed",) else 1)


if __name__ == "__main__":
    main()
