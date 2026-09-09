# -*- coding: utf-8 -*-
"""生成可复现的准生产演示商品集（500 SPU / 705 SKU）。

这是一次性数据构建工具，运行后得到应提交到仓库的 ``data/catalog-v1.jsonl``；
运行时只读取 JSONL，业务代码不再依赖硬编码商品表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.persistence.seed_products import _build_legacy_seed_products


_OUT = Path(__file__).resolve().parents[1] / "data" / "catalog-v1.jsonl"
_PLATFORMS = ("amazon", "ebay", "etsy", "walmart")
_CURRENCIES = ("CNY", "USD", "EUR", "JPY", "SGD")
_CATEGORIES = (
    ("旅行装备", "旅行收纳 轻便 出行", "旅行收纳包"),
    ("数码配件", "数码 快充 便携", "数码扩展坞"),
    ("家居生活", "家居 天然材质 轻器物", "家居收纳盒"),
    ("户外运动", "户外 防水 耐用 徒步", "户外保温杯"),
    ("美妆个护", "个护 低敏 旅行装", "旅行洗护套装"),
    ("厨房餐饮", "厨房 食品接触 便携", "便携餐盒"),
    ("办公学习", "办公 护眼 轻薄", "桌面收纳架"),
    ("母婴宠物", "母婴 宠物 安全 易清洁", "宠物出行包"),
)
_MATERIALS = (
    ("天然纤维", "帆布棉麻"),
    ("合成聚合物", "再生尼龙"),
    ("金属", "铝合金"),
    ("陶瓷", "高温陶瓷"),
    ("玻璃", "耐热玻璃"),
)
_ORIGINS = ("CN", "US", "DE", "JP", "KR", "VN", "PT", "SG")
_DESTINATIONS = (
    ["CN"], ["US"], ["EU"], ["JP"], ["SG"],
    ["CN", "US"], ["CN", "EU"], ["US", "EU", "JP"], ["CN", "US", "EU", "JP", "SG"],
)


def _legacy_record(product, index: int) -> dict:
    # “再生”不改变材料属性；记忆棉、超纤、PVC/PU 等同样不能标为天然材质。
    synthetic_markers = ("尼龙", "记忆棉", "超纤", "聚酯", "涤纶", "PVC", "PU", "塑料")
    material = "合成聚合物" if any(marker in product.description for marker in synthetic_markers) else "天然材料"
    description = product.description.replace("无塑料感", "含再生尼龙")
    highlights = [{"label": h.label, "detail": h.detail} for h in product.highlights]
    if product.product_id == "P1001":
        highlights = [
            {"label": "材质", "detail": "帆布+再生尼龙（含合成聚合物）"},
            *highlights[1:],
        ]
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "origin_country": product.origin_country,
        "description": description,
        "highlights": highlights,
        "ships_to": product.ships_to,
        "skus": [
            {"sku_id": sku.sku_id, "spec": sku.spec, "price_major": sku.price.to_major_units(), "currency": sku.price.currency, "stock": sku.stock}
            for sku in product.skus
        ],
        "source_platform": _PLATFORMS[index % len(_PLATFORMS)],
        "external_product_id": f"{_PLATFORMS[index % len(_PLATFORMS)]}-{product.product_id}",
        "canonical_product_id": f"CAN-LEGACY-{index:03d}",
        "material_tags": [material],
        "weight_kg": round(0.15 + (index % 20) * 0.09, 2),
        "dimensions_cm": {"length": 12 + index % 30, "width": 8 + index % 15, "height": 4 + index % 12},
        "tax_category": product.category,
        "rating_summary": {"average": round(4.0 + (index % 9) / 10, 1), "review_count": 30 + index * 11},
        "updated_at": "2026-08-01",
    }


def _price(index: int, currency: str) -> float:
    base = (39, 89, 169, 329, 699, 1299)[index % 6]
    if currency == "CNY":
        return float(base)
    if currency == "USD":
        return round(base / 7.1, 2)
    if currency == "EUR":
        return round(base / 7.8, 2)
    if currency == "JPY":
        return round(base / 0.048)
    return round(base / 5.3, 2)


def _generated_record(index: int) -> dict:
    category, keywords, noun = _CATEGORIES[index % len(_CATEGORIES)]
    material_tag, material_text = _MATERIALS[index % len(_MATERIALS)]
    platform = _PLATFORMS[index % len(_PLATFORMS)]
    currency = _CURRENCIES[index % len(_CURRENCIES)]
    product_id = f"P{2000 + index:04d}"
    multi_sku = index < 200
    fully_out = index < 50
    partially_out = 50 <= index < 100
    skus = []
    for variant in range(2 if multi_sku else 1):
        stock = 0 if fully_out or (partially_out and variant == 0) else 8 + (index * 7 + variant * 11) % 180
        skus.append(
            {
                "sku_id": f"{product_id}-S{variant + 1}",
                "spec": ("标准版" if variant == 0 else "升级版"),
                "price_major": round(_price(index + variant, currency) * (1 if variant == 0 else 1.12), 2),
                "currency": currency,
                "stock": stock,
            },
        )
    title = f"Atlas {noun} {index:03d}"
    brand = f"Atlas-{index % 24:02d}"
    description = f"{keywords} {material_text} 多场景使用 轻量 耐用 评测候选 {index:03d}"
    # 为“无合成聚合物旅行三件套”保留一个真实可检索的天然材质正例，
    # 避免把含再生尼龙的 P1001 错标为“无塑料”。
    if index == 120:
        title = "PureCanvas 纯棉旅行三件套（收纳袋+颈枕+眼罩）"
        brand = "PureCanvas"
        description = "旅行三件套 纯棉 帆布 天然材质 不含合成聚合物 长途飞行 评测候选"

    evaluation_tags = ["hard_negative"] if index < 75 else []
    if evaluation_tags:
        description += " 标题近似款：关键属性故意不匹配，用于检验属性过滤而非标题碰撞。"

    return {
        "product_id": product_id,
        "title": title,
        "brand": brand,
        "category": category,
        "origin_country": _ORIGINS[index % len(_ORIGINS)],
        "description": description,
        "highlights": [
            {"label": "材质", "detail": material_text},
            {"label": "测试属性", "detail": f"候选分组 {index // 5}"},
        ],
        "ships_to": _DESTINATIONS[index % len(_DESTINATIONS)],
        "skus": skus,
        "source_platform": platform,
        "external_product_id": f"{platform}-{product_id}",
        "canonical_product_id": f"CAN-{index // 5:03d}",
        "material_tags": [material_tag],
        "weight_kg": round(0.1 + (index % 35) * 0.08, 2),
        "dimensions_cm": {"length": 10 + index % 31, "width": 7 + index % 17, "height": 3 + index % 13},
        "tax_category": category,
        "rating_summary": {"average": round(3.8 + (index % 12) / 10, 1), "review_count": 20 + index * 13},
        "updated_at": f"2026-08-{1 + index % 28:02d}",
        "evaluation_tags": evaluation_tags,
    }


def build_records() -> list[dict]:
    legacy = [_legacy_record(product, index) for index, product in enumerate(_build_legacy_seed_products())]
    generated = [_generated_record(index) for index in range(440)]
    return legacy + generated


def main() -> None:
    records = build_records()
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    print(f"已写入 {_OUT}：{len(records)} SPU，{sum(len(record['skus']) for record in records)} SKU")


if __name__ == "__main__":
    main()
