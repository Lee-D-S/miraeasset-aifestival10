def validate_evidence(documents: list[dict], answer: str) -> tuple[bool, str]:
    if not documents:
        return False, "답변을 뒷받침하는 제공 문서를 찾지 못했습니다."
    if not answer.strip():
        return False, "답변 생성 결과가 비어 있습니다."
    return True, "근거 문서가 확인되었습니다."


def context_text(documents: list[dict]) -> str:
    return "\n\n".join(f"[출처: {item.get('source', '')}]\n{item.get('text', '')}" for item in documents)

