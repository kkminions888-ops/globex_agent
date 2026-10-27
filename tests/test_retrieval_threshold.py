"""真实模型阈值校准的留出隔离、评分来源及严格门槛契约。"""
from copy import deepcopy
import hashlib
import socket

import pytest

from scripts.eval.retrieval_threshold import (PROTECTED, THRESHOLDS, choose_threshold,
    deny_network, grid_definition, guard_score_configuration, protection_result,
    validate_cache, validate_lock)


IDENTITY = "a" * 64


def cached():
    cases = [{"id": "case", "query": "public query"}]
    records = [{"product_id": "P1001"}, {"product_id": "P1002"}]
    value = {"status": "completed", "index_complete": True, "original_index_unchanged": True,
             "catalog_sha256": "catalog", "dataset_sha256": "dataset", "rerank_calls": 0,
             "embedding_identity": IDENTITY, "queries": [{"case_id": "case", "vector": [.1, .2],
             "query_sha256": hashlib.sha256(b"public query").hexdigest(),
             "hits": [{"product_id": "P1001", "score": .5}, {"product_id": "P1002", "score": .4}]}]}
    return value, cases, records


def validate(value, cases, records):
    return validate_cache(value, cases, records, expected_identity=IDENTITY,
                          catalog_sha256="catalog", dataset_sha256="dataset")


def report(threshold=None, split="dev", **quality):
    return {"id": str(threshold), "status": "completed", "cases": [{"error": None, "degraded": False}],
            "provenance": {"split": split, "embedding_identity": IDENTITY,
                           **dict.fromkeys(("catalog_sha256", "dataset_sha256", "selection_sha256",
                                            "source_sha256", "evaluator_sha256"), "fixed")},
            "parameters_safe": {"min_relevance_score": threshold, "relevance_score_kind": "embedding",
                                "hybrid_enabled": False, "reranker_enabled": False},
            "summary": {"ndcg_at_5": .8, "returned_precision": .5, "recall_at_5": .9,
                        "empty_accuracy": 1, **quality}}


def test_cache_requires_matching_real_model_identity():
    value, cases, records = cached()
    assert set(validate(value, cases, records)) == {"case"}
    value["embedding_identity"] = "b" * 64
    with pytest.raises(ValueError, match="模型身份不匹配"):
        validate(value, cases, records)


@pytest.mark.parametrize("change", ["unknown", "duplicate", "missing", "nan", "score_range", "order", "zero_vector"])
def test_cache_rejects_unknown_missing_or_invalid_vectors_and_candidates(change):
    value, cases, records = cached()
    row = value["queries"][0]
    if change == "unknown": row["hits"][0]["product_id"] = "P9999"
    if change == "duplicate": row["hits"][0]["product_id"] = "P1002"
    if change == "missing": row["hits"].pop()
    if change == "nan": row["vector"][0] = float("nan")
    if change == "score_range": row["hits"][0]["score"] = 2
    if change == "order": row["hits"].reverse()
    if change == "zero_vector": row["vector"] = [0, 0]
    with pytest.raises(ValueError):
        validate(value, cases, records)


@pytest.mark.parametrize("score_kind,hybrid,rerank", [
    ("reranker", False, False), ("bm25", False, False), ("rrf", True, False),
    ("embedding", True, False), ("embedding", False, True)])
def test_threshold_cannot_mix_reranker_rrf_or_other_scores(score_kind, hybrid, rerank):
    with pytest.raises(ValueError, match="禁止 RRF/精排混用"):
        guard_score_configuration(score_kind, hybrid, rerank)


def test_selection_only_uses_complete_development_grid():
    candidates = [report(value) for value in THRESHOLDS]
    candidates[-1]["provenance"]["split"] = "holdout"
    with pytest.raises(ValueError, match="禁止使用 holdout"):
        choose_threshold(report(), candidates, grid_definition())
    with pytest.raises(ValueError, match="完整 dev 网格"):
        choose_threshold(report(), candidates[:-1], grid_definition())


@pytest.mark.parametrize("key,value", [("ndcg_at_5", .799), ("returned_precision", .499),
                                       ("empty_accuracy", .99), ("recall_at_5", .889)])
def test_protected_metrics_cannot_be_relaxed(key, value):
    before, candidate = report(), report(.1, **{key: value})
    assert protection_result(before, candidate)["passed"] is False


def test_selection_is_precision_first_and_prefers_lower_threshold_for_ties():
    candidates = [report(value) for value in THRESHOLDS]
    candidates[2]["summary"].update(returned_precision=.6, recall_at_5=.89)
    candidates[3]["summary"].update(returned_precision=.6, recall_at_5=.89)
    candidates[4]["summary"].update(ndcg_at_5=.99, returned_precision=.59)
    lock = choose_threshold(report(), candidates, grid_definition())
    assert lock["threshold"] == .2
    assert lock["selection_split"] == "dev"


def test_degraded_report_cannot_win_selection():
    candidates = [report(value) for value in THRESHOLDS]
    candidates[5]["summary"]["returned_precision"] = 1
    candidates[5]["cases"][0]["degraded"] = True
    assert choose_threshold(report(), candidates, grid_definition())["threshold"] == 0


def test_grid_and_lock_cannot_relax_protection_or_change_model():
    changed = deepcopy(grid_definition())
    changed["protected_metrics"]["recall_min_delta"] = -.2
    with pytest.raises(ValueError, match="保护指标被更改"):
        choose_threshold(report(), [report(value) for value in THRESHOLDS], changed)
    lock = choose_threshold(report(), [report(value) for value in THRESHOLDS], grid_definition())
    validate_lock(lock, embedding_identity=IDENTITY)
    with pytest.raises(ValueError, match="模型身份或评分类型不匹配"):
        validate_lock(lock, embedding_identity="b" * 64)
    lock["protected_metrics"] = {**PROTECTED, "recall_min_delta": -.2}
    with pytest.raises(ValueError, match="不能放宽"):
        validate_lock(lock, embedding_identity=IDENTITY)


def test_real_replay_has_a_network_denial_guard_and_restores_socket_functions():
    original = socket.create_connection
    with deny_network():
        with pytest.raises(RuntimeError, match="禁止任何网络"):
            socket.create_connection(("example.com", 443))
    assert socket.create_connection is original
