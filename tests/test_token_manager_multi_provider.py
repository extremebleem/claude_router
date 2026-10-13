import unittest
import time
from auth.token_manager import TokenManager

class TestTokenManagerMultiProvider(unittest.TestCase):
    def test_provider_isolation_and_cooldown(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            tm = TokenManager(storage_path=temp_path)
            tm.add_account({"id": "g1@gmail.com", "provider": "google", "access_token": "g_tok"})
            tm.add_account({"id": "oc1@opencode.ai", "provider": "opencode", "access_token": "oc_tok"})

            g_acc = tm.get_active_account(provider="google")
            self.assertIsNotNone(g_acc)
            self.assertEqual(g_acc["id"], "g1@gmail.com")

            oc_acc = tm.get_active_account(provider="opencode")
            self.assertIsNotNone(oc_acc)
            self.assertEqual(oc_acc["id"], "oc1@opencode.ai")

            # Cooldown OpenCode account
            tm.mark_account_cooldown("oc1@opencode.ai", cooldown_seconds=60, provider="opencode")
            self.assertIsNone(tm.get_active_account(provider="opencode"))
            # Google account must remain active
            self.assertIsNotNone(tm.get_active_account(provider="google"))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == "__main__":
    unittest.main()
