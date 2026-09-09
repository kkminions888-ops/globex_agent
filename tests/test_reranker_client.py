# -*- coding: utf-8 -*-
"""Reranker HTTP 契约回归。"""
from __future__ import annotations

import pytest

from app.infrastructure.rerank import http_reranker
from app.infrastructure.rerank.http_reranker import HttpReranker
from app.infrastructure.settings import load_settings


@pytest.mark.asyncio
async def test_full_reranker_endpoint_is_used_with_gateway_authorization(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.1},
                ],
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, **kwargs):
            captured.update(url=url, **kwargs)
            return FakeResponse()

    monkeypatch.setenv("LLM_API_KEY", "test-gateway-key")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "RERANKER_BASE_URL",
        "https://1688openai.alibaba-inc.com/v1/services/reranker",
    )
    monkeypatch.setenv("RERANKER_MODEL", "qwen-text-rerank")
    monkeypatch.setattr(http_reranker.httpx, "AsyncClient", lambda **_: FakeClient())

    scores = await HttpReranker(load_settings()).rerank(
        "轻便旅行背包",
        ["20L 轻量旅行背包", "陶瓷咖啡杯"],
    )

    assert scores == [0.9, 0.1]
    assert captured["url"] == "https://1688openai.alibaba-inc.com/v1/services/reranker"
    assert captured["headers"] == {"Authorization": "Bearer test-gateway-key"}
    assert captured["json"] == {
        "model": "qwen-text-rerank",
        "query": "轻便旅行背包",
        "documents": ["20L 轻量旅行背包", "陶瓷咖啡杯"],
    }
