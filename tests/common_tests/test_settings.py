from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.common import settings

KEYS = ["GENERATOR_BASE_URL", "GENERATOR_MODEL", "GENERATOR_API_KEY", "GENERATOR_THINKING",
        "GENERATOR_REASONING_EFFORT", "GENERATOR_MAX_TOKENS", "WEB_SEARCH_ENABLED", "WEB_SEARCH_API_KEY"]


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text("# comment\nOTHER=1\nGENERATOR_MODEL=old-model\n", encoding="utf-8")
        self.patches = [mock.patch.object(settings, "ENV_PATH", self.env), mock.patch.object(settings, "_LOADED", True),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for patch in self.patches:
            patch.start()
        for key in KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_update_writes_env_and_masks_key(self) -> None:
        data = settings.update({"model": "deepseek-flash", "api_key": "sk-abcdef123456", "web_search": True})
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("GENERATOR_MODEL=deepseek-flash", text)
        self.assertNotIn("old-model", text)
        self.assertIn("OTHER=1", text)
        self.assertIn("# comment", text)
        self.assertIn("GENERATOR_API_KEY=sk-abcdef123456", text)
        self.assertEqual(data["api_key_hint"], "…3456")
        self.assertNotIn("sk-abcdef123456", str(data))
        self.assertTrue(data["web_search"])
        self.assertEqual(os.environ["GENERATOR_MODEL"], "deepseek-flash")

    def test_empty_key_keeps_existing_and_clear_removes(self) -> None:
        settings.update({"api_key": "sk-abcdef123456"})
        settings.update({"api_key": "", "model": "deepseek-v4-pro"})
        self.assertEqual(os.environ["GENERATOR_API_KEY"], "sk-abcdef123456")
        settings.update({"clear_api_key": True})
        self.assertFalse(settings.public_settings()["api_key_set"])

    def test_rejects_unknown_and_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            settings.update({"GENERATOR_API_KEY": "x"})
        with self.assertRaises(ValueError):
            settings.update({"base_url": "javascript:alert(1)"})
        with self.assertRaises(ValueError):
            settings.update({"model": "bad model\nEVIL=1"})
        with self.assertRaises(ValueError):
            settings.update({"reasoning_effort": "extreme"})

    def test_web_search_key_reuses_deepseek_chat_key(self) -> None:
        settings.update({"base_url": "https://api.deepseek.com", "api_key": "sk-abcdef123456"})
        self.assertEqual(settings.web_search_key(), "sk-abcdef123456")
        settings.update({"base_url": "https://api.other.com/v1"})
        self.assertEqual(settings.web_search_key(), "")

    def test_agent_uses_settings_defaults_when_only_key_is_set(self) -> None:
        from backend.agent.config import AgentConfig
        os.environ["GENERATOR_API_KEY"] = "sk-abcdef123456"
        os.environ["GENERATOR_BASE_URL"] = ""
        os.environ["GENERATOR_MODEL"] = ""
        cfg = AgentConfig.coerce(None)
        self.assertEqual(cfg.base_url, settings.DEEPSEEK_BASE_URL)
        self.assertEqual(cfg.model, "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
