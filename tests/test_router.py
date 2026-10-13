import unittest
from backend.router import ProviderRouter

class TestProviderRouter(unittest.TestCase):
    def test_provider_router_selection(self):
        router = ProviderRouter(antigravity_client="anti", opencode_client="oc")
        self.assertEqual(router.resolve_client("gemini-3.8-flash-tiered"), "anti")
        self.assertEqual(router.resolve_client("gemini-2.5-pro"), "anti")
        self.assertEqual(router.resolve_client("claude-sonnet-3-7"), "anti")
        self.assertEqual(router.resolve_client("space-bunny-free"), "oc")
        self.assertEqual(router.resolve_client("opencode/nemotron-3-ultra-free"), "oc")
        self.assertEqual(router.resolve_client("unknown-model"), "anti")

if __name__ == "__main__":
    unittest.main()
