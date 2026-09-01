from __future__ import annotations

import unittest

from integration import StageNodes, StagePipeline
from integration.api import create_app, to_submission_response
from fastapi import HTTPException
from fastapi.testclient import TestClient
from stage2.retrieval import matches_manifest_filter


def _stage1(state):
    return {
        "intent": {"intent": "lookup", "route": "ok"},
        "route": "ok",
    }


def _stage2(state):
    return {
        "stage2_result": {
            "status": "ok",
            "documents": [{"id": "doc-1", "text": "evidence"}],
            "cited_documents": [{"id": "doc-1", "text": "evidence"}],
            "retrieval_trace": [],
            "warnings": [],
        }
    }


def _stage3(state):
    return {
        "stage3_result": {"status": "insufficient_evidence", "citations": [], "warnings": [], "trace": []},
        "answer": "근거가 부족합니다.",
        "context": "",
        "messages": [],
        "gen_retry_num": state["gen_retry_num"] + 1,
    }


def _stage4(state):
    return {
        "stage4_result": {"status": "success", "warnings": [], "trace": []},
        "answer": state["answer"],
    }


class IntegrationSkeletonTests(unittest.TestCase):
    def test_health_does_not_initialize_pipeline(self) -> None:
        calls = []

        def factory():
            calls.append("factory")
            raise FileNotFoundError("missing corpus")

        app = create_app(pipeline_factory=factory)
        health = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/health")
        ready = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ready")

        self.assertEqual(health()["status"], "ok")
        self.assertEqual(calls, [])
        with self.assertRaises(HTTPException) as context:
            ready()
        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(calls, ["factory"])

    def test_stage2_honors_excluded_corp_names(self) -> None:
        document = {"metadata": {"corp_name": "삼성전자"}}
        manifest_filter = {"exclude_corp_names": ["삼성전자"]}
        self.assertFalse(matches_manifest_filter(document, manifest_filter))

    def test_submission_trace_includes_stage1_think_trace(self) -> None:
        response = to_submission_response(
            {
                "question_id": "Q-TRACE",
                "question": "질문",
                "intent": {"think_trace": "intent=lookup | 제외=삼성전자"},
            }
        )
        self.assertIn("stage1_think_trace", response["think_trace"])

    def test_submission_trace_is_redacted_and_lists_subquery_statuses(self) -> None:
        response = to_submission_response({
            "question_id": "Q-TRACE-2",
            "question": "비공개 질문",
            "intent": {"think_trace": "intent=lookup"},
            "stage3_result": {
                "status": "partial_success",
                "subresults": [
                    {"subquery_id": "subquery-1", "status": "success"},
                    {"subquery_id": "subquery-2", "status": "insufficient_evidence"},
                ],
                "provider_status": {"api_key": "should-not-appear", "status": "rate_limited"},
            },
        })
        assert "subquery-1" in response["think_trace"]
        assert "should-not-appear" not in response["think_trace"]

    def test_processable_route_runs_all_four_nodes(self) -> None:
        pipeline = StagePipeline(StageNodes(_stage1, _stage2, _stage3, _stage4))
        state = pipeline.invoke(question_id="Q-001", question="질문")

        self.assertEqual(state["question_id"], "Q-001")
        self.assertEqual(state["answer"], "근거가 부족합니다.")
        self.assertEqual(state["stage4_result"]["status"], "success")

    def test_blocked_route_skips_stage2_and_stage3_but_reaches_stage4(self) -> None:
        calls = []

        def stage1(_state):
            calls.append("stage1")
            return {"intent": {"intent": "unknown", "route": "unsafe"}, "route": "unsafe"}

        def stage2(_state):
            calls.append("stage2")
            raise AssertionError("blocked route must skip Stage2")

        def stage3(_state):
            calls.append("stage3")
            raise AssertionError("blocked route must skip Stage3")

        def stage4(state):
            calls.append("stage4")
            return {"stage4_result": {"status": "success"}, "answer": "차단되었습니다."}

        state = StagePipeline(StageNodes(stage1, stage2, stage3, stage4)).invoke(
            question_id="Q-002",
            question="위험한 질문",
        )

        self.assertEqual(calls, ["stage1", "stage4"])
        self.assertEqual(state["answer"], "차단되었습니다.")

    def test_submission_response_has_five_string_fields(self) -> None:
        state = {
            "question_id": "Q-003",
            "question": "질문",
            "answer": "답변",
            "stage3_result": {"citations": []},
        }

        response = to_submission_response(state)

        self.assertEqual(
            set(response),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in response.values()))

    def test_http_answer_preserves_decoded_query_and_response_contract(self) -> None:
        pipeline = StagePipeline(StageNodes(_stage1, _stage2, _stage3, _stage4))
        client = TestClient(create_app(pipeline=pipeline))

        response = client.get("/answer", params={"question_id": "Q-HTTP", "question": "삼성전자 매출액"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["question_id"], "Q-HTTP")
        self.assertEqual(payload["question"], "삼성전자 매출액")
        self.assertEqual(set(payload), {"question_id", "question", "retrieved_context", "think_trace", "answer"})
        self.assertTrue(all(isinstance(value, str) for value in payload.values()))

    def test_http_pipeline_failure_does_not_expose_internal_error(self) -> None:
        def factory():
            raise RuntimeError("secret path and API key must stay private")

        client = TestClient(create_app(pipeline_factory=factory))

        response = client.get("/answer", params={"question_id": "Q-FAIL", "question": "질문"})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret path", response.text)
        self.assertNotIn("API key", response.text)


if __name__ == "__main__":
    unittest.main()
