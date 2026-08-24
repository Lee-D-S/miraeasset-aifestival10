from langgraph_app.contracts import LlmPort
from langgraph_app.state import GraphState


def make_generate_answer_node(llm: LlmPort):
    def generate_answer_node(state: GraphState) -> dict:
        documents = state.get("cited_documents", [])
        context = "\n\n".join(
            f"[출처: {document.get('source', '')}]\n{document.get('text', '')}"
            for document in documents
        )
        content = (
            f"질문:\n{state.get('question', '')}\n\n"
            f"공시 근거 문서:\n{context}\n\n"
            "위 근거 문서에 있는 내용만 사용해 답변하세요. 근거가 없는 내용은 추측하지 마세요."
        )
        messages = [{"role": "user", "content": content}]
        try:
            response = llm.generate(messages, [])
            message = response.get("message", {}) or {}
            answer = str(message.get("content", "")).strip()
            thinking = str(message.get("thinkingContent", "")).strip()
            updates = {
                "messages": [*messages, message],
                "answer": answer,
                "status": "generated",
                "trace": [thinking] if thinking else [],
            }
            return updates
        except Exception as error:
            return {"answer": "", "status": "error", "error": str(error), "trace": ["generation_error"]}

    return generate_answer_node
