import re
from collections.abc import Iterable


_PERIOD_BY_QUARTER = {"1": "03", "2": "06", "3": "09", "4": "12"}


def extract_metadata_filters(
    query: str,
    *,
    corp_names: Iterable[str] = (),
) -> dict[str, str]:
    """Extract only unambiguous company, period, and disclosure filters."""
    filters: dict[str, str] = {}
    normalized = " ".join(query.split())

    candidates = sorted(
        {name.strip() for name in corp_names if name and name.strip()},
        key=len,
        reverse=True,
    )
    for corp_name in candidates:
        if corp_name in normalized:
            filters["corp_name"] = corp_name
            break

    year_match = re.search(r"(20\d{2})\s*년", normalized)
    quarter_match = re.search(r"(20\d{2})\s*년\s*([1-4])\s*분기", normalized)
    if quarter_match:
        year, quarter = quarter_match.groups()
        filters["report_period"] = f"{year}-{_PERIOD_BY_QUARTER[quarter]}"
    elif year_match and any(keyword in normalized for keyword in ("사업보고서", "연간")):
        filters["report_period"] = f"{year_match.group(1)}-12"
    elif year_match and any(keyword in normalized for keyword in ("반기", "상반기")):
        filters["report_period"] = f"{year_match.group(1)}-06"

    for document_type in ("사업보고서", "반기보고서", "분기보고서"):
        if document_type in normalized:
            filters["document_type"] = document_type
            break

    return filters
