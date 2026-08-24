import json
import re

from langgraph_app.contracts import LlmPort
from langgraph_app.state import GraphState


def _parse_evaluation(content: str) -> tuple[str, str]:
    try:
        value = json.loads(content)
        result = str(value.get("groundedness", "not_sure"))
        reason = str(value.get("reason", ""))
    except (json.JSONDecodeError, AttributeError):
        normalized = content.lower()
        if re.search(r"can be considered.*\bgrounded\b", normalized):
            result = "grounded"
        else:
            matches = list(re.finditer(r"not[_ -]?grounded|not[_ -]?sure|(?<!not[_ -])grounded", normalized))
            result = matches[-1].group(0).replace("-", "_").replace(" ", "_") if matches else "not_sure"
            if result == "not_grounded":
                result = "not_grounded"
            elif result == "not_sure":
                result = "not_sure"
            else:
                result = "grounded"
        reason = content.strip()
    if result not in {"grounded", "not_grounded", "not_sure"}:
        result = "not_sure"
    return result, reason


def make_evaluate_groundedness_node(llm: LlmPort):
    def evaluate_groundedness_node(state: GraphState) -> dict:
        documents = state.get("cited_documents", [])
        if not documents or not state.get("answer", "").strip():
            return {"groundedness": "not_sure", "evaluation_reason": "missing answer or cited documents", "status": "evaluated"}
        prompt = {
            "answer": state.get("answer", ""),
            "documents": [{"id": item.get("id"), "text": item.get("text", "")} for item in documents],
            "instruction": 'Return JSON only: {"groundedness": "grounded"|"not_grounded"|"not_sure", "reason": "..."}. Check factual support, numbers, periods, and companies.',
        }
        try:
            response = llm.generate([{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}], [])
            message = response.get("message", {}) or {}
            groundedness, reason = _parse_evaluation(str(message.get("content", "")))
            return {"groundedness": groundedness, "evaluation_reason": reason, "status": "evaluated", "trace": [f"groundedness={groundedness}"]}
        except Exception as error:
            return {"groundedness": "not_sure", "evaluation_reason": str(error), "status": "evaluated", "trace": ["evaluation_error"]}

    return evaluate_groundedness_node
