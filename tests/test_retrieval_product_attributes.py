# -*- coding: utf-8 -*-
"""细分类型、重量与检索字段的领域契约；不依赖模型或运行时数据库。"""
from dataclasses import replace
import math

import pytest

from app.domain.catalog.money import Money
from app.domain.catalog.product import Product, ProductHighlight
from app.domain.catalog.product_attributes import infer_product_type, normalize_product_type
from app.domain.catalog.product_search_spec import ProductSearchSpec
from app.domain.catalog.sku import Sku
from app.infrastructure.persistence.seed_products import _product_from_record


def product(**kwargs):
    values = dict(
        product_id="P1001", title="Trail 旅行双肩包 35L", brand="Trail",
        category="旅行装备", origin_country="CN", description="适合飞机出行",
        highlights=[ProductHighlight("特点", "折叠")], material_tags=["尼龙"],
        skus=[Sku("P1001-S1", "黑色", Money.from_major_units(199, "CNY"), 10)],
    )
    values.update(kwargs)
    return Product(**values)


@pytest.mark.parametrize("raw, expected", [
    ("双肩包", "背包"), (" backpack ", "背包"), ("DAYPACK", "背包"),
    ("Neck Pillow", "颈枕"), ("u型枕", "颈枕"), ("旅行装备", None),
    ("未知类型", None), (None, None), (123, None),
])
def test_explicit_type_uses_known_aliases_only(raw, expected):
    assert normalize_product_type(raw) == expected


@pytest.mark.parametrize("title, category, expected", [
    ("Trail 旅行双肩包 35L", "旅行装备", "背包"),
    ("Foldaway Daypack 25L", "旅行装备", "背包"),
    ("旅行三件套（收纳袋+颈枕+眼罩）", "旅行装备", "旅行套装"),
    ("桑蚕丝旅行睡袋内胆", "旅行装备", "睡袋内胆"),
    ("轻便防水商品", "背包", "背包"),
    ("轻便防水商品", "旅行装备", None),
    ("背包挂件", "旅行装备", None),
    ("背包防雨罩", "旅行装备", None),
    ("背包专用挂饰", "旅行装备", None),
    ("颈枕套", "旅行装备", None),
    ("Backpack Cover", "旅行装备", None),
    ("Backpack Rain Cover", "旅行装备", None),
    ("颈枕+眼罩组合", "旅行装备", None),
    ("Chargerette 配件", "数码配件", None),
])
def test_legacy_type_inference_is_conservative(title, category, expected):
    assert infer_product_type(title, category) == expected


def test_description_is_never_a_product_type_source():
    item = product(title="轻便配件", description="可放入背包，与颈枕配套")
    assert item.effective_product_type is None


def test_explicit_product_type_takes_precedence_over_title():
    assert product(product_type="neck pillow").effective_product_type == "颈枕"
    assert product(product_type="未知类型").effective_product_type is None
    assert product(product_type="").effective_product_type == "背包"


@pytest.mark.parametrize("weight, expected", [
    (0.0, None), (0.38, 0.38), (float("nan"), None),
    (float("inf"), None), (True, None),
])
def test_unknown_weight_is_not_treated_as_lightweight(weight, expected):
    item = product(weight_kg=weight)
    assert item.known_weight_kg == expected
    if math.isnan(weight):
        assert math.isnan(item.weight_kg)
    else:
        assert item.weight_kg == weight


def test_negative_weight_preserves_existing_constructor_contract():
    with pytest.raises(ValueError, match="必须非负"):
        product(weight_kg=-0.1)


def test_field_search_does_not_change_embedding_text():
    item = product()
    old_text = item.searchable_text()
    enriched = replace(item, product_type="背包", model_spec={"value": "BP-X7", "unit": "35L"})
    assert enriched.searchable_text() == old_text
    assert enriched.searchable_fields() == {
        "title": item.title, "brand": "Trail", "model": "BP-X7 35L",
        "category": "旅行装备", "description": "适合飞机出行",
        "highlights": "特点 折叠", "material": "尼龙",
    }
    assert product().searchable_fields()["model"] == ""  # 黑色规格不能被当成型号。


def test_record_loader_preserves_explicit_type_model_and_unknown_weight():
    item = _product_from_record({
        "product_id": "P1001", "title": "Unknown", "brand": "Acme",
        "category": "旅行装备", "origin_country": "CN", "description": "Test",
        "product_type": "backpack", "model_spec": "BP-X7", "weight_kg": 0,
        "skus": [{"sku_id": "P1001-S1", "spec": "蓝色", "price_major": 100, "currency": "CNY", "stock": 3}],
    })
    assert item.effective_product_type == "背包"
    assert item.searchable_fields()["model"] == "BP-X7"
    assert item.known_weight_kg is None


def test_search_spec_normalizes_explicit_type_preferences_and_specification():
    spec = ProductSearchSpec(
        normalized_query="旅行包", product_type="daypack", preferred_product_type="双肩包",
        max_weight_kg=0.5, prefer_lightweight=True, sku_spec="  黑色  /  35L ",
    )
    assert spec.product_type == spec.preferred_product_type == "背包"
    assert spec.sku_spec == "黑色 / 35L"


@pytest.mark.parametrize("name, value", [
    ("product_type", "未知类型"), ("product_type", ""), ("product_type", 3),
    ("preferred_product_type", "旅行装备"), ("preferred_product_type", []),
    ("max_weight_kg", 0), ("max_weight_kg", -1), ("max_weight_kg", True),
    ("max_weight_kg", float("nan")), ("max_weight_kg", float("inf")), ("max_weight_kg", "0.5"),
    ("prefer_lightweight", 1), ("sku_spec", ""), ("sku_spec", "  "),
    ("sku_spec", "x" * 161), ("sku_spec", "黑色\n35L"), ("sku_spec", 3),
])
def test_new_explicit_search_constraints_are_validated(name, value):
    with pytest.raises(ValueError, match=name):
        ProductSearchSpec(normalized_query="商品", **{name: value})


def test_new_search_options_keep_existing_id_only_entry_compatible():
    spec = ProductSearchSpec(product_id="p1001")
    assert spec.product_id == "P1001"
    assert spec.product_type is None and spec.max_weight_kg is None
    assert spec.prefer_lightweight is False
