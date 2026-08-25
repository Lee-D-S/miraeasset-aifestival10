"""Stage1 metric names accepted at the Stage3 boundary.

The registry is deliberately limited to the names emitted by Stage1.  It does
not classify or rename a question; downstream Fact extraction uses these keys
to select the appropriate disclosure fields.
"""

from __future__ import annotations


STAGE1_METRICS = frozenset(
    {
        "revenue",
        "operating_profit",
        "net_income",
        "total_assets",
        "capex",
        "supply_contract",
        "contract_termination",
        "facility_investment",
        "mgmt_judgement",
        "fundraising",
        "treasury_stock",
        "restructuring",
        "major_shareholding",
        "business_overview",
        "investment_plan",
        "rnd",
        "dividend",
        "employees",
        "shareholders",
        "litigation",
    }
)


def is_stage1_metric(metric: str | None) -> bool:
    return metric in STAGE1_METRICS


__all__ = ["STAGE1_METRICS", "is_stage1_metric"]
