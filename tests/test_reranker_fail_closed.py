"""精排异常不得伪造分数、隐藏用量或暴露请求数据。"""
from dataclasses import replace
import json
import math

import httpx
import pytest

from app.infrastructure.context_usage import (
    context_call_kind, context_diagnostic_sink, context_usage_sink,
)
from app.infrastructure.rerank import http_reranker
from app.infrastructure.rerank.http_reranker import HttpReranker, RerankerServiceError
from app.infrastructure.settings import load_settings


def client_for(monkeypatch, *, body=None, error=None, status=200, captured=None):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            if captured is not None:
                captured.update(url=url, **kwargs)
            if error is not None:
                raise error
            return httpx.Response(status, content=json.dumps(body).encode(), request=httpx.Request("POST", url))

    def factory(**kwargs):
        if captured is not None:
            captured["client"] = kwargs
        return Client()
    monkeypatch.setattr(http_reranker.httpx, "AsyncClient", factory)


def settings(protocol="flat"):
    return replace(load_settings(), reranker_protocol=protocol,
                   reranker_base_url="https://rerank.invalid/private", reranker_api_key="test-only")


INVALID_RESULTS = [
    None, [], [{"index": 0, "score": .9}],
    [{"index": 0, "score": .9}, {"index": 0, "score": .1}],
    [{"index": True, "score": .9}, {"index": 1, "score": .1}],
    [{"index": -1, "score": .9}, {"index": 1, "score": .1}],
    [{"index": 0, "score": float("inf")}, {"index": 1, "score": .1}],
    [{"index": 0, "score": True}, {"index": 1, "score": .1}],
    [{"index": 0}, {"index": 1, "score": .1}],
]


@pytest.mark.parametrize("protocol", ["flat", "dashscope"])
@pytest.mark.parametrize("results", INVALID_RESULTS)
async def test_both_protocols_require_complete_finite_unique_scores(monkeypatch, protocol, results):
    body = {"results": results}
    if protocol == "dashscope":
        body = {"output": body}
    client_for(monkeypatch, body=body)
    with pytest.raises(RerankerServiceError) as error:
        await HttpReranker(settings(protocol)).rerank("公开测试背包", ["公开背包", "公开杯子"])
    assert error.value.code == "invalid_scores"


@pytest.mark.parametrize("output", [None, [], "sensitive-response"])
async def test_dashscope_output_type_is_checked_without_echoing_body(monkeypatch, output):
    client_for(monkeypatch, body={"output": output})
    with pytest.raises(RerankerServiceError) as error:
        await HttpReranker(settings("dashscope")).rerank("公开测试", ["公开商品"])
    assert error.value.code == "invalid_response"
    assert "sensitive-response" not in str(error.value)


@pytest.mark.parametrize("kind", ["timeout", "network", "http"])
async def test_transport_failure_is_safe_and_records_one_unknown_usage(monkeypatch, kind):
    request = httpx.Request("POST", "https://private.invalid/path?key=private-value")
    error = (httpx.ReadTimeout("private-value", request=request) if kind == "timeout"
             else httpx.ConnectError("private-value", request=request) if kind == "network" else None)
    captured, usage, diagnostics = {}, [], []
    client_for(monkeypatch, error=error, status=403 if kind == "http" else 200, captured=captured)
    usage_token = context_usage_sink.set(usage.append)
    diagnostic_token = context_diagnostic_sink.set(diagnostics.append)
    previous_kind = context_call_kind.get()
    try:
        with pytest.raises(RerankerServiceError) as failure:
            await HttpReranker(settings(), timeout_seconds=.75).rerank("公开测试", ["公开商品"])
        assert failure.value.code == {"timeout": "timeout", "network": "network_error", "http": "http_error"}[kind]
        assert "private" not in str(failure.value)
        assert failure.value.__cause__ is None
        assert context_call_kind.get() == previous_kind
    finally:
        context_usage_sink.reset(usage_token)
        context_diagnostic_sink.reset(diagnostic_token)
    assert captured["client"] == {"timeout": .75, "follow_redirects": False}
    assert len(usage) == len(diagnostics) == 1
    assert usage[0]["kind"] == "rerank"
    assert usage[0]["input_tokens"] is None
    assert diagnostics[0]["success"] is False
    assert diagnostics[0]["reported_total_tokens"] is None


async def test_observed_total_usage_is_not_fabricated_as_input_or_cost(monkeypatch):
    client_for(monkeypatch, body={"output": {"results": [{"index": 1, "score": .1}, {"index": 0, "score": .9}]},
                                  "usage": {"total_tokens": 27}})
    usage, diagnostics = [], []
    usage_token = context_usage_sink.set(usage.append)
    diagnostic_token = context_diagnostic_sink.set(diagnostics.append)
    try:
        scores = await HttpReranker(settings("dashscope")).rerank("公开测试", ["公开背包", "公开杯子", "公开背包"])
    finally:
        context_usage_sink.reset(usage_token)
        context_diagnostic_sink.reset(diagnostic_token)
    assert scores == [.9, .1, .9]
    assert len(usage) == len(diagnostics) == 1
    assert usage[0]["input_tokens"] is None
    assert diagnostics[0]["reported_total_tokens"] == 27
    assert diagnostics[0]["reported_cost"] is None
    assert diagnostics[0]["requested_documents"] == 3
    assert diagnostics[0]["unique_documents"] == 2
    assert diagnostics[0]["success"] is True
    assert diagnostics[0]["error_code"] is None
    assert math.isfinite(diagnostics[0]["elapsed_ms"])


async def test_usage_survives_invalid_scores_and_boolean_usage_stays_unknown(monkeypatch):
    client_for(monkeypatch, body={"results": [], "usage": {"input_tokens": True, "total_tokens": 17}})
    usage, diagnostics = [], []
    usage_token = context_usage_sink.set(usage.append)
    diagnostic_token = context_diagnostic_sink.set(diagnostics.append)
    try:
        with pytest.raises(RerankerServiceError):
            await HttpReranker(settings()).rerank("公开测试", ["公开商品"])
    finally:
        context_usage_sink.reset(usage_token)
        context_diagnostic_sink.reset(diagnostic_token)
    assert usage[0]["input_tokens"] is None
    assert diagnostics[0]["reported_total_tokens"] == 17
    assert diagnostics[0]["error_code"] == "invalid_scores"
