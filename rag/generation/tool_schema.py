SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "ncloud_cs_retrieval",
        "description": "지정된 공시 문서에서 사용자 질문과 관련된 정보를 검색하는 도구입니다.",
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
