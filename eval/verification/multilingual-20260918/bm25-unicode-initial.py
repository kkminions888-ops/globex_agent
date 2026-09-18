"""应用层 BM25：Unicode 词项、完整型号、汉字/假名二元词项。"""
from __future__ import annotations
from collections import Counter
import math
import re
import unicodedata

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\u3005\U00020000-\U0002ffff]+")
_WORD = re.compile(r"[^\W_]+(?:[-_.][^\W_]+)*", re.UNICODE)


def terms(text: str) -> list[str]:
    # 统一全角型号与组合重音，同时保留语义不同的重音字母；不擅自音译。
    value = unicodedata.normalize("NFKC", text).casefold()
    tokens = _WORD.findall(_CJK.sub(" ", value))
    for segment in _CJK.findall(value):
        tokens.extend(segment[i:i+2] for i in range(len(segment)-1))
        if len(segment) == 1:
            tokens.append(segment)
    return tokens


def bm25_rank(query: str, products: list, *, k1: float = 1.2, b: float = .75) -> list[tuple[float, object]]:
    documents = [Counter(terms(p.searchable_text())) for p in products]
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / max(1, len(documents))
    frequencies = Counter(term for doc in documents for term in doc)
    query_terms = set(terms(query))
    scored = []
    for product, document, length in zip(products, documents, lengths):
        score = 0.0
        for term in query_terms:
            frequency = document[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(documents) - frequencies[term] + .5) / (frequencies[term] + .5))
            score += idf * frequency * (k1 + 1) / (frequency + k1 * (1-b + b*length/max(average, 1)))
        if score > 0:
            scored.append((score, product))
    return sorted(scored, key=lambda pair: (-pair[0], pair[1].product_id))


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
