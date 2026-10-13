import unittest
from unittest.mock import patch

from auth import opencode_catalog


CONSOLE_CONFIG = {
    "providers": {
        "opencode": {
            "name": "Personal / OpenCode",
            "settings": {"baseURL": "https://opencode.ai/inference/openai/v1"},
            "headers": {"x-opencode-org-id": "org_1"},
            "models": {
                "space-bunny-free": {"name": "Space Bunny Free", "cost": [{"input": 0, "output": 0}],
                                     "limit": {"context": 200000, "output": 32000}},
                "gpt-5.5": {"name": "GPT 5.5", "cost": [{"input": 5, "output": 30}],
                           "limit": {"context": 272000, "output": 128000}},
                "retired-model": {"name": "Retired", "cost": [{"input": 0, "output": 0}], "disabled": True},
            },
        }
    }
}

MODEL_LIST = {"object": "list", "data": [{"id": "space-bunny-free"}, {"id": "brand-new-free"}]}


class TestConsoleConfig(unittest.TestCase):
    def test_endpoint_org_header_and_limits_come_from_the_account(self):
        catalog = opencode_catalog._from_console_config(CONSOLE_CONFIG, "ua")
        self.assertEqual(catalog["base_url"], "https://opencode.ai/inference/openai/v1")
        self.assertEqual(catalog["headers"], {"x-opencode-org-id": "org_1"})
        self.assertEqual(catalog["models"]["space-bunny-free"]["output"], 32000)
        self.assertTrue(catalog["models"]["space-bunny-free"]["free"])
        self.assertFalse(catalog["models"]["gpt-5.5"]["free"])
        self.assertTrue(catalog["models"]["retired-model"]["disabled"])
        self.assertEqual(opencode_catalog.output_limit(catalog, "gpt-5.5"), 128000)

    def test_disabled_and_paid_models_are_not_offered(self):
        catalog = opencode_catalog._from_console_config(CONSOLE_CONFIG, "ua")
        self.assertIn("space-bunny-free", opencode_catalog.routable_models(catalog))
        self.assertNotIn("retired-model", opencode_catalog.routable_models(catalog))
        self.assertEqual(opencode_catalog.free_models(catalog), ["space-bunny-free"])

    def test_model_list_merges_instead_of_replacing(self):
        """/models has no pricing, so it may only add ids; the free flags have to survive."""
        catalog = opencode_catalog._from_model_list(MODEL_LIST, "ua")
        self.assertIn("brand-new-free", catalog["models"])
        self.assertTrue(catalog["models"]["space-bunny-free"]["free"])
        self.assertEqual(catalog["models"]["space-bunny-free"]["output"], 32000)


class TestFetch(unittest.TestCase):
    def setUp(self):
        opencode_catalog.invalidate()

    def tearDown(self):
        opencode_catalog.invalidate()

    def test_console_config_wins_over_the_public_list(self):
        with patch.object(opencode_catalog, "_get_json", return_value=CONSOLE_CONFIG):
            catalog = opencode_catalog.get_catalog("tok", "ua")
        self.assertEqual(catalog["source"], "console")
        self.assertIn("gpt-5.5", catalog["models"])

    def test_public_list_is_used_without_a_console_login(self):
        with patch.object(opencode_catalog, "_get_json", return_value=MODEL_LIST):
            catalog = opencode_catalog.get_catalog(None, "ua")
        self.assertEqual(catalog["source"], "models")
        self.assertIn("brand-new-free", catalog["models"])

    def test_console_failure_falls_back_to_the_public_list(self):
        def fail_on_console(url, token, ua):
            if "api/v2/config" in url:
                raise OSError("no console")
            return MODEL_LIST

        with patch.object(opencode_catalog, "_get_json", side_effect=fail_on_console):
            catalog = opencode_catalog.get_catalog("tok", "ua")
        self.assertEqual(catalog["source"], "models")
        self.assertNotIn("gpt-5.5", catalog["models"])

    def test_total_outage_falls_back_to_the_snapshot(self):
        with patch.object(opencode_catalog, "_get_json", side_effect=OSError("offline")):
            catalog = opencode_catalog.get_catalog("tok", "ua")
        self.assertEqual(catalog["source"], "static")
        self.assertIn("space-bunny-free", catalog["models"])

    def test_peek_never_blocks_and_serves_the_snapshot_when_cold(self):
        self.assertEqual(opencode_catalog.peek_catalog()["source"], "static")

    def test_second_call_is_served_from_cache(self):
        calls = []

        def counting(url, token, ua):
            calls.append(url)
            return CONSOLE_CONFIG

        with patch.object(opencode_catalog, "_get_json", side_effect=counting):
            opencode_catalog.get_catalog("tok", "ua")
            second = opencode_catalog.get_catalog("tok", "ua")
        self.assertEqual(len(calls), 1)
        self.assertTrue(second["cached"])

    def test_a_different_token_does_not_reuse_another_accounts_catalogue(self):
        with patch.object(opencode_catalog, "_get_json", return_value=CONSOLE_CONFIG):
            opencode_catalog.get_catalog("account-one", "ua")
            second = opencode_catalog.get_catalog("account-two", "ua")
        self.assertFalse(second["cached"])


if __name__ == "__main__":
    unittest.main()