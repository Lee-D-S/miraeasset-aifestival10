INTENT_PROMPT = """질의를 다음 JSON 하나로만 분류하세요. intent는 lookup, comparison, calculation, event_link, fact_extraction, unsupported 중 하나입니다. confidence는 0~1입니다. metadata는 기업·기간·문서유형의 명시된 값만 포함합니다.\n질의: {question}"""
ANSWER_PROMPT = """제공된 근거만 사용해 한국어로 답변하세요. 근거에 없는 사실, 투자 조언, 주가 예측을 만들지 마세요. 답변 끝에 [출처: 파일명] 형식으로 인용하세요.\n질문: {question}\n근거:\n{context}"""

