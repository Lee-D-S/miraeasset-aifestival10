import re
from collections.abc import Iterable

_PERIOD_BY_QUARTER = {"1": "03", "2": "06", "3": "09", "4": "12"}


def normalize_question(question: str) -> str:
    return " ".join(str(question or "").replace("\u200b", " ").split())


def extract_metadata(question: str, corp_names: Iterable[str] = ()) -> dict[str, str]:
    normalized = normalize_question(question)
    filters: dict[str, str] = {}
    for name in sorted({item.strip() for item in corp_names if item and item.strip()}, key=len, reverse=True):
        if name in normalized:
            filters["corp_name"] = name
            break
    quarter = re.search(r"(20\d{2})\s*년\s*([1-4])\s*분기", normalized)
    year = re.search(r"(20\d{2})\s*년", normalized)
    if quarter:
        filters["report_period"] = f"{quarter.group(1)}-{_PERIOD_BY_QUARTER[quarter.group(2)]}"
    elif year and any(word in normalized for word in ("사업보고서", "연간")):
        filters["report_period"] = f"{year.group(1)}-12"
    elif year and any(word in normalized for word in ("반기", "상반기")):
        filters["report_period"] = f"{year.group(1)}-06"
    for document_type in ("사업보고서", "반기보고서", "분기보고서"):
        if document_type in normalized:
            filters["document_type"] = document_type
            break
    return filters


def detect_intent(question: str, metadata: dict[str, str]) -> tuple[str, float]:
    text = normalize_question(question)
    if any(word in text for word in ("추천", "매수", "매도", "주가", "수익률 예측", "투자해야")):
        return "unsupported", 0.99
    if any(word in text for word in ("비교", "대비", "차이", "어느 기업")):
        return "comparison", 0.86
    if any(word in text for word in ("증가율", "감소율", "비율", "성장률", "%")):
        return "calculation", 0.82
    if any(word in text for word in ("사건", "발생", "원인", "영향", "공시")):
        return "event_link", 0.70
    if metadata or re.search(r"무엇|얼마|매출|영업이익|자산|사업", text):
        return "lookup", 0.75
    return "fact_extraction", 0.45
