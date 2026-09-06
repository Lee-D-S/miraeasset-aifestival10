"""Detect question content not covered by the deterministic dictionaries.

This is a call gate, not a second classifier: unknown wording is sent to the
slot model instead of being silently interpreted as a default lookup.
"""
from __future__ import annotations

import re

from ..index.corpus_index import squash


def needs_semantic_review(pre, entities, slots, index) -> bool:
    text = pre.squashed
    terms = [corp.matched_text for corp in entities.corps + entities.excluded_corps]
    terms.extend(index.sector_alias_map)
    for entry in index.config.metrics.get("metrics", []) + index.config.metrics.get("derived_metrics", []):
        terms.extend(entry.get("labels", []))
    for entry in index.config.metrics.get("doc_keywords", []):
        terms.extend(entry.get("labels", []))
    for category in ("intent_cues", "basis_cues", "correction_cues", "time_mode_cues"):
        for values in index.config.defaults.get(category, {}).values():
            if isinstance(values, list):
                terms.extend(values)
    terms.extend(index.config.defaults.get("relative_time_cues", {}))
    terms.extend(("상반기", "하반기", "연간", "첫여섯달", "첫6개월", "처음6개월", "전반기",
                  "원공시만", "원본만", "최초공시만", "정정본을제외", "정정제외",
                  "최종내용만", "최종공시만", "정정후최종", "최신정정", "마지막정정",
                  "자회사를빼고", "자회사를제외", "자회사제외", "본사만", "모회사만",
                  "국내", "해외", "지역별", "제품별", "사업부문별", "부문별", "합계", "평균",
                  "차액", "차이", "곱한", "최대", "최소", "마진"))
    for term in sorted({squash(term) for term in terms if term}, key=len, reverse=True):
        text = text.replace(term, " ")
    text = re.sub(r"(?:19|20)?\d{2}년?|[1-4]분기|[1-4]q|q[1-4]|\d+", " ", text)
    # Only grammatical/question scaffolding may remain without a model review.
    grammar = ("알려주세요|알려줘|알려줄래|보여줘|확인해줘|설명해줘|어떻게|얼마인가요|"
               "인가요|인가|입니까|일까요|했나요|했나|됐나요|값|수치|기준|내용|현황|"
               "전체|단독|나눈|만드는|회사|기업|것|좀|은|는|이|가|을|를|의|과|와|"
               r"제외하고|제외한|빼고|말고|하고|에서|으로|로|에|중|만|도|한|및|대해|각각|대상|부터|까지|요|해|줘|\s")
    return bool(re.sub(grammar, "", text))
