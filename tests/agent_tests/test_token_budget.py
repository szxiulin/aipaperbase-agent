"""Offline shared-budget regressions; no real dotenv files or provider calls."""

from __future__ import annotations

import importlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, mock_open, patch

import httpx


class TokenBudgetTests(unittest.TestCase):
    def setUp(self):
        # Isolate both inherited credentials and the process-global budget. Restore
        # the original state even when a test fails or other suites ran first.
        self.enterContext(patch.dict(os.environ, {"GENERATOR_TOKEN_BUDGET": "10"}, clear=True))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.budget = importlib.import_module("raglib._budget")
        for name in ("_BUDGET", "_SPENT", "_LOCK"):
            self.enterContext(patch.object(self.budget, name, getattr(self.budget, name)))
        importlib.reload(self.budget)
        self.llm = importlib.import_module("backend.agent.llm")
        self.generate = importlib.import_module("raglib.generate")
        self.config = importlib.import_module("raglib.config")
        self.load_dotenv = self.config.load_dotenv
        self.enterContext(patch.object(self.config, "load_dotenv", side_effect=AssertionError("real dotenv forbidden")))
        self.enterContext(patch("raglib.load_dotenv", side_effect=AssertionError("real dotenv forbidden")))
        self.sleep = self.enterContext(patch.object(self.llm.time, "sleep"))

    def response(self, prompt=0, completion=0, status=200):
        return httpx.Response(
            status,
            request=httpx.Request("POST", "https://budget-test.invalid/chat/completions"),
            json={
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
            },
        )

    def client(self, response=None):
        client = Mock(spec=httpx.Client)
        client.post.return_value = response if response is not None else self.response()
        return client

    def call(self, kind, client):
        if kind == "agent":
            return self.llm.chat(
                client, base_url="https://budget-test.invalid", api_key="fake-key",
                model="fake-model", messages=[], tools=[], thinking="disabled",
                reasoning_effort="high", max_tokens=2048,
            )
        with patch.object(httpx, "Client", return_value=client):
            generator = self.generate.OpenAICompatGenerator(
                "https://budget-test.invalid", "fake-key", "fake-model",
            )
        return generator.generate("question", [])

    def test_import_and_spent_do_not_initialize_budget(self):
        # An invalid value must not be parsed until an actual budget check.
        os.environ["GENERATOR_TOKEN_BUDGET"] = "not-yet-configured"
        importlib.reload(self.budget)
        self.assertEqual(self.budget.spent(), 0)
        self.assertEqual(self.llm.token_spent(), 0)
        os.environ["GENERATOR_TOKEN_BUDGET"] = "10"
        self.budget.check_budget()

    def test_text_only_agent_request_omits_tool_protocol_fields(self):
        client = self.client()
        self.call("agent", client)
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_dotenv_loaded_after_import_sets_first_call_limit(self):
        os.environ.pop("GENERATOR_TOKEN_BUDGET")
        importlib.reload(self.budget)
        self.assertEqual(self.llm.token_spent(), 0)
        # Exercise the actual loader against in-memory contents, never a real file.
        with patch.object(self.config.os.path, "exists", return_value=True), patch(
            "builtins.open", mock_open(read_data="GENERATOR_TOKEN_BUDGET=5\n")
        ) as opened:
            self.load_dotenv("/synthetic-budget-test.env")
        opened.assert_called_once_with("/synthetic-budget-test.env", encoding="utf-8")
        self.call("agent", self.client(self.response(3, 2)))
        blocked = self.client()
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.call("generator", blocked)
        blocked.post.assert_not_called()
        self.assertEqual(self.budget.spent(), 5)

    def test_budget_is_read_once_at_first_check(self):
        self.budget.check_budget()
        os.environ["GENERATOR_TOKEN_BUDGET"] = "0"
        self.budget.charge(8, 2)
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.budget.check_budget()

    def test_both_paths_block_at_exact_limit_before_post(self):
        self.budget.charge(7, 3)
        for kind in ("agent", "generator"):
            with self.subTest(kind=kind):
                client = self.client()
                with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
                    self.call(kind, client)
                client.post.assert_not_called()
        self.assertEqual(self.budget.spent(), 10)

    def test_both_paths_block_after_overshoot_before_post(self):
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.budget.charge(9, 3)
        for kind in ("agent", "generator"):
            with self.subTest(kind=kind):
                client = self.client()
                with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
                    self.call(kind, client)
                client.post.assert_not_called()
        self.assertEqual(self.budget.spent(), 12)

    def test_agent_and_generator_share_actual_usage(self):
        self.call("generator", self.client(self.response(2, 1)))
        self.assertEqual(self.llm.token_spent(), 3)
        self.call("agent", self.client(self.response(4, 3)))
        self.assertEqual(self.budget.spent(), 10)
        blocked = self.client()
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.call("generator", blocked)
        blocked.post.assert_not_called()

    def test_unlimited_zero_still_counts_both_paths(self):
        os.environ["GENERATOR_TOKEN_BUDGET"] = "0"
        importlib.reload(self.budget)
        self.call("generator", self.client(self.response(12, 3)))
        self.call("agent", self.client(self.response(8, 2)))
        self.assertEqual(self.llm.token_spent(), 25)

    def test_missing_budget_defaults_to_unlimited_with_accounting(self):
        os.environ.pop("GENERATOR_TOKEN_BUDGET")
        importlib.reload(self.budget)
        self.call("agent", self.client(self.response(12, 3)))
        self.assertEqual(self.budget.spent(), 15)

    def test_retry_rechecks_after_other_caller_spends_budget(self):
        failures = [self.response(status=s) for s in (429, 500, 502, 503)]
        failures.append(httpx.ReadTimeout("offline timeout"))
        for failure in failures:
            with self.subTest(failure=str(failure)):
                importlib.reload(self.budget)
                client = self.client()
                client.post.side_effect = [failure, self.response()]
                self.sleep.reset_mock()
                self.sleep.side_effect = lambda _: self.call("generator", self.client(self.response(8, 2)))
                with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
                    self.call("agent", client)
                self.assertEqual(client.post.call_count, 1)
                self.sleep.assert_called_once_with(2)
                self.assertEqual(self.budget.spent(), 10)

    def test_every_retry_runs_preflight_when_budget_remains(self):
        client = self.client()
        client.post.side_effect = [self.response(status=503), httpx.ReadTimeout("offline"), self.response(2, 3)]
        with patch.object(self.llm, "_check_budget", wraps=self.budget.check_budget) as check:
            self.call("agent", client)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(client.post.call_count, 3)
        self.assertEqual(self.budget.spent(), 5)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4])

    def test_both_paths_charge_overshoot_after_response_without_retry(self):
        for kind in ("agent", "generator"):
            with self.subTest(kind=kind):
                importlib.reload(self.budget)
                client = self.client(self.response(8, 5))
                with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
                    self.call(kind, client)
                self.assertEqual(client.post.call_count, 1)
                self.assertEqual(self.budget.spent(), 13)
        self.sleep.assert_not_called()

    def test_missing_usage_is_not_estimated_from_max_tokens(self):
        for kind in ("agent", "generator"):
            with self.subTest(kind=kind):
                response = httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", "https://budget-test.invalid"))
                self.call(kind, self.client(response))
        self.assertEqual(self.budget.spent(), 0)

    def test_preflight_does_not_reserve_in_flight_tokens(self):
        self.budget.check_budget()
        self.budget.check_budget()
        self.assertEqual(self.budget.spent(), 0)
        self.budget.charge(5, 2)
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.budget.charge(5, 2)
        self.assertEqual(self.budget.spent(), 14)
        with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
            self.budget.check_budget()

    def test_unlimited_concurrent_charges_are_not_lost(self):
        os.environ["GENERATOR_TOKEN_BUDGET"] = "0"
        importlib.reload(self.budget)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.budget.charge(2, 1), range(100)))
        self.assertEqual(self.budget.spent(), 300)


if __name__ == "__main__":
    unittest.main()
