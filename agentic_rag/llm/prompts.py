INTENT_PROMPT = """질의를 다음 JSON 하나로만 분류하세요. intent는 lookup, comparison, calculation, event_link, fact_extraction, unsupported 중 하나입니다. confidence는 0~1입니다. metadata는 기업·기간·문서유형의 명시된 값만 포함합니다.\n질의: {question}"""
ANSWER_PROMPT = """제공된 근거만 사용해 한국어로 답변하세요. 근거에 없는 사실, 투자 조언, 주가 예측을 만들지 마세요. 답변 끝에 [출처: 파일명] 형식으로 인용하세요.\n질문: {question}\n근거:\n{context}"""
EVENT_LINK_PROMPT = """다음 근거 문서에서 질문과 직접 관련된 사건만 JSON 배열로 추출하세요. 각 원소는 event, document_id, confidence를 포함해야 합니다. 근거에 없는 사건은 만들지 마세요.\n질문: {question}\n근거:\n{context}"""
FACT_EXTRACTION_PROMPT = """다음 근거 문서에서 질문에 답하는 사실만 JSON 배열로 추출하세요. 각 원소는 document_id, source, fact를 포함해야 합니다. 근거에 없는 사실은 만들지 마세요.\n질문: {question}\n근거:\n{context}"""
