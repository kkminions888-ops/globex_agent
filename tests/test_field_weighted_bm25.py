"""字段检索实验的权重、型号边界和确定性契约。"""
from types import SimpleNamespace

import pytest

from app.infrastructure.retrieval.bm25 import field_weighted_bm25_rank


def product(product_id, **fields):
    return SimpleNamespace(product_id=product_id, searchable_fields=lambda: fields)


def test_title_and_brand_weights_outrank_description_mentions():
    products = [
        product("description", description="Sony 背包"),
        product("brand", brand="Sony"),
        product("title", title="背包"),
    ]
    assert [item.product_id for _, item in field_weighted_bm25_rank("Sony", products)] == [
        "brand", "description"
    ]
    assert [item.product_id for _, item in field_weighted_bm25_rank("背包", products)] == [
        "title", "description"
    ]


def test_explicit_fields_are_complete_configuration_and_can_disable_title():
    products = [product("A", title="背包"), product("B", material="背包")]
    ranked = field_weighted_bm25_rank("背包", products, {"material": 1})
    assert [item.product_id for _, item in ranked] == ["B"]


def test_model_keeps_whole_unicode_tokens_and_excludes_neighbour_model():
    products = [
        product("A", model="WH-1000XM4"),
        product("B", model="ＷＨ－１０００ＸＭ５"),
        product("C", model="WH-1000XM50"),
    ]
    assert [item.product_id for _, item in field_weighted_bm25_rank("WH-1000XM5", products)] == ["B"]
    assert field_weighted_bm25_rank("WH-1000XM", products) == []


def test_fields_have_independent_length_normalization_and_ties_use_product_id():
    products = [
        product("B", title="轻便背包", description="无关正文" * 1000),
        product("A", title="轻便背包", description="短文"),
    ]
    ranked = field_weighted_bm25_rank("轻便背包", products, {"title": 1})
    assert ranked[0][0] == ranked[1][0]
    assert [item.product_id for _, item in ranked] == ["A", "B"]
    assert field_weighted_bm25_rank("轻便背包", list(reversed(products)), {"title": 1}) == ranked


def test_field_order_does_not_change_scores():
    products = [product("A", title="旅行背包", description="轻便旅行背包")]
    assert field_weighted_bm25_rank("旅行背包", products, {"title": 3, "description": 1}) == (
        field_weighted_bm25_rank("旅行背包", products, {"description": 1, "title": 3})
    )


def test_subwords_remain_optional_without_splitting_numeric_models():
    products = [product("A", title="Wanderrucksack")]
    assert field_weighted_bm25_rank("Rucksack", products)
    assert field_weighted_bm25_rank("Rucksack", products, subword_weight=0) == []
    assert field_weighted_bm25_rank("WH-1000XM5", products) == []


@pytest.mark.parametrize("weights", [
    {}, {"unknown": 1}, {"title": 0}, {"title": -1}, {"title": float("nan")},
    {"title": float("inf")}, {"title": True}, {"title": "3"}, [1, 2],
])
def test_invalid_field_weights_are_rejected(weights):
    with pytest.raises(ValueError):
        field_weighted_bm25_rank("背包", [product("A", title="背包")], weights)


@pytest.mark.parametrize("parameters", [
    {"k1": 0}, {"k1": float("inf")}, {"b": -0.1}, {"b": 1.1},
    {"b": float("nan")}, {"subword_weight": -1}, {"subword_weight": True},
])
def test_invalid_scoring_parameters_are_rejected(parameters):
    with pytest.raises(ValueError):
        field_weighted_bm25_rank("背包", [], **parameters)


def test_empty_query_and_corpus_return_no_results():
    assert field_weighted_bm25_rank("", [product("A", title="背包")]) == []
    assert field_weighted_bm25_rank("背包", []) == []
