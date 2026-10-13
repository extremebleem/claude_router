import unittest
import asyncio
from unittest.mock import MagicMock
from server import ProxyApp
from auth.token_manager import TokenManager

class TestE2ESwitching(unittest.TestCase):
    def test_models_endpoint_contains_both_providers(self):
        async def run_test():
            tm = MagicMock(spec=TokenManager)
            tm.get_accounts.return_value = []
            tm.get_gemini_api_key.return_value = None

            app = ProxyApp(token_manager=tm)
            models = await app.get_dynamic_models()
            model_ids = [m["id"] for m in models]

            # Google models from AVAILABLE_MODELS
            self.assertIn("gemini-3.8-flash-tiered", model_ids)
            self.assertIn("gemini-3.1-pro-high", model_ids)

            # OpenCode Zen models
            self.assertIn("space-bunny-free", model_ids)
            self.assertIn("opencode/nemotron-3-ultra-free", model_ids)
            # The prefixed twin of space-bunny-free is redundant: both route identically and
            # Zen receives the same bare id, so only one is advertised.
            self.assertNotIn("opencode/space-bunny-free", model_ids)

        asyncio.run(run_test())

    def test_routing_dispatch_resolution(self):
        tm = MagicMock(spec=TokenManager)
        app = ProxyApp(token_manager=tm)

        self.assertEqual(app.router.resolve_provider("gemini-3.8-flash-tiered"), "antigravity")
        self.assertEqual(app.router.resolve_provider("space-bunny-free"), "opencode")
        self.assertEqual(app.router.resolve_provider("opencode/nemotron-3-ultra-free"), "opencode")

if __name__ == "__main__":
    unittest.main()
