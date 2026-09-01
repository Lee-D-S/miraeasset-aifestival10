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


class StrictAnswerClient(CountingAnswerClient):
    strict_grounding = True


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

    def test_derived_ratio_lookup_counts_as_success(self):
        state = _state()
        question = "\uc0bc\uc131\uc804\uc790\uc758 2025\ub144 \ubd80\ucc44\ube44\uc728\uc740?"
        state["question"] = question
        state["intent"]["raw_question"] = question
        state["intent"]["normalized_question"] = question
        state["intent"]["metric"] = "total_assets"
        state["stage2_result"]["documents"] = [{
            "id": "ratio-doc",
            "source": "ratio.xml",
            "text": "\uc0bc\uc131\uc804\uc790 2025\ub144 \uc5f0\uacb0 \ubd80\ucc44\ube44\uc728 100%",
            "metadata": {
                "corp_name": "\uc0bc\uc131\uc804\uc790",
                "report_period": "2025-12",
                "basis": "\uc5f0\uacb0",
            },
        }]

        update = build_stage3_node()(state)

        result = update["stage3_result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["facts"][0]["metric"], "ratio")
        self.assertIn("100", update["answer"])

    def test_derived_equity_ratio_lookup_counts_as_success(self):
        state = _state()
        question = "\ud604\ub300\ucc28\uc758 2025\ub144 \uc790\uae30\uc790\ubcf8\ube44\uc728\uc740?"
        state["question"] = question
        state["intent"]["raw_question"] = question
        state["intent"]["normalized_question"] = question
        state["intent"]["metric"] = "total_assets"
        state["stage2_result"]["documents"] = [{
            "id": "equity-ratio-doc",
            "source": "equity-ratio.xml",
            "text": "\ud604\ub300\ucc28 2025\ub144 \uc5f0\uacb0 \uc790\uae30\uc790\ubcf8\ube44\uc728 40%",
            "metadata": {
                "corp_name": "\ud604\ub300\uc790\ub3d9\ucc28",
                "report_period": "2025-12",
                "basis": "\uc5f0\uacb0",
            },
        }]

        result = build_stage3_node()(state)["stage3_result"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["facts"][0]["metric"], "ratio")

    def test_lookup_rejects_unrequested_segment_fact_when_total_is_requested(self):
        state = _state()
        state["stage2_result"]["documents"] = [{
            "id": "segment-only",
            "source": "segment.xml",
            "text": "2025년 연결 사업부문별 매출액 자동차 100억원",
            "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
        }]

        result = build_stage3_node()(state)["stage3_result"]

        self.assertEqual(result["status"], "insufficient_evidence")

    def test_multi_period_trend_uses_first_and_last_period_and_keeps_series(self):
        state = _state(
            question_type="calculation",
            calculation={"operation": "percentage_change", "metric": "revenue"},
        )
        question = "\uc0bc\uc131\uc804\uc790\uc758 \ucd5c\uadfc 3\ub144 \ub9e4\ucd9c\uc561 \ucd94\uc774"
        state["question"] = question
        state["intent"]["raw_question"] = question
        state["intent"]["normalized_question"] = question
        state["intent"]["time"] = {"years": [2023, 2024, 2025], "base_months": [12]}
        state["stage2_result"]["documents"] = [
            {
                "id": "revenue-2023",
                "source": "2023.xml",
                "text": "2023\ub144 \uc5f0\uacb0 \ub9e4\ucd9c\uc561 100\uc5b5\uc6d0",
                "metadata": {"corp_name": "\uc0bc\uc131\uc804\uc790", "report_period": "2023-12", "basis": "\uc5f0\uacb0"},
            },
            {
                "id": "revenue-2024",
                "source": "2024.xml",
                "text": "2024\ub144 \uc5f0\uacb0 \ub9e4\ucd9c\uc561 120\uc5b5\uc6d0",
                "metadata": {"corp_name": "\uc0bc\uc131\uc804\uc790", "report_period": "2024-12", "basis": "\uc5f0\uacb0"},
            },
            {
                "id": "revenue-2025",
                "source": "2025.xml",
                "text": "2025\ub144 \uc5f0\uacb0 \ub9e4\ucd9c\uc561 150\uc5b5\uc6d0",
                "metadata": {"corp_name": "\uc0bc\uc131\uc804\uc790", "report_period": "2025-12", "basis": "\uc5f0\uacb0"},
            },
        ]

        result = build_stage3_node()(state)["stage3_result"]
        calculation = result["calculations"][0]

        self.assertEqual(result["status"], "success")
        self.assertEqual(calculation["result"], 50.0)
        self.assertEqual(calculation["evidence_ids"], ["revenue-2023", "revenue-2024", "revenue-2025"])
        self.assertEqual(
            [item["period"] for item in calculation["series"]],
            ["2023-12", "2024-12", "2025-12"],
        )

    def test_missing_operation_is_not_inferred_from_question_text(self):
        state = _state(question_type="calculation", calculation={})
        state["question"] = "2024년에서 2025년 매출 증가율은?"
        state["intent"]["raw_question"] = state["question"]
        state["intent"]["normalized_question"] = state["question"]

        update = build_stage3_node()(state)

        result = update["stage3_result"]
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["calculations"][0]["status"], "missing_calculation_plan")

    def test_multi_query_writes_one_answer_and_preserves_subquery_results(self):
        client = CountingAnswerClient()
        state = _state()
        state["intent"]["query_plan"] = [
            {"subquery_id": "subquery-1", "metric": "revenue", "question_type": "lookup", "calculation": {}, "time": {"years": [2025]}, "manifest_filter": {}},
            {"subquery_id": "subquery-2", "metric": "operating_profit", "question_type": "lookup", "calculation": {}, "time": {"years": [2025]}, "manifest_filter": {}},
        ]
        state["stage2_result"] = {
            "cited_documents": [
                {"id": "revenue-doc", "source": "revenue.xml", "text": "2025년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "profit-doc", "source": "profit.xml", "text": "2025년 연결 영업이익 20억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            ],
            "subresults": [
                {"subquery_id": "subquery-1", "cited_documents": [{"id": "revenue-doc", "source": "revenue.xml", "text": "2025년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}}]},
                {"subquery_id": "subquery-2", "cited_documents": [{"id": "profit-doc", "source": "profit.xml", "text": "2025년 연결 영업이익 20억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}}]},
            ],
        }

        result = build_stage3_node(answer_client=client)(state)["stage3_result"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(client.calls, 1)
        self.assertEqual([item["subquery_id"] for item in result["subresults"]], ["subquery-1", "subquery-2"])
        self.assertEqual(
            {item["metric"] for item in result["facts"] if item["kind"] == "numeric"},
            {"revenue", "operating_profit"},
        )

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

    def test_strict_client_missing_core_claim_uses_explicit_grounding_answer(self):
        client = StrictAnswerClient(answer="관련 내용을 확인할 수 없습니다.")

        update = build_stage3_node(answer_client=client)(_state())

        self.assertIn("100", update["answer"])
        self.assertIn("doc-a", update["answer"])
        self.assertIn("answer_mode=deterministic_grounding_fallback", update["stage3_result"]["trace"])

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

    def test_correction_chain_links_events_even_for_lookup_questions(self):
        state = _state()
        state["intent"]["correction_mode"] = "include_chain"
        state["stage2_result"]["documents"] = [
            {"id": "origin", "source": "origin.xml", "text": "怨꾩빟紐?A 泥닿껐", "metadata": {"corp_name": "湲곗뾽A", "report_nm": "怨꾩빟泥닿껐"}},
            {"id": "correction", "source": "correction.xml", "text": "怨꾩빟紐?A [湲곗옱?뺤젙] ?뺤젙?ъ쑀: 湲곗쟻", "metadata": {"corp_name": "湲곗뾽A", "is_correction": True, "report_nm": "怨꾩빟泥닿껐"}},
        ]

        update = build_stage3_node()(state)

        events = update["stage3_result"]["linked_events"]
        self.assertTrue(events)
        self.assertEqual(events[0]["source_ids"], ["origin", "correction"])


if __name__ == "__main__":
    unittest.main()
