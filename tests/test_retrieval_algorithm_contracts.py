# -*- coding: utf-8 -*-
"""商品检索改进的业务合同：以可核对规格、价格和资格验证结果。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agentscope.tool import FunctionTool

from app.application.tools.product_search_tool import build_product_search_tool
from app.application.usecases.catalog_search import CatalogSearchUseCase
from app.domain.catalog.money import Money
from app.domain.catalog.ports.retrieval_ports import VectorHit
from app.domain.catalog.product import Product
from app.domain.catalog.product_search_spec import ProductSearchSpec
from app.domain.catalog.sku import Sku
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository


def _product(number: int = 1001, *, title="旅行背包", canonical="", weight=0.4,
             kind=None, skus=None, description="轻便出行") -> Product:
    identifier = f"P{number}"
    return Product(
        product_id=identifier, title=title, brand="Trail", category="旅行装备",
        origin_country="CN", description=description, ships_to=["CN", "US"],
        canonical_product_id=canonical, weight_kg=weight, product_type=kind,
        skus=skus or [_sku(identifier, 1, "黑色 20L", 99)],
    )


def _sku(product_id, number, spec, price, *, currency="CNY", stock=8):
    return Sku(f"{product_id}-S{number}", spec, Money.from_major_units(price, currency), stock)


def _search(products, *, path="keyword", **options):
    repo = InMemoryProductRepository(products)
    index = SimpleNamespace(search=AsyncMock(side_effect=lambda embedding, top_n: [
        VectorHit(item.product_id, 1 / (position + 1))
        for position, item in enumerate(products[:top_n])
    ]))
    if path in {"dense", "hybrid"}:
        options.update(embedder=SimpleNamespace(embed=AsyncMock(return_value=[1])), vector_index=index)
    if path == "hybrid":
        options["hybrid_enabled"] = True
    return CatalogSearchUseCase(repo, **options), index


@pytest.mark.parametrize("path", ["keyword", "dense", "hybrid"])
async def test_non_primary_eligible_sku_controls_displayed_and_landed_prices(path):
    item = _product(skus=[
        _sku("P1001", 1, "红色 30L", 900),
        _sku("P1001", 2, "蓝色 20L", 20, currency="USD"),
        _sku("P1001", 3, "黑色 20L", 1, stock=0),
    ])
    usecase, _ = _search([item], path=path)
    result = await usecase.execute(ProductSearchSpec(
        normalized_query="背包", price_max_major=200, ship_to="CN", target_currency="CNY",
    ))
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["default_sku_id"] == "P1001-S2"
    assert hit["eligible_sku_ids"] == ["P1001-S2"]
    assert (hit["source_price_major"], hit["source_currency"]) == (20, "USD")
    assert (hit["price_major"], hit["currency"]) == (142, "CNY")
    assert hit["landed_price"]["subtotal_major"] == 142
    assert hit["landed_price"]["landed_total_major"] == 167


@pytest.mark.parametrize("path", ["keyword", "dense", "hybrid"])
async def test_specification_and_budget_must_be_satisfied_by_same_available_sku(path):
    item = _product(skus=[
        _sku("P1001", 1, "红色 20L", 99),
        _sku("P1001", 2, "黑色 20L", 400),
        _sku("P1001", 3, "黑色 20L", 99, stock=0),
    ])
    usecase, _ = _search([item], path=path)
    result = await usecase.execute(ProductSearchSpec(
        normalized_query="背包", sku_spec="黑色 20L", price_max_major=200,
    ))
    assert result["hits"] == []
    assert result["filtered_out"][0]["reason"] == "over_price_cap"


@pytest.mark.parametrize("query", ["20L", "２０Ｌ", "black 20L"])
async def test_numeric_sku_specification_is_a_whole_token(query):
    item = _product(skus=[
        _sku("P1001", 1, "black 120L", 70),
        _sku("P1001", 2, "black 20L", 90),
    ])
    usecase, _ = _search([item])
    result = await usecase.execute(ProductSearchSpec(normalized_query="背包", sku_spec=query))
    assert result["hits"][0]["eligible_sku_ids"] == ["P1001-S2"]
    assert result["hits"][0]["default_sku_id"] == "P1001-S2"


async def test_exact_sku_cannot_be_replaced_by_budget_eligible_sibling():
    item = _product(skus=[_sku("P1001", 1, "黑色 20L", 99), _sku("P1001", 2, "黑色 30L", 400)])
    usecase, _ = _search([item])
    result = await usecase.execute(ProductSearchSpec(product_id="P1001", sku_id="P1001-S2", price_max_major=200))
    assert result["hits"] == []
    assert result["filtered_out"][0]["sku_id"] == "P1001-S2"
    assert result["filtered_out"][0]["price_major"] == 400
    assert result["filtered_out"][0]["reason"] == "over_price_cap"


async def test_generic_product_id_uses_qualified_sku_and_exact_spec_stays_strict():
    item = _product(skus=[_sku("P1001", 1, "红色 30L", 400), _sku("P1001", 2, "黑色 20L", 99)])
    usecase, _ = _search([item])
    result = await usecase.execute(ProductSearchSpec(product_id="P1001", price_max_major=200, sku_spec="黑色 20L"))
    assert result["hits"][0]["default_sku_id"] == "P1001-S2"
    assert result["hits"][0]["price_major"] == 99
    rejected = await usecase.execute(ProductSearchSpec(sku_id="P1001-S1", sku_spec="黑色 20L"))
    assert rejected["hits"] == []
    assert rejected["filtered_out"][0]["reason"] == "sku_spec_unavailable"


@pytest.mark.parametrize("path", ["keyword", "dense", "hybrid"])
async def test_all_recall_paths_remove_canonical_duplicates_before_top_k(path):
    items = [_product(1001, canonical="CAN-A"), _product(1002, canonical="CAN-A"),
             _product(1003, canonical="CAN-B"), _product(1004, canonical="CAN-C")]
    usecase, _ = _search(items, path=path)
    result = await usecase.execute(ProductSearchSpec(normalized_query="背包", top_k=3))
    assert [hit["canonical_product_id"] for hit in result["hits"]] == ["CAN-A", "CAN-B", "CAN-C"]
    assert result["total_candidates"] == 3


@pytest.mark.parametrize("path,filtered_index", [("dense", False), ("hybrid", False), ("hybrid", True)])
async def test_dense_refill_counts_unique_canonical_products(path, filtered_index):
    items = [_product(1001 + index, canonical="CAN-SAME") for index in range(8)]
    items.append(_product(1009, canonical="CAN-OTHER"))
    usecase, index = _search(items, path=path, recall_candidates=8)
    if filtered_index:
        index.search_filtered = AsyncMock(side_effect=lambda embedding, top_n, product_ids: [
            VectorHit(item.product_id, 1 / (position + 1))
            for position, item in enumerate(items[:top_n]) if item.product_id in product_ids
        ])
    result = await usecase.execute(ProductSearchSpec(normalized_query="unmatched123", top_k=2))
    assert [hit["canonical_product_id"] for hit in result["hits"]] == ["CAN-SAME", "CAN-OTHER"]
    calls = index.search_filtered.await_args_list if filtered_index else index.search.await_args_list
    assert [call.kwargs["top_n"] for call in calls] == [8, 16]
    if filtered_index:
        index.search.assert_not_awaited()


@pytest.mark.parametrize("path", ["keyword", "dense", "hybrid"])
async def test_strict_type_and_weight_reject_unknown_facts(path):
    items = [
        _product(1001, weight=0.3), _product(1002, weight=0.8),
        _product(1003, weight=0), _product(1004, weight=0.2, title="旅行颈枕", description="可装入背包"),
        _product(1005, weight=0.2, title="旅行用品", description="可装入背包"),
    ]
    usecase, _ = _search(items, path=path)
    result = await usecase.execute(ProductSearchSpec(
        normalized_query="背包", product_type="背包", max_weight_kg=0.5,
    ))
    assert [hit["product_id"] for hit in result["hits"]] == ["P1001"]
    assert result["hits"][0]["known_weight_kg"] == 0.3
    # filtered_out 受体积上限限制；逐个精确查证，保证未知类型和未知重量各自有理由。
    for number, reason in [(1002, "over_weight_limit"), (1003, "weight_unknown"),
                           (1004, "product_type_mismatch"), (1005, "product_type_unknown")]:
        rejected = await usecase.execute(ProductSearchSpec(
            product_id=f"P{number}", product_type="背包", max_weight_kg=0.5,
        ))
        assert rejected["hits"] == []
        assert rejected["filtered_out"][0]["reason"] == reason


@pytest.mark.parametrize("path", ["keyword", "dense", "hybrid"])
async def test_soft_type_and_lightweight_preferences_reorder_without_excluding(path):
    items = [_product(1001, title="旅行颈枕", weight=0.1, description="旅行用品"),
             _product(1002, weight=0, description="旅行用品"),
             _product(1003, weight=0.4, description="旅行用品"),
             _product(1004, weight=0.2, description="旅行用品")]
    usecase, _ = _search(items, path=path)
    result = await usecase.execute(ProductSearchSpec(
        normalized_query="旅行", top_k=4, preferred_product_type="背包", prefer_lightweight=True,
    ))
    assert [hit["product_id"] for hit in result["hits"]] == ["P1004", "P1003", "P1002", "P1001"]
    assert result["total_candidates"] == 4
    assert not result.get("filtered_out")


async def test_default_uncalibrated_relevance_threshold_does_not_drop_weak_dense_hits():
    items = [_product(1001), _product(1002)]
    usecase, index = _search(items, path="dense")
    index.search.return_value = [VectorHit("P1001", 0.01), VectorHit("P1002", -0.4)]
    index.search.side_effect = None
    result = await usecase.execute(ProductSearchSpec(normalized_query="unmatched123", top_k=2))
    assert len(result["hits"]) == 2
    assert result["retrieval_diagnostics"]["relevance_filter_applied"] is False
    assert result["retrieval_diagnostics"]["low_relevance_removed"] == 0


@pytest.mark.parametrize("path, bound", [("dense", "reranker"), ("hybrid", "embedding")])
async def test_relevance_threshold_cannot_cross_score_types(path, bound):
    usecase, _ = _search([_product(1001), _product(1002)], path=path,
                         min_relevance_score=99, relevance_score_kind=bound)
    result = await usecase.execute(ProductSearchSpec(normalized_query="背包", top_k=2))
    assert len(result["hits"]) == 2
    assert result["retrieval_diagnostics"]["relevance_filter_skipped"] == "score_kind_mismatch"
    assert result["retrieval_diagnostics"]["relevance_filter_applied"] is False


async def test_calibrated_threshold_returns_fewer_cards_and_explains_shortfall():
    items = [_product(1001), _product(1002), _product(1003)]
    usecase, index = _search(items, path="dense", min_relevance_score=0.5, relevance_score_kind="embedding")
    index.search.side_effect = None
    index.search.return_value = [VectorHit("P1001", 0.9), VectorHit("P1002", 0.4), VectorHit("P1003", 0.2)]
    result = await usecase.execute(ProductSearchSpec(normalized_query="unmatched123", top_k=3))
    assert [hit["product_id"] for hit in result["hits"]] == ["P1001"]
    assert result["retrieval_diagnostics"]["relevance_filter_applied"] is True
    assert result["retrieval_diagnostics"]["low_relevance_removed"] == 2
    assert result["shortfall"] == {"requested": 3, "returned": 1, "reason": "low_relevance"}


def test_search_tool_schema_exposes_constraints_and_soft_preferences():
    tool = build_product_search_tool(SimpleNamespace(execute=AsyncMock()), TradeEventBus())
    properties = FunctionTool(tool).input_schema["properties"]
    assert {"sku_spec", "product_type", "max_weight_kg", "preferred_product_type", "prefer_lightweight"} <= properties.keys()
    assert {variant["type"] for variant in properties["max_weight_kg"]["anyOf"]} == {"number", "string", "null"}


async def test_tool_forwards_new_arguments_and_coerces_numeric_weight_string():
    execute = AsyncMock(return_value={"hits": [], "total_candidates": 0, "recall_strategy": "keyword_2gram", "rerank_applied": False})
    tool = build_product_search_tool(SimpleNamespace(execute=execute), TradeEventBus())
    token = ShoppingContext.set(ShoppingContextSnapshot("synthetic-contract", "synthetic-buyer", "zh-CN", "CNY"))
    try:
        response = await tool(
            normalized_query="户外背包", sku_spec="黑色 20L", product_type="daypack",
            max_weight_kg="0.5", preferred_product_type="backpack", prefer_lightweight=True,
        )
    finally:
        ShoppingContext.reset(token)
    assert not response.content[0].text.startswith("[error]")
    spec = execute.await_args.args[0]
    assert spec.product_type == spec.preferred_product_type == "背包"
    assert spec.sku_spec == "黑色 20L" and spec.max_weight_kg == 0.5 and spec.prefer_lightweight is True
    assert spec.category is None  # 户外字样不擅自限制为户外运动一级类目。
    assert json.loads(response.content[0].text)["query_conditions"]["max_weight_kg"] == 0.5


@pytest.mark.parametrize("value", ["not-a-number", "nan", "inf", "0", "-1"])
async def test_tool_rejects_invalid_explicit_weight_without_searching(value):
    execute = AsyncMock()
    tool = build_product_search_tool(SimpleNamespace(execute=execute), TradeEventBus())
    response = await tool(normalized_query="背包", max_weight_kg=value)
    assert response.content[0].text.startswith("[error]")
    execute.assert_not_awaited()


async def test_equal_vector_scores_have_stable_product_id_order():
    usecase, index = _search([_product(1001), _product(1002)], path="dense")
    index.search.side_effect = None
    index.search.return_value = [VectorHit("P1002", 0.8), VectorHit("P1001", 0.8)]
    result = await usecase.execute(ProductSearchSpec(normalized_query="背包", top_k=2))
    assert [hit["product_id"] for hit in result["hits"]] == ["P1001", "P1002"]


def test_model_projection_preserves_unknown_weight_and_eligible_spec_evidence():
    from app.infrastructure.persistence.context_evidence import product_decision_view
    from app.infrastructure.context_products import business_view
    result = {"hits": [{"product_id": "P1001", "product_type": "背包", "weight_kg": 0,
                        "known_weight_kg": None, "eligible_sku_ids": ["P1001-S2"]}]}
    for projection in (product_decision_view, business_view):
        hit = projection(result)["hits"][0]
        assert hit["known_weight_kg"] is None
        assert hit["product_type"] == "背包"
        assert hit["eligible_sku_ids"] == ["P1001-S2"]
