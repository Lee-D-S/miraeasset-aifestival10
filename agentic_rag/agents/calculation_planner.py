from __future__ import annotations

import re
from typing import Any

from agentic_rag.deterministic.calculation_schema import validate_calculation_plan
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE
from agentic_rag.llm.schemas import CALCULATION_PLAN_SCHEMA
from agentic_rag.llm.prompts import CALCULATION_PLAN_PROMPT


def deterministic_plan(question: str) -> dict[str, Any] | None:
    text = str(question or "")
    periods = re.findall(r"20\d{2}", text)
    metric = "revenue"
    if "영업이익률" in text:
        return {"operation": "margin", "metric": "operating_income", "sub_operations": ["operating_income_lookup", "revenue_lookup", "margin"]}
    if "순이익률" in text:
        return {"operation": "margin", "metric": "net_income", "sub_operations": ["net_income_lookup", "revenue_lookup", "margin"]}
    if "부채비율" in text:
        return {"operation": "debt_ratio", "metric": "liabilities", "sub_operations": ["liabilities_lookup", "equity_lookup", "debt_ratio"]}
    if "유동비율" in text:
        return {"operation": "current_ratio", "metric": "current_assets", "sub_operations": ["current_assets_lookup", "current_liabilities_lookup", "current_ratio"]}
    if "CAGR" in text.upper() or "연평균" in text:
        return {"operation": "cagr", "metric": metric, "periods": periods[:2], "sub_operations": ["revenue_lookup", "cagr"]}
    if any(word in text for word in ("증가율", "감소율", "증감률", "성장률", "%")):
        if "영업이익" in text:
            metric = "operating_income"
        elif "당기순이익" in text or "순이익" in text:
            metric = "net_income"
        return {"operation": "percentage_change", "metric": metric, "periods": periods[:2], "sub_operations": [f"{metric}_lookup", "percentage_change"]}
    return None


def make_calculation_planner(client: Any | None = None):
    def plan(question: str) -> tuple[dict[str, Any] | None, str]:
        deterministic = deterministic_plan(question)
        if deterministic is not None:
            return deterministic, "deterministic"
        if client is None:
            return None, "unavailable"
        try:
            parsed = client.generate_json([{"role": "user", "content": CALCULATION_PLAN_PROMPT.format(question=question)}], schema=CALCULATION_PLAN_SCHEMA, profile=DEFAULT_PROFILE)
            valid, reason = validate_calculation_plan(parsed)
            if not valid:
                raise ValueError(reason)
            return parsed, "llm"
        except Exception:
            return None, "fallback"
    return plan
