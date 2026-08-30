import json

from stage3.agents.answer import AnswerWriter
from stage3.contracts import Stage3Fact, adapt_stage1_intent


class CapturingClient:
    strict_grounding = False

    def __init__(self):
        self.message = ""

    def generate_text(self, messages):
        self.message = messages[0]["content"]
        return "확인했습니다."


def test_answer_prompt_compacts_citations_without_changing_result_inputs():
    client = CapturingClient()
    writer = AnswerWriter(client)
    intent = adapt_stage1_intent({
        "question": "기업A의 매출은?",
        "route": "ok",
        "intent": "lookup",
        "question_type": "lookup",
        "metric": "revenue",
        "companies": ["기업A"],
        "time": {"years": [2025]},
    })
    fact = Stage3Fact(
        metric="revenue", label="매출액", value=100, raw_value=100, unit="억원",
        normalized_value=100, period="2025-12", basis="연결", company="기업A",
        document_id="doc-0", source="a.xml", evidence="근거",
    )
    citations = [
        {"document_id": f"doc-{index}", "source": "a.xml", "evidence": "x" * 5000}
        for index in range(20)
    ]

    writer.write(
        question="기업A의 매출은?", intent=intent, facts=[fact], calculations=[],
        comparisons=[], events=[], citations=citations, warnings=[],
    )
    payload_text = client.message.split("자료:\n", 1)[-1]
    payload = json.loads(payload_text)

    assert len(payload["citations"]) == 8
    assert all(len(item["evidence"]) <= 800 for item in payload["citations"])
    assert len(citations) == 20
