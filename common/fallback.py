from collections.abc import Sequence


def format_fallback_answer(
    reason: str,
    *,
    same_company: Sequence[dict] = (),
    same_period: Sequence[dict] = (),
) -> str:
    lines = [
        "제공된 공시 문서에서는 요청하신 조건에 맞는 정보를 확인할 수 없습니다.",
        "",
        f"이유: {reason}",
    ]
    if same_company:
        lines.extend(["", "대신 같은 기업의 다른 기간 자료가 있습니다."])
        lines.extend(_format_documents(same_company))
    if same_period:
        lines.extend(["", "같은 기간의 유사 기업 자료도 참고할 수 있습니다."])
        lines.extend(_format_documents(same_period))
    if same_company or same_period:
        lines.extend([
            "",
            "주의: 위 자료는 요청하신 조건과 일치하지 않는 참고 자료이며, "
            "원 질문의 직접적인 근거로 사용하지 않았습니다.",
        ])
    else:
        lines.extend(["", "현재 색인에서 확인 가능한 관련 참고 자료도 찾지 못했습니다."])
    return "\n".join(lines)


def _format_documents(documents: Sequence[dict]) -> list[str]:
    lines: list[str] = []
    for document in documents[:3]:
        metadata = document.get("metadata", {}) or {}
        period = metadata.get("report_period") or "기간 미상"
        source = document.get("source", "출처 미상")
        lines.append(f"- {period} | {source}")
    return lines
