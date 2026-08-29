from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.clova_config import DEFAULT_CLOVA_CHAT_MODEL
from stage2.llm import ProviderConfigurationError, build_stage2_chat_model


class Stage2LlmConfigurationTests(unittest.TestCase):
    def test_chat_clova_x_receives_unified_clova_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {"CLOVA_API_KEY": "test-clova-key", "CLOVASTUDIO_API_KEY": ""},
            clear=False,
        ):
            model = build_stage2_chat_model()

        self.assertEqual(model.api_key.get_secret_value(), "test-clova-key")
        self.assertEqual(model.model_name, DEFAULT_CLOVA_CHAT_MODEL)

    def test_legacy_studio_key_remains_a_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {"CLOVA_API_KEY": "", "CLOVASTUDIO_API_KEY": "legacy-key"},
            clear=False,
        ):
            model = build_stage2_chat_model()

        self.assertEqual(model.api_key.get_secret_value(), "legacy-key")

    def test_missing_clova_key_is_reported_before_provider_initialization(self) -> None:
        with patch.dict(
            os.environ,
            {"CLOVA_API_KEY": "", "CLOVASTUDIO_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaises(ProviderConfigurationError):
                build_stage2_chat_model()


if __name__ == "__main__":
    unittest.main()
