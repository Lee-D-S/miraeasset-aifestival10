"""② 사전 매칭 — universe.csv로 기업·섹터를 확정한다. LLM을 쓰지 않는다.

매칭 순서가 중요하다.
1) 코퍼스 외 기업 블로클리스트를 먼저 소비한다. (카카오뱅크가 '카카오'로 잘못 잡히는 것 방지)
2) 최장일치로 기업 별칭을 소비한다. (삼성전자가 '삼성'으로 잡히는 것 방지)
3) 남은 구간에서만 섹터·모호토큰·접미어 휴리스틱을 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..index.corpus_index import CorpusIndex, squash
from ..models.intent import CorpRef
from .preprocess import PreprocessResult

_ASCII_SHORT_MAX = 3
_SPAN_SEP = "\u0000"


@dataclass
class EntityResult:
    corps: list[CorpRef] = field(default_factory=list)
    sector: Optional[str] = None
    sector_members: list[str] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    # 블로클리스트 확정 매칭. route=unanswerable 근거로 쓴다.
    unknown_entities: list[str] = field(default_factory=list)
    # 접미어 휴리스틱 추정. 오탐 가능성이 있어 경고로만 쓴다.
    suspect_entities: list[str] = field(default_factory=list)
    leftover: str = ""


def _is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _boundary_ok(text: str, start: int, end: int, key: str) -> bool:
    """짧은 라틴 별칭(NC, KT 등)이 영문 단어 안에서 잡히는 것을 막는다."""
    if not (key.isascii() and len(key) <= _ASCII_SHORT_MAX):
        return True
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (_is_ascii_alnum(before) or _is_ascii_alnum(after))


def _consume(text: str, spans: list[tuple[int, int]]) -> str:
    """소비한 구간을 구분자로 치환해, 이후 부분문자열 매칭이 경계를 넘지 않게 한다."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = _SPAN_SEP
    return "".join(chars)


def _scan_longest(text: str, keys: dict[str, str], max_len: int) -> list[tuple[int, int, str, str]]:
    hits: list[tuple[int, int, str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == _SPAN_SEP:
            i += 1
            continue
        matched = False
        upper = min(max_len, n - i)
        for length in range(upper, 1, -1):
            candidate = text[i : i + length]
            if _SPAN_SEP in candidate:
                continue
            value = keys.get(candidate)
            if value is None:
                continue
            if not _boundary_ok(text, i, i + length, candidate):
                continue
            hits.append((i, i + length, candidate, value))
            i += length
            matched = True
            break
        if not matched:
            i += 1
    return hits


def link(pre: PreprocessResult, index: CorpusIndex) -> EntityResult:
    result = EntityResult()
    text = pre.squashed
    if not text:
        result.leftover = ""
        return result

    guards = index.config.guards
    blocklist = {squash(name): name for name in guards.get("external_corp_blocklist", [])}
    blocklist = {k: v for k, v in blocklist.items() if len(k) >= 2}

    # 1) 코퍼스 외 기업
    block_hits = _scan_longest(text, blocklist, max((len(k) for k in blocklist), default=0))
    for _, _, _, name in block_hits:
        if name not in result.unknown_entities:
            result.unknown_entities.append(name)
    text = _consume(text, [(s, e) for s, e, _, _ in block_hits])

    # 2) 기업 별칭 (최장일치)
    alias_map = index.alias_map
    alias_hits = _scan_longest(text, alias_map, max((len(k) for k in alias_map), default=0))
    seen: set[str] = set()
    for start, end, key, corp_name in alias_hits:
        if corp_name in seen:
            continue
        row = index.corp(corp_name)
        if row is None:
            continue
        seen.add(corp_name)
        result.corps.append(
            CorpRef(
                corp_name=corp_name,
                corp_code=row["corp_code"],
                stock_code=row["stock_code"],
                listed_name=row["listed_name"],
                sector=row["sector"],
                listing_date=row["listing_date"],
                matched_text=pre.original_slice(start, end) or key,
                match_source="alias",
            )
        )
    text = _consume(text, [(s, e) for s, e, _, _ in alias_hits])

    # 3) 섹터
    sector_hits = _scan_longest(
        text,
        index.sector_alias_map,
        max((len(k) for k in index.sector_alias_map), default=0),
    )
    if sector_hits:
        result.sector = sector_hits[0][3]
        result.sector_members = index.members_of(result.sector)
        text = _consume(text, [(s, e) for s, e, _, _ in sector_hits])

    # 4) 모호 토큰 (그룹명·짧은 접두어)
    ambiguous_keys = {k: k for k in index.ambiguous_tokens}
    amb_hits = _scan_longest(text, ambiguous_keys, max((len(k) for k in ambiguous_keys), default=0))
    for start, end, key, _ in amb_hits:
        candidates = index.ambiguous_tokens.get(key, [])
        if len(candidates) == 1:
            corp_name = candidates[0]
            if corp_name not in seen:
                row = index.corp(corp_name)
                if row is not None:
                    seen.add(corp_name)
                    result.corps.append(
                        CorpRef(
                            corp_name=corp_name,
                            corp_code=row["corp_code"],
                            stock_code=row["stock_code"],
                            listed_name=row["listed_name"],
                            sector=row["sector"],
                            listing_date=row["listing_date"],
                            matched_text=pre.original_slice(start, end) or key,
                            match_source="prefix_unique",
                        )
                    )
        elif len(candidates) > 1:
            result.ambiguous.append(
                {
                    "token": pre.original_slice(start, end) or key,
                    "candidates": candidates,
                }
            )
    text = _consume(text, [(s, e) for s, e, _, _ in amb_hits])

    # 5) 접미어 휴리스틱 (블로클리스트에 없는 코퍼스 외 기업)
    heuristic = guards.get("corp_suffix_heuristic", {})
    if heuristic.get("enabled"):
        for name in _suffix_candidates(text, heuristic, index):
            if name not in result.suspect_entities:
                result.suspect_entities.append(name)

    result.leftover = text
    return result


def _suffix_candidates(text: str, heuristic: dict[str, Any], index: CorpusIndex) -> list[str]:
    suffixes = heuristic.get("suffixes", [])
    non_corp = {squash(term) for term in heuristic.get("non_corp_terms", [])}
    found: list[str] = []
    if not suffixes:
        return found

    pattern = re.compile(
        r"([0-9A-Za-z가-힣]{2,12}?)(" + "|".join(re.escape(s) for s in suffixes) + r")"
    )
    for match in pattern.finditer(text):
        token = match.group(0)
        if _SPAN_SEP in token or len(token) < 3:
            continue
        key = squash(token)
        if key in non_corp or key in index.alias_map or key in index.sector_alias_map:
            continue
        if key in index.ambiguous_tokens:
            continue
        found.append(token)
    return found
