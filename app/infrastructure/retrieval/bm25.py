"""应用层 BM25：Unicode 词项、完整型号、汉字/假名二元词项。"""
from __future__ import annotations
from collections import Counter
from functools import lru_cache
import math
import re
import unicodedata
from collections.abc import Mapping

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\u3005\U00020000-\U0002ffff]+")
_WORD = re.compile(r"[^\W_]+(?:[-_.][^\W_]+)*", re.UNICODE)

# 仅作为实验开发起点；上线调用方须显式选择，经开发集冻结后再验收。
FIELD_WEIGHTED_BM25_DEFAULT_WEIGHTS = {
    "title": 3.0,
    "brand": 2.0,
    "model": 4.0,
    "category": 1.5,
    "description": 1.0,
    "highlights": 1.0,
    "material": 1.5,
}


def terms(text: str) -> list[str]:
    # 统一全角型号与组合重音，同时保留语义不同的重音字母；不擅自音译。
    value = unicodedata.normalize("NFKC", text).casefold()
    tokens = _WORD.findall(_CJK.sub(" ", value))
    for segment in _CJK.findall(value):
        tokens.extend(segment[i:i+2] for i in range(len(segment)-1))
        if len(segment) == 1:
            tokens.append(segment)
    return tokens


def _subwords(words) -> list[str]:
    # 德语/荷兰语/瑞典语常把商品名写成复合词。字符字段仅辅助召回，
    # 数字及带连接符的型号不拆分，避免 WH-1000XM5 与其它型号靠片段混淆。
    return [word[i:i+3] for word in words if word.isalpha() and 5 <= len(word) <= 64
            for i in range(len(word)-2)]


@lru_cache(maxsize=8192)
def _document_terms(text, tokenizer):
    # 只缓存不可变正文派生的词项；库存/报价仍逐次读取权威数据。
    # tokenizer 也是键的一部分，离线消融使用旧分词器时不会串用缓存。
    words = tokenizer(text)
    return Counter(words), Counter(_subwords(words))


def _field_scores(query_terms, documents, *, k1, b):
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / max(1, len(documents))
    frequencies = Counter(term for doc in documents for term in doc)
    scored = []
    for document, length in zip(documents, lengths):
        score = 0.0
        for term in query_terms:
            frequency = document[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(documents) - frequencies[term] + .5) / (frequencies[term] + .5))
            score += idf * frequency * (k1 + 1) / (frequency + k1 * (1-b + b*length/max(average, 1)))
        scored.append(score)
    return scored


def bm25_rank(query: str, products: list, *, k1: float = 1.2, b: float = .75,
              subword_weight: float = .25) -> list[tuple[float, object]]:
    if not math.isfinite(subword_weight) or subword_weight < 0:
        raise ValueError("子词权重必须为有限非负数")
    fields = [_document_terms(p.searchable_text(), terms) for p in products]
    query_words = set(terms(query))
    word_scores = _field_scores(query_words, [f[0] for f in fields], k1=k1, b=b)
    query_subwords = set(_subwords(query_words))
    subword_scores = (_field_scores(query_subwords, [f[1] for f in fields], k1=k1, b=b)
                      if subword_weight and query_subwords else [0.] * len(products))
    # 按查询扩展数量归一化，避免长单词产生大量子词后压过完整词项。
    scale = subword_weight / max(1, len(query_subwords) / max(1, len(query_words)))
    scored = [(whole + scale * subword, product)
              for product, whole, subword in zip(products, word_scores, subword_scores)
              if whole + scale * subword > 0]
    return sorted(scored, key=lambda pair: (-pair[0], pair[1].product_id))


def field_weighted_bm25_rank(
    query: str,
    products: list,
    field_weights: Mapping[str, float] | None = None,
    *,
    k1: float = 1.2,
    b: float = .75,
    subword_weight: float = .25,
) -> list[tuple[float, object]]:
    """逐字段 BM25 后加权，保留各字段独立的长度归一化和文档频率。

    显式权重是完整实验配置，未列出的字段不计分；默认值仅是待评测起点。
    不重复文本放大词频，也不改动现有 ``bm25_rank`` 的排序行为。
    """
    weights = dict(FIELD_WEIGHTED_BM25_DEFAULT_WEIGHTS) if field_weights is None else field_weights
    if not isinstance(weights, Mapping) or set(weights) - set(FIELD_WEIGHTED_BM25_DEFAULT_WEIGHTS):
        raise ValueError("字段权重必须为已知检索字段的字典")
    if (not weights or any(type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0
                           for weight in weights.values()) or not any(weights.values())):
        raise ValueError("字段权重必须有限、非负且至少一项大于零")
    if (type(k1) not in (int, float) or not math.isfinite(k1) or k1 <= 0
            or type(b) not in (int, float) or not math.isfinite(b) or not 0 <= b <= 1
            or type(subword_weight) not in (int, float) or not math.isfinite(subword_weight)
            or subword_weight < 0):
        raise ValueError("BM25 参数须满足 k1>0、0<=b<=1、子词权重非负，且均为有限数")
    query_words = sorted(set(terms(query)))
    query_subwords = sorted(set(_subwords(query_words)))
    scale = subword_weight / max(1, len(query_subwords) / max(1, len(query_words)))
    documents = [product.searchable_fields() for product in products]
    scores = [0.0] * len(products)
    for name in FIELD_WEIGHTED_BM25_DEFAULT_WEIGHTS:
        weight = weights.get(name, 0)
        if weight == 0:
            continue
        fields = [_document_terms(document.get(name, ""), terms) for document in documents]
        word_scores = _field_scores(query_words, [field[0] for field in fields], k1=k1, b=b)
        subword_scores = (
            _field_scores(query_subwords, [field[1] for field in fields], k1=k1, b=b)
            if scale and query_subwords else [0.0] * len(products)
        )
        for index, (whole, subword) in enumerate(zip(word_scores, subword_scores)):
            scores[index] += weight * (whole + scale * subword)
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("字段加权得分超出有限数值范围，请缩小权重")
    return sorted(
        [(score, product) for score, product in zip(scores, products) if score > 0],
        key=lambda pair: (-pair[0], pair[1].product_id),
    )


def reciprocal_rank_fusion(*rankings: list, k: int = 60, weights=None) -> list:
    weights = tuple(weights) if weights is not None else (1.0,) * len(rankings)
    if (len(weights) != len(rankings) or not any(weights)
            or any(not math.isfinite(w) or w < 0 for w in weights)
            or type(k) is not int or k < 1):
        raise ValueError("融合权重须等长、有限、非负且至少一项大于零；k 须为正整数")
    scores, products = {}, {}
    for ranking, weight in zip(rankings, weights):
        if weight == 0:
            continue
        seen = set()
        for rank, (_, product) in enumerate(ranking, start=1):
            key = product.product_id
            if key in seen:
                continue
            seen.add(key)
            products[key] = product
            scores[key] = scores.get(key, 0) + weight/(k+rank)
    return [(scores[key], products[key]) for key in sorted(scores, key=lambda key: (-scores[key], key))]
