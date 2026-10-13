import unittest
from backend.router import ProviderRouter

class TestProviderRouter(unittest.TestCase):
    def test_provider_router_selection(self):
        router = ProviderRouter(antigravity_client="anti", opencode_client="oc")
        self.assertEqual(router.resolve_client("gemini-3.8-flash-tiered"), "anti")
        self.assertEqual(router.resolve_client("gemini-2.5-pro"), "anti")
        self.assertEqual(router.resolve_client("claude-sonnet-3-7"), "anti")
        self.assertEqual(router.resolve_client("space-bunny-free"), "oc")
        self.assertEqual(router.resolve_client("big-pickle"), "oc")
        self.assertEqual(router.resolve_client("opencode/nemotron-3-ultra-free"), "oc")
        self.assertEqual(router.resolve_client("opencode/gpt-5.5"), "oc")
        self.assertEqual(router.resolve_client("unknown-model"), "anti")

    def test_zen_twins_of_google_models_do_not_steal_the_bare_id(self):
        """Zen also serves claude-sonnet-4-6 and gemini-3.8-flash; those stay on Antigravity here."""
        router = ProviderRouter(antigravity_client="anti", opencode_client="oc")
        for model in ("claude-sonnet-4-6", "claude-opus-4-6", "gemini-3.8-flash", "gpt-5.5"):
            self.assertEqual(router.resolve_provider(model), "antigravity", model)
            self.assertEqual(router.resolve_provider(f"opencode/{model}"), "opencode", model)

    def test_free_tier_shapes_route_without_the_namespace(self):
        router = ProviderRouter(antigravity_client="anti", opencode_client="oc")
        for model in ("nemotron-3-ultra-free", "mimo-v2.6-flash-free", "cat-jam-free", "big-pickle"):
            self.assertEqual(router.resolve_provider(model), "opencode", model)
        self.assertEqual(router.resolve_provider("not-free-model"), "antigravity")

if __name__ == "__main__":
    unittest.main()
