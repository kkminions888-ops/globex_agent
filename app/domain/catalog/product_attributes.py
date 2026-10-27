# -*- coding: utf-8 -*-
"""保守的商品类型规范化：宽泛类目、描述中的用途词都不能充当商品事实。"""
from __future__ import annotations

import re
import unicodedata


# 类型是有限词表；增加别名不修改商品目录，也不补造重量等商品事实。
_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "背包": ("背包", "双肩包", "backpack", "daypack", "rucksack"),
    "颈枕": ("颈枕", "旅行枕", "u型枕", "neck pillow", "travel pillow"),
    "眼罩": ("眼罩", "sleep mask", "eye mask"),
    "旅行套装": ("旅行套装", "旅行三件套", "travel kit", "travel set"),
    "行李箱": ("行李箱", "旅行箱", "登机箱", "suitcase", "luggage case"),
    "腰包": ("腰包", "胸包", "waist bag", "belt bag", "fanny pack"),
    "收纳袋": ("收纳袋", "收纳包", "收纳方块", "packing cube", "storage pouch"),
    "耳机": ("耳机", "headphones", "headphone", "earbuds", "earphones"),
    "充电器": ("充电器", "charger"),
    "移动电源": ("移动电源", "充电宝", "power bank"),
    "数据线": ("数据线", "充电线", "charging cable", "usb cable"),
    "转换插头": ("转换插头", "travel adapter", "plug adapter"),
    "露营灯": ("露营灯", "营地灯", "camping lantern", "camp lantern"),
    "手电筒": ("手电筒", "手电", "flashlight", "torch"),
    "登山杖": ("登山杖", "trekking poles", "trekking pole", "hiking poles"),
    "睡袋": ("睡袋", "sleeping bag"),
    "睡袋内胆": ("睡袋内胆", "sleeping bag liner"),
    "水壶": ("水壶", "水瓶", "water bottle"),
    "咖啡杯": ("咖啡杯", "马克杯", "coffee mug", "mug"),
    "茶具套装": ("茶具套装", "tea set"),
    "餐具套装": ("餐具套装", "cutlery set"),
    "炊具套装": ("炊具套装", "cookware set"),
    "折叠凳": ("折叠凳", "露营凳", "camping stool", "folding stool"),
    "防潮垫": ("防潮垫", "sleeping pad", "camping mat"),
    "雨伞": ("雨伞", "晴雨伞", "umbrella"),
    "毛巾": ("毛巾", "towel"),
    "耳塞": ("隔音耳塞", "earplugs", "earplug"),
    "风扇": ("风扇", "neck fan", "portable fan"),
    "挂烫机": ("挂烫机", "garment steamer"),
    "外套": ("外套", "防风衣", "windbreaker", "jacket"),
}


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


_ALIASES = {_normalized(alias): kind for kind, aliases in _TYPE_ALIASES.items() for alias in aliases}
KNOWN_PRODUCT_TYPES = frozenset(_TYPE_ALIASES)


def normalize_product_type(value: object) -> str | None:
    """只规范化明确且已知的类型/别名；未知类型不被猜成最近的已知类型。"""
    return _ALIASES.get(_normalized(value)) if isinstance(value, str) else None


def _contains_alias(text: str, alias: str) -> bool:
    # 英文按词边界匹配，避免把 chargerette 等字符串判成充电器。
    if alias.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text) is not None
    return alias in text


def infer_product_type(title: str, category: str = "") -> str | None:
    """仅根据标题或细分类目推断；冲突/配件/宽泛类目返回未知。"""
    text = _normalized(title)
    # 描述不会传入此函数。配件标题即使出现主商品名也不能认作主商品。
    if re.search(r"(?:背包|双肩包).*?(?:挂件|挂饰|配件|防雨罩|保护罩)|(?:背包|双肩包)扣|(?:颈枕|枕头).*?(?:替换套|枕套)|(?:颈枕|枕头)套|(?:backpack|pillow).*?\b(?:covers?|accessor\w*)\b", text):
        return None
    matches = {kind for alias, kind in _ALIASES.items() if _contains_alias(text, alias)}
    # 一个明确的套装名优先于标题括号中的组成件名称。
    if "旅行套装" in matches:
        return "旅行套装"
    if "睡袋内胆" in matches:
        matches.discard("睡袋")
    if len(matches) == 1:
        return next(iter(matches))
    if matches:
        return None
    # category 必须完整匹配类型别名，“旅行装备”等一级类目不会命中。
    return normalize_product_type(category)
