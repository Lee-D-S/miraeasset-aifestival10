UNSUPPORTED_MARKERS = ("뉴스", "위키", "실시간", "OpenDART", "주가 예측", "투자 추천", "매수", "매도")


def policy_violation(question: str) -> str:
    for marker in UNSUPPORTED_MARKERS:
        if marker.lower() in question.lower():
            return f"대회 규칙상 '{marker}' 요청은 지원하지 않습니다. 제공된 공시 자료 기반 질문을 입력해 주세요."
    return ""

