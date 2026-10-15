"""只读评测视图的鉴权、证据读取边界和计分合同；数字仅是测试夹具。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.infrastructure.identity import IdentityPolicy
from app.infrastructure.retrieval_evaluation_store import EvaluationReadError, RetrievalEvaluationStore
from app.presentation.retrieval_evaluation import register_retrieval_evaluation_routes


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _provenance() -> dict:
    from app.application.retrieval_evaluation import load_standard, standard_fingerprint

    return {
        "catalog_sha256": "a" * 64, "dataset_sha256": "b" * 64,
        "selection_sha256": "c" * 64, "source_sha256": "d" * 64,
        "config_sha256": "e" * 64, "embedding_identity": "none:lexical",
        "standard_sha256": standard_fingerprint(load_standard()), "evaluator_sha256": "a" * 64,
        "query_mode": "structured_spec", "k": 5, "split": "holdout", "repetitions": 1,
        "catalog_count": 10, "case_count": 2,
    }


def _report(role: str, *, scope: str = "formal", score: float = .5) -> dict:
    return {
        "id": role, "label": "仅测试夹具", "protocol": "retrieval-standard-v1", "status": "completed",
        "scope": scope, "role": role, "created_at": "2026-10-14T12:00:00Z",
        "standard_id": "retrieval-standard-v1", "variant": "test-only", "provenance": _provenance(),
        "summary": {"ndcg_at_5": 999}, "query": "private-example-do-not-expose",
        "cases": [
            {"id": "positive", "family": "case-family", "kind": "positive", "ndcg_at_5": score,
             "recall_at_5": score, "returned_precision": score, "empty_ok": None,
             "violations": {"budget": 0, "material": 0, "shipping": 0},
             "applicable_constraints": {"budget": True, "material": True, "shipping": True},
             "returned_count": 2, "elapsed_ms": 1, "error": None, "degraded": False,
             "query": "private-example-do-not-expose"},
            {"id": "empty", "family": "case-family", "kind": "empty", "ndcg_at_5": None,
             "recall_at_5": None, "returned_precision": None, "empty_ok": True,
             "violations": {"budget": 0, "material": 0, "shipping": 0},
             "applicable_constraints": {"budget": False, "material": False, "shipping": False},
             "returned_count": 0, "elapsed_ms": 1, "error": None, "degraded": False},
        ],
    }


def _fixture(tmp_path: Path) -> FastAPI:
    standard_path = Path(__file__).resolve().parents[1] / "eval/retrieval/standard-v1.json"
    _write(tmp_path / "eval/retrieval/standard-v1.json", json.loads(standard_path.read_text(encoding="utf-8")))
    _write(tmp_path / "eval/retrieval/baseline-v1.json", {
        "id": "frozen-baseline", "protocol": "retrieval-standard-v1", "status": "frozen",
        "created_at": "2026-10-14T12:00:00Z", "provenance": _provenance(),
        "selection": {"case_ids": ["positive", "empty"], "split": "holdout", "repetitions": 1},
        "source_files": {"E:/secret/local.py": "d" * 64}, "note": "private-example-do-not-expose",
    })
    api = FastAPI()
    register_retrieval_evaluation_routes(api, tmp_path)
    return api


def _add_report(tmp_path: Path, identifier: str, report: dict) -> None:
    _write(tmp_path / "eval/verification/retrieval-standard" / identifier / "report.json", report)


async def test_missing_measurements_are_pending_and_frozen_definition_is_not_success(tmp_path):
    api = _fixture(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/commerce/retrieval-evaluation")
    assert response.status_code == 200
    result = response.json()
    assert result["reports"] == []
    assert result["comparison"]["status"] == "pending"
    assert result["baseline_definition"]["status"] == "frozen"
    assert "private-example" not in response.text and "E:/secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_compare_recomputes_summary_and_does_not_return_queries(tmp_path):
    api = _fixture(tmp_path)
    _add_report(tmp_path, "baseline", _report("baseline"))
    candidate = _report("candidate", score=.6)
    # 允许代码与配置有差异，不能强制优化前后源码相同。
    candidate["provenance"]["source_sha256"] = "f" * 64
    candidate["provenance"]["config_sha256"] = "0" * 64
    _add_report(tmp_path, "candidate", candidate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/commerce/retrieval-evaluation?baseline_id=baseline&candidate_id=candidate")
    assert response.status_code == 200
    result = response.json()
    assert result["comparison"]["status"] == "passed"
    assert result["comparison"]["comparable"] is True
    assert result["reports"][0]["summary"]["ndcg_at_5"] == .5
    assert "private-example" not in response.text
    assert "cases" not in result["reports"][0]
    assert set(result["comparison"]["cases"][0]) == {
        "id", "kind", "baseline_ndcg", "candidate_ndcg", "delta", "violation_count"
    }


async def test_diagnostic_scores_never_become_formal_pass(tmp_path):
    api = _fixture(tmp_path)
    definition_path = tmp_path / "eval/retrieval/baseline-v1.json"
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    definition["provenance"]["embedding_identity"] = "current-dense-model"
    _write(definition_path, definition)
    _add_report(tmp_path, "baseline", _report("baseline", scope="diagnostic"))
    _add_report(tmp_path, "candidate", _report("candidate", scope="diagnostic", score=.9))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation?baseline_id=baseline&candidate_id=candidate")).json()
    assert result["comparison"]["status"] == "diagnostic"


@pytest.mark.parametrize("field,replacement", [
    ("catalog_sha256", "f" * 64), ("dataset_sha256", "f" * 64),
    ("selection_sha256", "f" * 64), ("source_sha256", "f" * 64),
    ("embedding_identity", "another-embedding"), ("query_mode", "another-query-mode"),
    ("k", 4), ("split", "dev"), ("repetitions", 2),
])
async def test_formal_pair_must_match_current_frozen_baseline(tmp_path, field, replacement):
    api = _fixture(tmp_path)
    baseline, candidate = _report("baseline"), _report("candidate", score=.9)
    # 两份报告彼此相同，仍不能冒充当前冻结 B0 的优化收益。
    baseline["provenance"][field] = replacement
    candidate["provenance"][field] = replacement
    _add_report(tmp_path, "baseline", baseline)
    _add_report(tmp_path, "candidate", candidate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
    comparison = result["comparison"]
    assert comparison["status"] == "not_comparable"
    assert comparison["comparable"] is False
    assert comparison["metrics"] == []
    assert any("与当前冻结 B0 不一致" in reason and field in reason for reason in comparison["reasons"])


async def test_formal_pair_cannot_pass_without_current_frozen_definition(tmp_path):
    api = _fixture(tmp_path)
    (tmp_path / "eval/retrieval/baseline-v1.json").unlink()
    _add_report(tmp_path, "baseline", _report("baseline"))
    _add_report(tmp_path, "candidate", _report("candidate", score=.9))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
    assert result["comparison"]["status"] == "not_comparable"
    assert any("缺少当前冻结 B0" in reason for reason in result["comparison"]["reasons"])


async def test_defaults_use_measured_pair_and_keep_formal_pending_separate(tmp_path):
    api = _fixture(tmp_path)
    pending = _report("baseline")
    pending.update(status="not_run", cases=[], created_at="2026-10-14T10:00:00Z")
    _add_report(tmp_path, "formal-pending", pending)
    _add_report(tmp_path, "diagnostic-baseline", _report("baseline", scope="diagnostic"))
    _add_report(tmp_path, "diagnostic-candidate", _report("candidate", scope="diagnostic", score=.9))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
        pending_result = (await client.get("/commerce/retrieval-evaluation", params={
            "baseline_id": "formal-pending", "candidate_id": "diagnostic-candidate"
        })).json()
    assert result["selected_baseline_id"] == "diagnostic-baseline"
    assert result["selected_candidate_id"] == "diagnostic-candidate"
    assert result["comparison"]["status"] == "diagnostic"
    assert pending_result["comparison"]["status"] == "pending"
    assert next(row for row in result["reports"] if row["id"] == "formal-pending")["summary"]["ndcg_at_5"] is None


def _dated_report(role: str, minute: int, *, query_mode="structured_spec", status="completed") -> dict:
    report = _report(role, scope="diagnostic")
    report.update(created_at=f"2026-10-14T12:{minute:02d}:00Z", status=status)
    report["provenance"]["query_mode"] = query_mode
    return report


async def test_defaults_select_latest_completed_comparable_pair_not_earliest_baseline(tmp_path):
    api = _fixture(tmp_path)
    for identifier, report in [
        ("old-baseline", _dated_report("baseline", 1)),
        ("old-candidate", _dated_report("candidate", 2)),
        ("new-baseline-first", _dated_report("baseline", 10, query_mode="raw_query")),
        ("new-baseline-latest", _dated_report("baseline", 11, query_mode="raw_query")),
        ("new-candidate", _dated_report("candidate", 12, query_mode="raw_query")),
        ("unmatched-newest", _dated_report("candidate", 15, query_mode="other_query")),
        ("failed-newest", _dated_report("candidate", 16, status="failed")),
    ]:
        _add_report(tmp_path, identifier, report)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
    assert (result["selected_baseline_id"], result["selected_candidate_id"]) == ("new-baseline-latest", "new-candidate")
    assert result["comparison"]["comparable"] is True
    assert result["comparison"]["status"] == "diagnostic"


@pytest.mark.parametrize("field,replacement", [
    ("catalog_sha256", "1" * 64), ("dataset_sha256", "2" * 64),
    ("selection_sha256", "3" * 64), ("evaluator_sha256", "4" * 64),
    ("embedding_identity", "different-model"), ("query_mode", "raw_query"),
    ("split", "dev"), ("repetitions", 2),
])
async def test_default_candidate_requires_all_comparison_conditions(tmp_path, field, replacement):
    api = _fixture(tmp_path)
    _add_report(tmp_path, "baseline", _dated_report("baseline", 1))
    _add_report(tmp_path, "matching", _dated_report("candidate", 2))
    different = _dated_report("candidate", 3)
    different["provenance"][field] = replacement
    _add_report(tmp_path, "different-newest", different)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
    assert result["selected_candidate_id"] == "matching"
    assert result["comparison"]["comparable"] is True


@pytest.mark.parametrize("params,expected", [
    ({"baseline_id": "old-baseline"}, ("old-baseline", "old-candidate")),
    ({"candidate_id": "old-candidate"}, ("old-baseline", "old-candidate")),
    ({"baseline_id": "old-baseline", "candidate_id": "new-candidate"}, ("old-baseline", "new-candidate")),
])
async def test_explicit_selection_is_preserved_and_only_missing_side_is_matched(tmp_path, params, expected):
    api = _fixture(tmp_path)
    for identifier, report in [
        ("old-baseline", _dated_report("baseline", 1)),
        ("old-candidate", _dated_report("candidate", 2)),
        ("new-baseline", _dated_report("baseline", 3, query_mode="raw_query")),
        ("new-candidate", _dated_report("candidate", 4, query_mode="raw_query")),
    ]:
        _add_report(tmp_path, identifier, report)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation", params=params)).json()
    assert (result["selected_baseline_id"], result["selected_candidate_id"]) == expected
    assert result["comparison"]["comparable"] is (len(params) == 1)
    if len(params) == 2:
        assert result["comparison"]["status"] == "not_comparable"


async def test_no_comparable_pair_keeps_safe_mismatch_display(tmp_path):
    api = _fixture(tmp_path)
    _add_report(tmp_path, "baseline", _dated_report("baseline", 1))
    _add_report(tmp_path, "different", _dated_report("candidate", 2, query_mode="different_query"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation")).json()
    assert (result["selected_baseline_id"], result["selected_candidate_id"]) == ("baseline", "different")
    assert result["comparison"]["status"] == "not_comparable"


@pytest.mark.parametrize("parameters,expected", [
    ({"latency_scope": "replay_after_frozen_real_retrieval", "external_services": False}, "replay_after_frozen_real_retrieval"),
    ({"latency_scope": "offline"}, "offline"),
    ({"external_services": False}, "offline"),
    ({"latency_scope": "untrusted_value", "external_services": True}, "unknown"),
    ({}, "unknown"),
])
async def test_public_latency_scope_is_allowlisted_without_private_parameters(tmp_path, parameters, expected):
    api = _fixture(tmp_path)
    report = _dated_report("baseline", 1)
    report["parameters_safe"] = {**parameters, "private_api_key": "private-example-do-not-expose"}
    _add_report(tmp_path, "baseline", report)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/commerce/retrieval-evaluation")
    result = response.json()
    assert result["reports"][0]["latency_scope"] == expected
    assert result["reports"][0]["scope"] == "diagnostic"
    assert "parameters_safe" not in result["reports"][0]
    assert "private-example" not in response.text


async def test_strict_mode_requires_authorized_metrics_reader(tmp_path):
    api = _fixture(tmp_path)
    policy = IdentityPolicy(mode="hmac", secret="evaluation-test-secret-at-least-32-bytes")
    api.state.identity_policy = policy
    api.state.metrics_reader_buyers = ("operator",)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get("/commerce/retrieval-evaluation")).status_code == 401
        assert (await client.get("/commerce/retrieval-evaluation", headers={
            "Authorization": "Bearer " + policy.issue("buyer")
        })).status_code == 403
        assert (await client.get("/commerce/retrieval-evaluation", headers={
            "Authorization": "Bearer " + policy.issue("operator")
        })).status_code == 200
        api.state.metrics_reader_buyers = ()
        assert (await client.get("/commerce/retrieval-evaluation", headers={
            "Authorization": "Bearer " + policy.issue("operator")
        })).status_code == 404


@pytest.mark.parametrize("identifier", ["../outside", "..", "C:\\private", "/etc/passwd", "a/b", "a%2fb"])
async def test_path_traversal_and_unknown_reports_are_rejected(tmp_path, identifier):
    api = _fixture(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get("/commerce/retrieval-evaluation", params={"baseline_id": identifier})).status_code == 422
        assert (await client.get("/commerce/retrieval-evaluation?baseline_id=unknown")).status_code == 404


async def test_invalid_evidence_warns_without_exposing_original_content(tmp_path):
    api = _fixture(tmp_path)
    path = tmp_path / "eval/verification/retrieval-standard/broken/report.json"
    path.parent.mkdir(parents=True)
    path.write_text("private-example-do-not-expose", encoding="utf-8")
    invalid = _report("baseline")
    del invalid["cases"][0]["ndcg_at_5"]
    _add_report(tmp_path, "invalid", invalid)
    malformed = _report("baseline")
    malformed["cases"] = [None]
    _add_report(tmp_path, "invalid-shape", malformed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/commerce/retrieval-evaluation")
    assert response.status_code == 200
    assert response.json()["reports"] == []
    assert len(response.json()["warnings"]) == 3
    assert "private-example" not in response.text


async def test_changed_catalog_is_not_comparable(tmp_path):
    api = _fixture(tmp_path)
    _add_report(tmp_path, "baseline", _report("baseline"))
    candidate = _report("candidate", score=.9)
    candidate["provenance"]["catalog_sha256"] = "f" * 64
    _add_report(tmp_path, "candidate", candidate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/commerce/retrieval-evaluation?baseline_id=baseline&candidate_id=candidate")).json()
    assert result["comparison"]["status"] == "not_comparable"


def test_linked_report_directory_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "report.json", _report("baseline"))
    directory = tmp_path / "eval/verification/retrieval-standard"
    directory.mkdir(parents=True)
    try:
        (directory / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("此Windows账户没有创建符号链接权限")
    reports, warnings = RetrievalEvaluationStore(tmp_path).reports()
    assert reports == [] and any("链接" in warning for warning in warnings)


def test_store_rejects_oversized_json(tmp_path, monkeypatch):
    path = tmp_path / "eval/retrieval/standard-v1.json"
    _write(path, {"oversized": "x" * 100})
    monkeypatch.setattr("app.infrastructure.retrieval_evaluation_store.MAX_JSON_BYTES", 64)
    with pytest.raises(EvaluationReadError, match="大小限制"):
        RetrievalEvaluationStore(tmp_path).standard()


def test_store_bounds_number_of_scanned_directories(tmp_path, monkeypatch):
    _add_report(tmp_path, "first", _report("baseline"))
    _add_report(tmp_path, "second", _report("candidate"))
    monkeypatch.setattr("app.infrastructure.retrieval_evaluation_store.MAX_REPORTS", 1)
    reports, warnings = RetrievalEvaluationStore(tmp_path).reports()
    assert len(reports) == 1
    assert any("上限" in warning for warning in warnings)
