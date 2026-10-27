"""标准口径、前5展示防补位与证据完整性门禁。"""
from copy import deepcopy
import math

import pytest

from app.application.retrieval_evaluation import (
    PROTOCOL, aggregate_cases, compare_reports, evaluate_case, load_standard, standard_fingerprint, validate_report,
)
from scripts.eval.retrieval_standard import _write_new


def product(identifier, canonical=None, materials=("金属",), ships=("CN",)):
    return {"product_id": identifier, "canonical_product_id": canonical or identifier,
            "material_tags": list(materials), "ships_to": list(ships)}


def hit(identifier, price=20, currency="CNY"):
    return {"product_id": identifier, "price_major": price, "currency": currency}


def query(**overrides):
    return {"id": "case-1", "family": 1, "kind": "intent", "expected_empty": False,
            "relevant_canonical_ids": ["A", "B"], **overrides}


def evidence(identifier="case-1", quality=.4, **overrides):
    return {"id": identifier, "family": 1, "kind": "intent", "ndcg_at_5": quality,
            "recall_at_5": quality, "returned_precision": quality, "empty_ok": None,
            "violations": {"budget": 0, "material": 0, "shipping": 0},
            "applicable_constraints": {"budget": True, "material": True, "shipping": True},
            "returned_count": 5, "elapsed_ms": 10., "error": None, "degraded": False, **overrides}


def report(role, quality=.4, **overrides):
    empty = evidence("empty-1", kind="empty", ndcg_at_5=None, recall_at_5=None,
                     returned_precision=None, empty_ok=True, returned_count=0)
    return {"protocol": PROTOCOL, "standard_id": PROTOCOL, "id": role, "label": role,
            "status": "completed", "scope": "formal", "role": role,
            "provenance": {"catalog_sha256": "a"*64, "dataset_sha256": "b"*64,
                           "selection_sha256": "c"*64, "source_sha256": "d"*64, "config_sha256": "e"*64,
                           "standard_sha256": standard_fingerprint(load_standard()), "evaluator_sha256": "a"*64,
                           "embedding_identity": "f"*64, "query_mode": "structured_spec", "k": 5,
                           "split": "holdout", "repetitions": 1, "catalog_count": 3700, "case_count": 2},
            "summary": {"ndcg_at_5": 1}, "cases": [evidence(quality=quality), empty], **overrides}


def test_binary_relevance_does_not_use_gold_order():
    products = {"A": product("A"), "B": product("B")}
    first = evaluate_case(query(), [hit("B"), hit("A")], products)
    second = evaluate_case(query(relevant_canonical_ids=["B", "A"]), [hit("B"), hit("A")], products)
    assert first["ndcg_at_5"] == second["ndcg_at_5"] == 1


def test_duplicate_still_consumes_position_and_sixth_cannot_fill_top5():
    products = {name: product(name, "A" if name in ("A1", "A2") else name) for name in ("A1", "A2", "X", "Y", "Z", "B")}
    row = evaluate_case(query(), [hit(name) for name in products], products)
    assert row["recall_at_5"] == .5
    assert row["returned_precision"] == .2
    assert row["ndcg_at_5"] == pytest.approx(1 / (1 + 1/math.log2(3)))
    assert row["returned_count"] == 5


def test_positive_empty_result_scores_zero_but_expected_empty_is_separate():
    positive = evaluate_case(query(), [], {})
    empty = evaluate_case(query(id="empty", kind="empty", expected_empty=True, relevant_canonical_ids=[]), [], {})
    summary = aggregate_cases([positive, empty])
    assert summary["ndcg_at_5"] == 0
    assert summary["empty_accuracy"] == 1
    assert summary["positive_cases"] == summary["empty_cases"] == 1


def test_three_constraints_and_rate_have_explicit_denominator():
    case = query(price_max_major=10, target_currency="CNY", ship_to="CN", required_material_tags=["金属"])
    row = evaluate_case(case, [hit("A", price=100)], {"A": product("A", materials=("塑料",), ships=("US",))})
    assert row["violations"] == {"budget": 1, "material": 1, "shipping": 1}
    summary = aggregate_cases([row])
    assert summary["constraint_case_counts"] == {"budget": 1, "material": 1, "shipping": 1}
    assert summary["hard_constraint_violation_rate"] == 1


def test_currency_mismatch_cannot_pass_budget():
    row = evaluate_case(query(price_max_major=100), [hit("A", price=1, currency="USD")], {"A": product("A")})
    assert row["violations"]["budget"] == 1


def test_no_coverage_is_unknown_not_zero_rate():
    summary = aggregate_cases([evaluate_case(query(), [], {})])
    assert summary["hard_constraint_violation_rate"] is None


