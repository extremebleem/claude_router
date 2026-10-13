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

    def test_google_pool_and_refresh_never_use_opencode(self):
        import tempfile, os
        from unittest.mock import patch
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            tm = TokenManager(storage_path=temp_path)
            tm.add_account({"id": "g1@gmail.com", "provider": "google", "access_token": "g_tok", "project_id": "p"})
            tm.add_account({"id": "oc1@opencode.ai", "provider": "opencode", "access_token": "oc_tok",
                            "refresh_token": "oc_rt", "expires_at": time.time() - 10})

            self.assertEqual([a["id"] for a in tm.get_available_accounts("m")], ["g1@gmail.com"])
            tm.mark_account_exhausted("g1@gmail.com", model="*", cooldown_seconds=60)
            self.assertIsNone(tm.get_active_account())

            # The OpenCode refresh token must go to OpenCode, not to Google OAuth
            with patch("auth.token_manager.refresh_access_token") as google_refresh, \
                 patch("auth.token_manager.refresh_opencode_token",
                       return_value={"access_token": "new_oc", "refresh_token": "new_rt",
                                     "expires_at": time.time() + 3600}) as oc_refresh:
                token, _ = tm.get_token_for_account(tm.get_active_account(provider="opencode"))
            self.assertEqual(token, "new_oc")
            oc_refresh.assert_called_once_with("oc_rt")
            google_refresh.assert_not_called()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_active_account_is_per_provider(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name
        try:
            tm = TokenManager(storage_path=temp_path)
            tm.add_account({"email": "g1@gmail.com", "provider": "google", "access_token": "a", "project_id": "p"})
            tm.add_account({"email": "g2@gmail.com", "provider": "google", "access_token": "b", "project_id": "p"})
            tm.add_account({"email": "oc1@opencode.ai", "provider": "opencode", "access_token": "c"})
            tm.add_account({"email": "oc2@opencode.ai", "provider": "opencode", "access_token": "d"})
            tm.set_active_account("g2@gmail.com")
            tm.set_active_account("oc2@opencode.ai")
            # Switching OpenCode must not move the Google active account, and vice versa
            self.assertEqual(tm.get_active_account(provider="google")["email"], "g2@gmail.com")
            self.assertEqual(tm.get_active_account(provider="opencode")["email"], "oc2@opencode.ai")
            tm.remove_account("oc2@opencode.ai")
            self.assertEqual(tm.get_active_account(provider="opencode")["email"], "oc1@opencode.ai")
            self.assertEqual(tm.get_active_account(provider="google")["email"], "g2@gmail.com")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == "__main__":
    unittest.main()
