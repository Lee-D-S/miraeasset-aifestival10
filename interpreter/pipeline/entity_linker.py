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
# "삼성SDI를 제외한"에서 기업명과 제외 단서 사이에 허용하는 조사 길이.
_EXCLUSION_PARTICLE_MAX = 2


@dataclass
class EntityResult:
    corps: list[CorpRef] = field(default_factory=list)
    # "A를 제외한 ..."으로 명시적으로 빠진 기업. corps에는 넣지 않는다.
    excluded_corps: list[CorpRef] = field(default_factory=list)
    sector: Optional[str] = None
    sector_members: list[str] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    # 블로클리스트 확정 매칭. route=unanswerable 근거로 쓴다.
    unknown_entities: list[str] = field(default_factory=list)
    # 계약상대방·발주처처럼 질의 대상이 아닌 관계값으로 식별된 외부 기업.
    related_entities: list[str] = field(default_factory=list)
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


def _is_excluded_mention(text: str, end: int, cues: list[str], particles: str) -> bool:
    """기업명 바로 뒤가 제외 표현인지 본다.

    "삼성SDI를 제외한 2차전지 기업"에서 삼성SDI만 빼야 하는데, 질의 전체에서 '제외'를
    찾으면 어느 기업을 빼라는 것인지 알 수 없다. 그래서 매칭된 기업명 직후(조사 최대
    2자)에 단서가 붙은 경우만 제외로 인정한다.
    """
    if not cues:
        return False
    tail = text[end : end + 12]
    offset = 0
    while offset < len(tail) and offset < _EXCLUSION_PARTICLE_MAX and tail[offset] in particles:
        offset += 1
    rest = tail[offset:]
    return any(cue and rest.startswith(cue) for cue in cues)


def _consume(text: str, spans: list[tuple[int, int]]) -> str:
    """소비한 구간을 구분자로 치환해, 이후 부분문자열 매칭이 경계를 넘지 않게 한다."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = _SPAN_SEP
    return "".join(chars)


_RELATION_ROLE_PATTERN = re.compile(r"(?:계약상대방|계약상대|발주처|인수인)(?:인|은|는|이|가)?$")


def _is_relationship_mention(text: str, start: int) -> bool:
    """Return whether an external company follows an explicit role cue."""

    prefix = text[max(0, start - 16) : start]
    return bool(_RELATION_ROLE_PATTERN.search(prefix))


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
    exclusion = index.config.defaults.get("exclusion_cues", {})
    exclusion_cues = [squash(cue) for cue in exclusion.get("cues", [])]
    exclusion_particles = exclusion.get("particles", "")

    blocklist = {squash(name): name for name in guards.get("external_corp_blocklist", [])}
    blocklist = {k: v for k, v in blocklist.items() if len(k) >= 2}

    # 1) 코퍼스 외 기업
    block_hits = _scan_longest(text, blocklist, max((len(k) for k in blocklist), default=0))
    for start, _end, _key, name in block_hits:
        target = result.related_entities if _is_relationship_mention(text, start) else result.unknown_entities
        if name not in target:
            target.append(name)
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
        ref = CorpRef(
            corp_name=corp_name,
            corp_code=row["corp_code"],
            stock_code=row["stock_code"],
            listed_name=row["listed_name"],
            sector=row["sector"],
            listing_date=row["listing_date"],
            matched_text=pre.original_slice(start, end) or key,
            match_source="alias",
        )
        if _is_excluded_mention(text, end, exclusion_cues, exclusion_particles):
            result.excluded_corps.append(ref)
        else:
            result.corps.append(ref)
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
                    ref = CorpRef(
                        corp_name=corp_name,
                        corp_code=row["corp_code"],
                        stock_code=row["stock_code"],
                        listed_name=row["listed_name"],
                        sector=row["sector"],
                        listing_date=row["listing_date"],
                        matched_text=pre.original_slice(start, end) or key,
                        match_source="prefix_unique",
                    )
                    if _is_excluded_mention(text, end, exclusion_cues, exclusion_particles):
                        result.excluded_corps.append(ref)
                    else:
                        result.corps.append(ref)
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

    # 제외 대상은 섹터 멤버 목록에서도 빼야 한다. 남겨두면 2·3단계가
    # "섹터 전원의 수치가 필요하다"고 판단해 빠진 기업을 다시 찾는다.
    if result.excluded_corps and result.sector_members:
        excluded_names = {corp.corp_name for corp in result.excluded_corps}
        result.sector_members = [
            name for name in result.sector_members if name not in excluded_names
        ]

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
