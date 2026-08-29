from __future__ import annotations

import json
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage

from stage1 import build_intent
from integration.composition import Stage123Application, _stage2_failure
from stage2.json_repository import JsonStage2Repository
from integration.local_index import LocalJsonCorpusIndex
from stage2.stage2_agent import Stage2Agent


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "test_data" / "disclosure_clova_local.json"
QUESTION = "삼성전자의 2023년 1분기 매출액은 얼마인가?"


class ScriptedSearchModel:
    """Unit-test model; real E2E uses ChatClovaX from stage2.llm."""

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "dart_hybrid_search_tool",
                    "args": {"query": "매출액", "top_k": 2},
                    "id": "scripted-search",
                }
            ],
        )


class PollutingSearchModel(ScriptedSearchModel):
    """Simulate an LLM adding filters outside Stage1's manifest boundary."""

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "dart_hybrid_search_tool",
                    "args": {
                        "query": "매출액",
                        "top_k": 2,
                        "start_date": "20230101",
                        "end_date": "20230131",
                        "section_name": "사업의 내용",
                        "sector": "반도체·전자부품",
                        "exclude_corp_name": "삼성전자",
                    },
                    "id": "polluting-search",
                }
            ],
        )


class Stage123FlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = json.loads(DATA.read_text(encoding="utf-8"))
        self.index = LocalJsonCorpusIndex.load(DATA)
        target = next(row for row in self.rows if "매출액" in row["text"])
        self.repository = JsonStage2Repository.from_path(
            DATA,
            query_embedder=lambda _query: target["embedding"],
        )

    def test_stage1_filters_are_injected_into_real_toolnode_flow(self) -> None:
        intent = build_intent(QUESTION, self.index, use_llm=False).to_dict()
        result = Stage2Agent(ScriptedSearchModel(), self.repository).run(
            question=QUESTION,
            intent=intent,
        )
        self.assertEqual(result["status"], "ok")
        self.assertGreater(len(result["documents"]), 0)
        self.assertTrue(result["documents"][0]["id"])
        self.assertTrue(result["documents"][0]["metadata"])
        self.assertEqual(result["original_question"], QUESTION)
        self.assertNotEqual(result["search"]["query"], "")

    def test_stage1_filters_remove_llm_only_extra_constraints(self) -> None:
        intent = build_intent(QUESTION, self.index, use_llm=False).to_dict()
        result = Stage2Agent(PollutingSearchModel(), self.repository).run(
            question=QUESTION,
            intent=intent,
        )
        self.assertEqual(result["status"], "ok")
        self.assertGreater(len(result["documents"]), 0)

    def test_provider_connection_failure_keeps_safe_cause(self) -> None:
        class APIConnectionError(Exception):
            pass

        try:
            try:
                raise OSError("[WinError 10013] socket access denied")
            except OSError as cause:
                raise APIConnectionError("Connection error.") from cause
        except APIConnectionError as error:
            failure = _stage2_failure(error)

        self.assertEqual(failure.classification, "provider_connection")
        self.assertIn("WinError 10013", str(failure))

    def test_non_ok_route_does_not_construct_or_call_stage2_model(self) -> None:
        application = Stage123Application(
            index=self.index,
            repository=self.repository,
            model_factory=lambda: (_ for _ in ()).throw(AssertionError("Stage2 must not run")),
        )
        run = application.run(question_id="unsafe-1", question="삼성전자 지금 사도 되나?")
        self.assertEqual(run.status, "unsafe")
        self.assertEqual(run.stage2_result["documents"], [])
        self.assertIn("unsafe", run.response["think_trace"])

    def test_stage1_to_stage3_contract_is_structured(self) -> None:
        intent = build_intent(QUESTION, self.index, use_llm=False).to_dict()
        result = Stage123Application(
            index=self.index,
            repository=self.repository,
            model_factory=ScriptedSearchModel,
        ).run(question_id="lookup-1", question=QUESTION)
        self.assertEqual(result.intent, intent)
        self.assertGreater(len(result.stage2_result["documents"]), 0)
        self.assertTrue(result.stage3_result.trace)
        self.assertIn("lookup-1", result.response["question_id"])
        self.assertTrue(result.response["retrieved_context"])


if __name__ == "__main__":
    unittest.main()
