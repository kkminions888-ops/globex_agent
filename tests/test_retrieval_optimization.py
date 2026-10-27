"""优化诊断的证据隔离、SKU计分与仅开发集选参契约。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.eval.retrieval_optimization import (
    additional_case_metrics, aggregate_additional, converted_major, json_hash,
    select_parameters, source_fingerprint, verify_source_manifest, write_new,
)


def product(pid="P9001", canonical="CAN-A", weight=.3, kind="背包"):
    return {"product_id": pid, "canonical_product_id": canonical, "product_type": kind,
            "weight_kg": weight, "skus": [
                {"sku_id": f"{pid}-S1", "spec": "大号", "price_major": 420, "currency": "CNY", "stock": 5},
                {"sku_id": f"{pid}-S2", "spec": "小号", "price_major": 180, "currency": "CNY", "stock": 5}]}


def hit(pid="P9001", suffix=2, price=180):
    return {"product_id": pid, "default_sku_id": f"{pid}-S{suffix}", "price_major": price,
            "currency": "CNY", "source_price_major": price, "source_currency": "CNY", "score": 1}


def case(**extra):
    return {"id": "case", "kind": "contract", "family": "sku", "relevant": ["P9001"],
            "relevant_canonical_ids": ["CAN-A"], "target_currency": "CNY", **extra}


def metrics(data=None, hits=None, products=None, result=None, unsupported=None):
    return additional_case_metrics(data or case(), hits if hits is not None else [hit()],
           products or {"P9001": product()}, result or {}, unsupported or [])


def test_price_consistency_uses_selected_sku_and_not_cheapest_price():
    good = metrics()
    wrong = metrics(hits=[hit(suffix=1, price=180)])
    assert good["display_price_checks"] == [True]
    assert wrong["display_price_checks"] == [False]


def test_explicit_sku_and_specification_identity_are_measured():
    row = metrics(case(sku_spec="小号", sku_id="P9001-S1"))
    assert row["sku_spec_checks"] == [True]
    assert row["selected_sku_checks"] == [False]
    assert row["contract_expectation_met"] is False


def test_unknown_weight_is_not_lightweight_and_type_is_independent():
    row = metrics(case(max_weight_kg=.5, product_type="背包"), products={"P9001": product(weight=0, kind="颈枕")})
    assert row["weight_checks"] == [False]
    assert row["type_checks"] == [False]
    assert row["contract_expectation_met"] is False


def test_exchange_rate_check_is_frozen_and_independent():
    assert converted_major({"price_major": 20, "currency": "USD"}, "CNY") == 142
    assert converted_major({"price_major": 7.1, "currency": "CNY"}, "USD") == 1


def test_false_exclusion_does_not_misclassify_missing_recall_as_budget_error():
    data = case(price_max_major=200)
    missing = metrics(data, hits=[])
    excluded = metrics(data, hits=[], result={"filtered_out": [{"product_id": "P9001", "reason": "over_price_cap"}]})
    assert missing["budget_eligible_gold_product_count"] == 1
    assert missing["budget_false_excluded_product_ids"] == []
    assert excluded["budget_false_excluded_product_ids"] == ["P9001"]


def test_explicit_expensive_sku_cannot_be_counted_as_false_budget_exclusion():
    row = metrics(case(price_max_major=200, sku_id="P9001-S1"), hits=[],
                  result={"filtered_out": [{"product_id": "P9001", "reason": "over_price_cap"}]})
    assert row["budget_eligible_gold_product_count"] == 0
    assert row["budget_false_excluded_product_ids"] == []


def test_duplicates_and_actual_candidate_pool_are_measured_separately():
    products = {"P9001": product(), "P9002": product("P9002", "CAN-A")}
    row = metrics(hits=[hit(), hit("P9002")], products=products,
                  result={"retrieval_stages": {"merged_candidates": ["P9001", "P9002"]}})
    summary = aggregate_additional([row])
    assert row["mrr_at_5"] == 1
    assert row["candidate_recall"] == 1
    assert summary["duplicate_rate"] == .5
    assert summary["external_api_calls"] == 0


def test_absent_trace_and_zero_display_stay_unknown():
    row = metrics(hits=[])
    summary = aggregate_additional([row])
    assert row["candidate_recall"] is None
    assert summary["display_price_consistency"]["satisfaction"] is None
    assert summary["duplicate_rate"] is None


def test_source_manifest_binds_all_app_files_and_rejects_mutation(tmp_path):
    (tmp_path / "app").mkdir()
    source = tmp_path / "app/__init__.py"
    source.write_text("value=1\n", encoding="utf-8")
    fingerprint = source_fingerprint(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_files": fingerprint["files"]}), encoding="utf-8")
    assert verify_source_manifest(tmp_path, manifest, fingerprint)["verified"]
    source.write_text("value=2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="来源清单不一致"):
        verify_source_manifest(tmp_path, manifest, source_fingerprint(tmp_path))


def test_new_evidence_never_overwrites_existing_file(tmp_path):
    path = tmp_path / "report.json"
    write_new(path, {"old": True})
    with pytest.raises(FileExistsError):
        write_new(path, {"old": False})
    assert json.loads(path.read_text())["old"] is True


def report(identifier="report", *, split="dev", ndcg=.6, recall=.8, precision=.5, parameters=None):
    row = {"id": "case", "kind": "positive", "family": "test", "ndcg_at_5": ndcg,
           "recall_at_5": recall, "returned_precision": precision, "empty_ok": None,
           "violations": dict(budget=0, material=0, shipping=0),
           "applicable_constraints": dict(budget=False, material=False, shipping=False),
           "returned_count": 5, "elapsed_ms": 1, "error": None, "degraded": False}
    return {"id": identifier, "protocol": "retrieval-standard-v1", "standard_id": "retrieval-standard-v1",
            "status": "completed", "scope": "diagnostic", "role": "candidate", "cases": [row],
            "provenance": {"split": split, "embedding_identity": "none:lexical", "query_mode": "structured_spec",
                           "case_count": 1, **dict.fromkeys(("catalog_sha256", "dataset_sha256", "selection_sha256",
                           "source_sha256", "evaluator_sha256", "standard_sha256"), "a" * 64)},
            "parameters_safe": {"mode": "field_hybrid", **(parameters or {})}}


def grid():
    return {"configurations": [{"id": "small", "field_weights": {"title": 2}, "recall_candidates": 32},
                               {"id": "large", "field_weights": {"title": 2}, "recall_candidates": 64}]}


def candidates():
    return [report(row["id"], parameters={key: row[key] for key in ("field_weights", "recall_candidates")})
            for row in grid()["configurations"]]


def test_parameter_selection_rejects_holdout_and_incomplete_grid():
    values = candidates()
    values[0]["provenance"]["split"] = "holdout"
    with pytest.raises(ValueError, match="只能来自完整 dev"):
        select_parameters(report(), values, grid())
    with pytest.raises(ValueError, match="完整测量"):
        select_parameters(report(), candidates()[:1], grid())


def test_parameter_selection_protects_recall_and_precision_before_ndcg():
    values = candidates()
    values[0]["cases"][0].update(ndcg_at_5=.99, recall_at_5=.7)
    values[1]["cases"][0].update(ndcg_at_5=.7)
    selected = select_parameters(report(), values, grid())
    assert selected["selected_configuration"] == "large"
    assert selected["selection_split"] == "dev"


def test_parameter_selection_prefers_smaller_budget_when_quality_tied():
    selected = select_parameters(report(), candidates(), grid())
    assert selected["selected_configuration"] == "small"


def test_parameter_selection_rejects_changed_source_between_configurations():
    values = candidates()
    values[1]["provenance"]["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="条件不一致"):
        select_parameters(report(), values, grid())
