# -*- coding: utf-8 -*-
"""Product 聚合根

Globex 把跨境商品建模为 Product（SPU）+ Sku（多个），携带品牌、产地、亮点等结构化属性。
SearchAgent 召回的"候选集"传递的就是 Product 卡片，TradeAgent 创建订单时再以 Sku 粒度结算。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Optional

from app.domain.catalog.product_attributes import infer_product_type, normalize_product_type
from app.domain.catalog.sku import Sku


@dataclass(frozen=True)
class ProductHighlight:
    label: str
    detail: str = ""


@dataclass
class Product:
    product_id: str
    title: str
    brand: str
    category: str
    origin_country: str
    description: str
    highlights: list[ProductHighlight] = field(default_factory=list)
    ships_to: list[str] = field(default_factory=list)
    skus: list[Sku] = field(default_factory=list)
    # 以下字段来自版本化商品数据集；用于评测跨平台去重、材质/配送约束与数据新鲜度。
    source_platform: str = ""
    external_product_id: str = ""
    canonical_product_id: str = ""
    material_tags: list[str] = field(default_factory=list)
    weight_kg: float = 0.0
    dimensions_cm: dict[str, float] = field(default_factory=dict)
    tax_category: str = ""
    rating_summary: dict[str, float | int] | None = None
    updated_at: str = ""
    source_language: str = ""
    source_locale: str = ""
    localized_category: str = ""
    localized_material: str = ""
    data_provenance: str = ""
    # 新字段不写入旧的向量检索文本，以免无意间改变 Embedding 输入。
    product_type: str | None = None
    model_spec: str | dict[str, object] | None = ""

    def __post_init__(self) -> None:
        if not self.product_id:
            raise ValueError("Product.product_id required")
        if not self.skus:
            raise ValueError(f"Product 至少要有一个 Sku：{self.product_id}")
        if self.weight_kg < 0:
            raise ValueError(f"Product.weight_kg 必须非负：{self.product_id}")

    @property
    def known_weight_kg(self) -> float | None:
        """只返回有依据的正数重量；0、负数、非有限值都代表未知。"""
        value = self.weight_kg
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and value > 0 else None

    @property
    def effective_product_type(self) -> str | None:
        """显式类型优先，旧目录才使用保守标题/类目推断。"""
        if self.product_type is not None and str(self.product_type).strip():
            return normalize_product_type(self.product_type)
        return infer_product_type(self.title, self.localized_category or self.category)

    def searchable_fields(self) -> dict[str, str]:
        """字段检索使用结构化文本，供词项权重独立配置。"""
        if isinstance(self.model_spec, dict):
            model = " ".join(
                str(self.model_spec[key]) for key in ("model", "name", "value", "unit")
                if key in self.model_spec and isinstance(self.model_spec[key], (str, int, float))
            )
        else:
            model = self.model_spec if isinstance(self.model_spec, str) else ""
        return {
            "title": self.title,
            "brand": self.brand,
            "model": model,
            "category": self.localized_category or self.category,
            "description": self.description,
            "highlights": " ".join(f"{h.label} {h.detail}" for h in self.highlights),
            "material": self.localized_material or " ".join(self.material_tags),
        }

    def primary_sku(self) -> Sku:
        return self.skus[0]

    def primary_available_sku(self) -> Sku:
        """优先返回可售 SKU，避免部分缺货商品仍展示缺货的默认规格。"""
        return next((sku for sku in self.skus if sku.stock > 0), self.primary_sku())

    def has_available_sku(self) -> bool:
        return any(sku.stock > 0 for sku in self.skus)

    def find_sku(self, sku_id: str) -> Optional[Sku]:
        return next((s for s in self.skus if s.sku_id == sku_id), None)

    def searchable_text(self) -> str:
        """召回用的可检索文本：标题 + 品牌 + 品类 + 描述 + 亮点。"""
        highlight_text = " ".join(f"{h.label} {h.detail}" for h in self.highlights)
        return " ".join(
            [
                self.title, self.brand, self.localized_category or self.category,
                self.origin_country, self.description, highlight_text,
                self.localized_material or " ".join(self.material_tags),
                self.localized_category or self.tax_category,
            ],
        )
