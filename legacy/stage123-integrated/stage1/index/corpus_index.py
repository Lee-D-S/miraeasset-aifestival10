"""universe.csv / manifest.jsonl 로더와 별칭·섹터 인덱스.

1단계는 raw/ XML을 열지 않는다. manifest는 후보 문서 건수 확인용으로만 쓴다.
corp_code(8) / stock_code(6)는 선행 0이 있어 항상 문자열로 다룬다.
"""

from __future__ import annotations

import csv
import json
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models.intent import ManifestFilter

CONFIG_FILES = {
    "aliases": "aliases.json",
    "sectors": "sector_aliases.json",
    "metrics": "metric_router.json",
    "defaults": "defaults.json",
    "bounds": "corpus_bounds.json",
    "guards": "guard_patterns.json",
}

# squash 시 제거하는 문자. '&'와 '%'는 삼성E&A / 5% 같은 유효 토큰이라 남긴다.
_DROP_CHARS = set(" \t\r\n.,'\"`()[]{}<>·ㆍ:;!?~/\\|+*=_-–—")


def squash(text: str) -> str:
    """별칭·라벨 매칭용 정규화. 공백·구두점 제거 + 소문자."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(ch.lower() for ch in normalized if ch not in _DROP_CHARS)


def squash_with_map(text: str) -> tuple[str, list[int]]:
    """squash 결과와 (squash 인덱스 → 원문 인덱스) 매핑."""
    normalized = unicodedata.normalize("NFKC", text)
    chars: list[str] = []
    positions: list[int] = []
    for i, ch in enumerate(normalized):
        if ch in _DROP_CHARS:
            continue
        chars.append(ch.lower())
        positions.append(i)
    return "".join(chars), positions


@dataclass
class Stage1Config:
    aliases: dict[str, Any]
    sectors: dict[str, Any]
    metrics: dict[str, Any]
    defaults: dict[str, Any]
    bounds: dict[str, Any]
    guards: dict[str, Any]

    @classmethod
    def load(cls, config_dir: Path) -> "Stage1Config":
        loaded = {}
        for key, filename in CONFIG_FILES.items():
            path = config_dir / filename
            with path.open(encoding="utf-8") as fp:
                loaded[key] = json.load(fp)
        return cls(**loaded)


def _find_corpus_dir(explicit: Optional[Path]) -> Path:
    if explicit:
        return Path(explicit)

    env = os.environ.get("CORPUS_DIR")
    if env:
        return Path(env)

    here = Path(__file__).resolve()
    for base in [here.parent.parent, *here.parents]:
        for candidate in sorted(base.glob("*/*/corpus")) + sorted(base.glob("*/corpus")):
            if (candidate / "universe.csv").exists():
                return candidate
        if (base / "corpus" / "universe.csv").exists():
            return base / "corpus"
    raise FileNotFoundError(
        "corpus 디렉터리를 찾지 못했습니다. CORPUS_DIR 환경변수로 universe.csv가 있는 폴더를 지정하세요."
    )


class CorpusIndex:
    def __init__(
        self,
        universe: list[dict[str, str]],
        manifest: list[dict[str, Any]],
        config: Stage1Config,
        corpus_dir: Path,
    ) -> None:
        self.universe = universe
        self.manifest = manifest
        self.config = config
        self.corpus_dir = corpus_dir

        self.by_corp_name: dict[str, dict[str, str]] = {r["corp_name"]: r for r in universe}
        self.alias_map: dict[str, str] = {}
        self.sector_members: dict[str, list[str]] = {}
        self.sector_alias_map: dict[str, str] = {}
        self.ambiguous_tokens: dict[str, list[str]] = {}

        self._build_alias_map()
        self._build_sector_index()
        self._build_ambiguous_tokens()

    # --- 로딩 -----------------------------------------------------------------

    @classmethod
    def load(
        cls,
        corpus_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None,
    ) -> "CorpusIndex":
        resolved_corpus = _find_corpus_dir(corpus_dir)
        resolved_config = Path(config_dir) if config_dir else Path(__file__).resolve().parent.parent / "config"

        config = Stage1Config.load(resolved_config)
        universe = cls._read_universe(resolved_corpus / "universe.csv")
        manifest = cls._read_manifest(resolved_corpus / "manifest.jsonl")
        return cls(universe, manifest, config, resolved_corpus)

    @staticmethod
    def _read_universe(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as fp:
            return [dict(row) for row in csv.DictReader(fp)]

    @staticmethod
    def _read_manifest(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    # --- 인덱스 ---------------------------------------------------------------

    def _register_alias(self, alias: str, corp_name: str) -> None:
        key = squash(alias)
        if len(key) < 2:
            return
        existing = self.alias_map.get(key)
        if existing and existing != corp_name:
            # 서로 다른 기업이 같은 키를 주장하면 자동 매핑하지 않는다.
            self.alias_map[key] = ""
            return
        self.alias_map[key] = corp_name

    def _build_alias_map(self) -> None:
        for row in self.universe:
            corp_name = row["corp_name"]
            for field_name in ("corp_name", "listed_name", "corp_eng_name", "stock_code"):
                value = (row.get(field_name) or "").strip()
                if value:
                    self._register_alias(value, corp_name)

        for alias, corp_name in self.config.aliases.get("extra_aliases", {}).items():
            if corp_name not in self.by_corp_name:
                raise ValueError(f"aliases.json의 '{alias}' → '{corp_name}'는 universe에 없는 corp_name입니다.")
            self._register_alias(alias, corp_name)

        self.alias_map = {k: v for k, v in self.alias_map.items() if v}

    def _build_sector_index(self) -> None:
        for row in self.universe:
            self.sector_members.setdefault(row["sector"], []).append(row["corp_name"])

        for sector in self.sector_members:
            self.sector_alias_map[squash(sector)] = sector

        for alias, sector in self.config.sectors.get("sector_aliases", {}).items():
            if sector not in self.sector_members:
                raise ValueError(f"sector_aliases.json의 '{alias}' → '{sector}'는 universe에 없는 sector입니다.")
            self.sector_alias_map[squash(alias)] = sector

    def _build_ambiguous_tokens(self) -> None:
        for token in self.config.aliases.get("ambiguous_tokens", []):
            key = squash(token)
            candidates = sorted(
                {
                    row["corp_name"]
                    for row in self.universe
                    if squash(row["corp_name"]).startswith(key)
                    or squash(row["listed_name"]).startswith(key)
                }
            )
            self.ambiguous_tokens[key] = candidates

    # --- 조회 -----------------------------------------------------------------

    def corp(self, corp_name: str) -> Optional[dict[str, str]]:
        return self.by_corp_name.get(corp_name)

    def resolve_alias(self, key: str) -> Optional[str]:
        return self.alias_map.get(key)

    def alias_keys_sorted(self) -> list[str]:
        return sorted(self.alias_map, key=len, reverse=True)

    def sector_keys_sorted(self) -> list[str]:
        return sorted(self.sector_alias_map, key=len, reverse=True)

    def members_of(self, sector: str) -> list[str]:
        return list(self.sector_members.get(sector, []))

    def count_docs(self, flt: ManifestFilter) -> int:
        return sum(1 for _ in self.iter_docs(flt))

    def iter_docs(self, flt: ManifestFilter) -> Iterable[dict[str, Any]]:
        groups = set(flt.effective_doc_groups())
        subtypes = set(flt.effective_doc_subtypes())
        corp_names = set(flt.corp_names)
        base_years = set(flt.base_years)
        base_months = set(flt.base_months)

        for doc in self.manifest:
            if corp_names and doc["corp_name"] not in corp_names:
                continue
            if not corp_names and flt.sector and doc.get("sector") != flt.sector:
                continue
            if groups and doc["doc_group"] not in groups:
                continue
            if subtypes and doc.get("doc_subtype") not in subtypes:
                continue
            if flt.is_correction is not None and bool(doc.get("is_correction")) != flt.is_correction:
                continue

            # base_year/base_month는 정기공시 전용 필드다. 수시공시는 접수일로만 자른다.
            # 정기공시를 접수일로 자르면 FY2025 사업보고서(2026년 접수)가 탈락한다.
            if doc["doc_group"] == "periodic":
                if base_years and doc.get("base_year") not in base_years:
                    continue
                if base_months and doc.get("base_month") not in base_months:
                    continue
                if not base_years and not self._in_rcept_range(doc, flt):
                    continue
            elif not self._in_rcept_range(doc, flt):
                continue
            if flt.report_nm_contains and not any(
                token in doc.get("report_nm", "") for token in flt.report_nm_contains
            ):
                continue
            yield doc

    @staticmethod
    def _in_rcept_range(doc: dict[str, Any], flt: ManifestFilter) -> bool:
        if flt.rcept_from and doc["rcept_dt"] < flt.rcept_from:
            return False
        if flt.rcept_to and doc["rcept_dt"] > flt.rcept_to:
            return False
        return True
