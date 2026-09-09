# -*- coding: utf-8 -*-
"""ProductSearchSpec 值对象

SearchAgent 把买家自然语言 query 改写为标准化检索规格：
normalized_query 用于召回，槽位（category / price_band / ship_to / locale）用于过滤。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import math


@dataclass(frozen=True)
class ProductSearchSpec:
    normalized_query: str
    category: Optional[str] = None
    ship_to: Optional[str] = None
    locale: str = "zh-CN"
    top_k: int = 5
    # 到手价目标币种：命中 ship_to 时商品卡内联 landed_price（小计+运费+关税）
    target_currency: str = "CNY"
    # 价格硬约束（目标币种主单位）：硬约束由检索链路结构化过滤，不交给 embedding/reranker
    price_max_major: Optional[float] = None
    # 材质黑名单：如“不要塑料”应传入 ["合成聚合物"]，不靠最终回复临时解释。
    excluded_material_tags: list[str] | tuple[str, ...] = ()
    # 材质白名单：复合约束评测及“必须是金属/天然纤维”等场景必须结构化过滤。
    required_material_tags: list[str] | tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.normalized_query or not self.normalized_query.strip():
            raise ValueError("ProductSearchSpec.normalized_query required")
        if type(self.top_k) is not int or not 1 <= self.top_k <= 50:
            raise ValueError("ProductSearchSpec.top_k 必须为1到50的整数")
        if self.price_max_major is not None and (isinstance(self.price_max_major, bool) or not math.isfinite(self.price_max_major) or self.price_max_major < 0):
            raise ValueError("ProductSearchSpec.price_max_major 必须是有限的非负金额")