def test_summary_is_recomputed_and_false_improvement_rejected():
    baseline, candidate = report("baseline"), report("candidate")
    candidate["summary"] = {"ndcg_at_5": 1, "returned_precision": 1}
    assert validate_report(candidate)["summary"]["ndcg_at_5"] == .4
    assert compare_reports(baseline, candidate)["status"] == "blocked"


def test_frozen_gates_can_pass_complete_formal_evidence():
    result = compare_reports(report("baseline"), report("candidate", quality=.6))
    assert result["status"] == "passed"
    assert result["comparable"]
    assert set(result["cases"][0]) == {"id", "kind", "baseline_ndcg", "candidate_ndcg", "delta", "violation_count"}


@pytest.mark.parametrize("field", ["catalog_sha256", "dataset_sha256", "selection_sha256", "embedding_identity", "query_mode", "split", "k", "repetitions"])
def test_changed_comparison_condition_is_not_comparable(field):
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    candidate["provenance"][field] = "changed"
    assert compare_reports(baseline, candidate)["status"] == "not_comparable"


def test_code_and_algorithm_config_can_change_but_need_fingerprints():
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    candidate["provenance"]["source_sha256"] = "f"*64
    candidate["provenance"]["config_sha256"] = "1"*64
    assert compare_reports(baseline, candidate)["status"] == "passed"
    candidate["provenance"].pop("source_sha256")
    assert compare_reports(baseline, candidate)["status"] == "not_comparable"


def test_same_standard_id_with_changed_threshold_is_not_same_standard():
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    changed = deepcopy(load_standard())
    changed["gates"]["ndcg_at_5"]["min_delta"] = 0
    assert compare_reports(baseline, candidate, changed)["status"] == "not_comparable"


def test_different_evaluator_cannot_claim_algorithm_improvement():
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    candidate["provenance"]["evaluator_sha256"] = "b"*64
    assert compare_reports(baseline, candidate)["status"] == "not_comparable"


@pytest.mark.parametrize("split", ["all", "dev"])
def test_formal_report_cannot_use_development_or_combined_selection(split):
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    baseline["provenance"]["split"] = candidate["provenance"]["split"] = split
    result = compare_reports(baseline, candidate)
    assert result["status"] == "blocked"
    assert any("holdout" in reason for reason in result["reasons"])
    baseline["scope"] = candidate["scope"] = "diagnostic"
    assert compare_reports(baseline, candidate)["status"] == "diagnostic"


@pytest.mark.parametrize("change", ["count", "empty", "duplicate", "missing", "nan", "invalid-id"])
def test_incomplete_or_corrupt_case_evidence_never_passes(change):
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    if change == "count":
        candidate["provenance"]["case_count"] = 3
    elif change == "empty":
        candidate["cases"] = []
    elif change == "duplicate":
        candidate["cases"][1]["id"] = candidate["cases"][0]["id"]
    elif change == "missing":
        candidate["cases"][0].pop("returned_precision")
    elif change == "nan":
        candidate["cases"][0]["ndcg_at_5"] = float("nan")
    else:
        candidate["cases"][0]["id"] = "not safe raw content"
    assert compare_reports(baseline, candidate)["status"] == "not_comparable"


def test_missing_material_coverage_and_only_empty_budget_cannot_pass():
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    for current in (baseline, candidate):
        current["cases"][0]["applicable_constraints"]["budget"] = False
        for case in current["cases"]:
            case["applicable_constraints"]["material"] = False
    result = compare_reports(baseline, candidate)
    assert result["status"] == "blocked"
    assert any("预算" in reason for reason in result["reasons"])
    assert any("材质" in reason for reason in result["reasons"])


@pytest.mark.parametrize("flag", ["degraded", "error", "status", "scope"])
def test_error_degradation_not_run_and_diagnostic_cannot_pass(flag):
    baseline, candidate = report("baseline"), report("candidate", quality=.6)
    if flag == "degraded":
        candidate["cases"][0][flag] = True
    elif flag == "error":
        candidate["cases"][0][flag] = "Timeout"
    elif flag == "status":
        candidate["status"] = "not_run"
    else:
        candidate["scope"] = "diagnostic"
    result = compare_reports(baseline, candidate)
    assert result["status"] in {"blocked", "pending", "diagnostic"}


def test_writes_never_overwrite_existing_evidence(tmp_path):
    target = tmp_path / "report.json"
    _write_new(target, {"original": True})
    with pytest.raises(FileExistsError):
        _write_new(target, {"original": False})
    assert 'true' in target.read_text(encoding="utf-8")


def test_standard_freezes_engineering_targets_without_claiming_results():
    standard = load_standard()
    assert standard["gates"]["ndcg_at_5"]["min_delta"] == .02
    assert standard["gates"]["returned_precision"]["min_delta"] == .02
    assert standard["gates"]["recall_at_5"]["min_delta"] == -.01
    assert standard["gates"]["hard_constraint_violations"]["min_positive_cases_per_dimension"] == 1
