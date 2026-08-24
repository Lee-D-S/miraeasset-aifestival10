import unittest

from agentic_rag.llm.cache import ResponseCache
from agentic_rag.llm.chat_clova_x import ChatClovaXClient
from agentic_rag.llm.schemas import INTENT_SCHEMA
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE, QUALITY_PROFILE


class ChatClovaXTests(unittest.TestCase):
    def test_default_profile_and_v3_payload(self):
        payloads = []

        def transport(payload, profile):
            payloads.append((payload, profile))
            return {"message": {"content": "답변"}}

        client = ChatClovaXClient(transport=transport)
        self.assertEqual(DEFAULT_PROFILE.model, "HCX-DASH-002")
        self.assertEqual(client.generate_text([{"role": "user", "content": "질문"}]), "답변")
        payload, profile = payloads[0]
        self.assertEqual(profile.model, "HCX-DASH-002")
        self.assertEqual(payload["maxCompletionTokens"], 512)
        self.assertNotIn("tools", payload)
        self.assertNotIn("rag-reasoning", str(payload).lower())

    def test_json_fence_schema_retry_and_cache(self):
        calls = []

        def transport(payload, _profile):
            calls.append(payload)
            if len(calls) == 1:
                return {"message": {"content": '```json {"intent": 1} ```'}}
            return {"message": {"content": '```json {"intent": "lookup", "confidence": 0.9} ```'}}

        client = ChatClovaXClient(transport=transport, cache=ResponseCache())
        value = client.generate_json([{"role": "user", "content": "질문"}], schema=INTENT_SCHEMA)
        self.assertEqual(value["intent"], "lookup")
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.generate_json([{"role": "user", "content": "질문"}], schema=INTENT_SCHEMA), value)
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.cache_hits, 1)

    def test_quality_profile_uses_structured_outputs(self):
        payloads = []

        def transport(payload, profile):
            payloads.append((payload, profile))
            return {"message": {"content": '{"intent":"lookup","confidence":0.9}'}}

        client = ChatClovaXClient(transport=transport)
        client.generate_json([{"role": "user", "content": "질문"}], schema=INTENT_SCHEMA, profile=QUALITY_PROFILE)
        payload, profile = payloads[0]
        self.assertEqual(profile.model, "HCX-007")
        self.assertEqual(payload["responseFormat"]["type"], "json")

    def test_transport_failure_retries_then_falls_back_or_succeeds(self):
        attempts = []

        def transport(_payload, _profile):
            attempts.append(True)
            if len(attempts) == 1:
                raise TimeoutError("temporary timeout")
            return {"message": {"content": "재시도 성공"}}

        client = ChatClovaXClient(transport=transport)
        self.assertEqual(client.generate_text([{"role": "user", "content": "질문"}]), "재시도 성공")
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
