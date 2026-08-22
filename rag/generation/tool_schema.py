SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_financial_documents",
        "description": (
            "지정된 공시 문서만 검색하는 도구입니다. 질문에서 기업명, 기간, "
            "공시 유형과 핵심 지표를 추출해 검색하세요. 검색 결과에 근거가 없으면 "
            "추측하지 말고 확인할 수 없다고 답하세요."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "사용자 질문을 공시 검색에 적합한 검색어로 정제한 값",
                }
            },
            "required": ["query"],
        },
    },
}
