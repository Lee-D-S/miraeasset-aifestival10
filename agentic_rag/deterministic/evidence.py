def validate_evidence(documents: list[dict], answer: str) -> tuple[bool, str]:
    if not documents:
        return False, "답변을 뒷받침하는 제공 문서를 찾지 못했습니다."
    if not answer.strip():
        return False, "답변 생성 결과가 비어 있습니다."
    return True, "근거 문서가 확인되었습니다."


def validate_agent_outputs(documents: list[dict], agent_results: list[dict], provenance: list[dict], all_documents: list[dict] | None = None) -> tuple[bool, str]:
    from agentic_rag.agents.schemas import validate_agent_result
    document_ids = {str(item.get("id", "")) for item in [*(all_documents or []), *documents]}
    provenance_agents = {str(item.get("agent", "")) for item in provenance}
    for result in agent_results:
        schema_valid, schema_reason = validate_agent_result(result)
        if not schema_valid:
            return False, schema_reason
        agent = str(result.get("agent", ""))
        if not agent or agent not in provenance_agents:
            return False, f"Agent {agent or 'unknown'}의 provenance가 없습니다."
        evidence_ids = {str(item) for item in result.get("evidence_ids", []) if item}
        if evidence_ids and not evidence_ids.issubset(document_ids):
            return False, f"Agent {agent}가 존재하지 않는 근거를 참조했습니다."
    return True, "Agent 결과와 provenance가 확인되었습니다."


def context_text(documents: list[dict]) -> str:
    return "\n\n".join(f"[출처: {item.get('source', '')}]\n{item.get('text', '')}" for item in documents)


def validate_answer_claims(answer: str, documents: list[dict]) -> tuple[bool, str]:
    if not answer.strip() or not documents:
        return False, "답변 또는 근거 문서가 없습니다."
    sources = [str(item.get("source", "")) for item in documents]
    cited_sources = re.findall(r"\[출처:\s*([^\]]+)\]", answer)
    if cited_sources and any(not any(cited in source or source in cited for source in sources) for cited in cited_sources):
        return False, "답변의 인용 source가 검색 근거와 일치하지 않습니다."
    answer_numbers = set(re.findall(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?", answer))
    context_numbers = set(re.findall(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?", context_text(documents)))
    if answer_numbers and not answer_numbers.issubset(context_numbers):
        return False, "답변의 수치가 근거 문서에서 확인되지 않습니다."
    return True, "답변의 인용과 수치가 근거와 일치합니다."
import re
