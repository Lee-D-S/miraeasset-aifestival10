from __future__ import annotations

import copy
import unittest

from stage3.node import build_stage3_node


class CountingAnswerClient:
    def __init__(self, answer: str = "HyperCLOVA 답변") -> None:
        self.answer = answer
        self.calls = 0

    def generate_text(self, _messages):
        self.calls += 1
        return self.answer


class FailingAnswerClient(CountingAnswerClient):
    def generate_text(self, _messages):
        self.calls += 1
        raise RuntimeError("provider unavailable")


def _state(*, question_type: str = "lookup", calculation: dict | None = None) -> dict:
    question = "기업A의 2025년 연결 매출액은?"
    return {
        "question": question,
        "route": "ok",
        "intent": {
            "raw_question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": question_type,
            "question_type": question_type,
            "calculation": calculation or {},
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        },
        "stage2_result": {
            "documents": [{
                "id": "doc-a",
                "source": "a.xml",
                "text": "2025년 연결 매출액 100억원",
                "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
            }],
        },
        "context": "old context",
        "messages": [],
        "gen_retry_num": 2,
        "retry_num": 7,
    }


class Stage3NodeTests(unittest.TestCase):
    def test_lookup_returns_partial_state_and_preserves_input(self):
        state = _state()
        original = copy.deepcopy(state)

        update = build_stage3_node()(state)

        self.assertEqual(state, original)
        self.assertEqual(update["gen_retry_num"], 3)
        self.assertNotIn("retry_num", update)
        self.assertIn("doc-a", update["context"])
        self.assertEqual(update["stage3_result"]["citations"][0]["document_id"], "doc-a")
        self.assertEqual(update["messages"][0].content, update["answer"])

    def test_calculation_uses_explicit_stage1_operation(self):
        state = _state(question_type="calculation", calculation={"operation": "percentage_change"})
        state["question"] = "2024년에서 2025년 매출 증가율은?"
        state["intent"]["raw_question"] = state["question"]
        state["intent"]["normalized_question"] = state["question"]
        state["intent"]["time"] = {"years": [2024, 2025], "base_months": [12]}
        state["stage2_result"]["documents"] = [
            {"id": "old", "source": "old.xml", "text": "2024년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"}},
            {"id": "new", "source": "new.xml", "text": "2025년 연결 매출액 120억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]

        update = build_stage3_node()(state)

        result = update["stage3_result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["calculations"][0]["result"], 20.0)

    def test_missing_operation_is_not_inferred_from_question_text(self):
        state = _state(question_type="calculation", calculation={})
        state["question"] = "2024년에서 2025년 매출 증가율은?"
        state["intent"]["raw_question"] = state["question"]
        state["intent"]["normalized_question"] = state["question"]

        update = build_stage3_node()(state)

        result = update["stage3_result"]
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["calculations"][0]["status"], "missing_calculation_plan")

    def test_non_ok_route_only_updates_stage3_result(self):
        state = _state()
        state["route"] = "unsafe"
        state["answer"] = "previous"

        update = build_stage3_node()(state)

        self.assertEqual(set(update), {"stage3_result"})
        self.assertEqual(update["stage3_result"]["status"], "unsafe")

    def test_client_is_called_once(self):
        client = CountingAnswerClient()
        update = build_stage3_node(answer_client=client)(_state())

        self.assertEqual(client.calls, 1)
        self.assertEqual(update["answer"], "HyperCLOVA 답변")

    def test_provider_failure_uses_deterministic_fallback_without_retry(self):
        client = FailingAnswerClient()
        update = build_stage3_node(answer_client=client)(_state())

        self.assertEqual(client.calls, 1)
        self.assertIn("100", update["answer"])
        self.assertIn("answer_mode=deterministic_fallback", update["stage3_result"]["trace"])
        self.assertTrue(any("answer_provider_error" in warning for warning in update["stage3_result"]["warnings"]))

    def test_event_linking_requires_stage1_event_marker(self):
        state = _state()
        state["stage2_result"]["documents"] = [
            {"id": "origin", "source": "origin.xml", "text": "계약명 A 체결", "metadata": {"corp_name": "기업A", "rcept_no": "origin-rcept", "report_nm": "계약체결"}},
            {"id": "termination", "source": "termination.xml", "text": "계약명 A 해지", "original_rcept_no": "origin-rcept", "metadata": {"corp_name": "기업A", "report_nm": "계약해지"}},
        ]

        lookup = build_stage3_node()(state)
        state["intent"]["question_type"] = "event"
        state["intent"]["intent"] = "event"
        state["intent"]["metric"] = "contract_termination"
        event = build_stage3_node()(state)

        self.assertEqual(lookup["stage3_result"]["linked_events"], [])
        self.assertTrue(event["stage3_result"]["linked_events"])


if __name__ == "__main__":
    unittest.main()
