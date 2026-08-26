from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stage1.index.corpus_index import CorpusIndex, Stage1Config


def _period(value: Any) -> tuple[int | None, int | None]:
    text = str(value or "")
    if "-" not in text:
        return None, None
    year, month = text.split("-", 1)
    try:
        return int(year), int(month)
    except ValueError:
        return None, None


def _subtype(metadata: dict[str, Any]) -> str | None:
    report = str(metadata.get("document_type") or "")
    if "사업보고서" in report:
        return "annual"
    if "반기보고서" in report:
        return "half"
    if "분기보고서" in report:
        return "quarter"
    _year, month = _period(metadata.get("report_period"))
    return {3: "quarter", 6: "half", 9: "quarter", 12: "annual"}.get(month)


class LocalJsonCorpusIndex:
    """Build the existing Stage1 ``CorpusIndex`` contract from local JSON."""

    @classmethod
    def load(cls, path: str | Path) -> CorpusIndex:
        source_path = Path(path).expanduser().resolve()
        rows = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("local JSON corpus must contain a list")

        config_dir = Path(__file__).resolve().parents[1] / "stage1" / "config"
        config = Stage1Config.load(config_dir)
        corp_names = {
            str((row.get("metadata") or {}).get("corp_name", "")).strip()
            for row in rows
            if isinstance(row, dict)
        }
        corp_names.discard("")
        sectors = {
            str((row.get("metadata") or {}).get("sector", "")).strip()
            for row in rows
            if isinstance(row, dict)
        }
        sectors.discard("")

        config.aliases = {
            **config.aliases,
            "extra_aliases": {
                alias: target
                for alias, target in config.aliases.get("extra_aliases", {}).items()
                if target in corp_names
            },
        }
        config.sectors = {
            **config.sectors,
            "sector_aliases": {
                alias: target
                for alias, target in config.sectors.get("sector_aliases", {}).items()
                if target in sectors
            },
        }

        available: dict[str, set[int]] = {}
        universe: list[dict[str, str]] = []
        seen_corps: set[str] = set()
        manifest: list[dict[str, Any]] = []
        for row in rows:
            metadata = dict(row.get("metadata") or {})
            corp_name = str(metadata.get("corp_name", "")).strip()
            if not corp_name:
                continue
            corp_code = str(metadata.get("corp_code", ""))
            if corp_name not in seen_corps:
                universe.append(
                    {
                        "corp_name": corp_name,
                        "corp_code": corp_code,
                        "stock_code": str(metadata.get("stock_code", "")),
                        "listed_name": corp_name,
                        "corp_eng_name": str(metadata.get("corp_eng_name", "")),
                        "sector": str(metadata.get("sector", "")),
                        "listing_date": str(metadata.get("listing_date", "")),
                    }
                )
                seen_corps.add(corp_name)
            year, month = _period(metadata.get("report_period"))
            if year and month:
                available.setdefault(str(year), set()).add(month)
            manifest.append(
                {
                    "chunk_id": row.get("id", ""),
                    "corp_name": corp_name,
                    "corp_code": corp_code,
                    "sector": str(metadata.get("sector", "")),
                    "doc_group": str(metadata.get("source_group", "periodic")),
                    "doc_subtype": _subtype(metadata),
                    "base_year": year,
                    "base_month": month,
                    "rcept_dt": str(metadata.get("disclosure_date", "")).replace("-", ""),
                    "report_nm": str(metadata.get("document_type", "")),
                    "is_correction": bool(metadata.get("is_correction", False)),
                    "section_name": str(metadata.get("section_name", "")),
                }
            )

        fiscal = dict(config.bounds.get("fiscal", {}))
        if available:
            years = sorted(int(year) for year in available)
            fiscal["min_year"] = min(years)
            fiscal["max_year"] = max(years)
            fiscal["available_periods"] = {
                year: sorted(months) for year, months in available.items()
            }
            config.bounds = {**config.bounds, "fiscal": fiscal}

        return CorpusIndex(universe, manifest, config, source_path.parent)
