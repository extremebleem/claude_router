"""
Unit tests for multi-account pool, cooldown management, and 429 failover in CloudCodeClient.
"""

import sys
import os
import time
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.token_manager import TokenManager
from backend.cloudcode import CloudCodeClient, parse_retry_delay_from_error


class TestMultiAccountPool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.temp_dir.name) / "accounts.json"
        self.manager = TokenManager(storage_path=self.accounts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_list_accounts(self):
        acc1 = {"email": "user1@example.com", "access_token": "token1", "project_id": "proj1"}
        acc2 = {"email": "user2@example.com", "access_token": "token2", "project_id": "proj2"}

        self.manager.add_or_update_account(acc1)
        self.manager.add_or_update_account(acc2)

        accounts = self.manager.get_accounts()
        self.assertEqual(len(accounts), 2)
        self.assertEqual(self.manager.get_active_account()["email"], "user1@example.com")

    def test_switch_and_remove_account(self):
        acc1 = {"email": "user1@example.com", "access_token": "token1", "project_id": "proj1"}
        acc2 = {"email": "user2@example.com", "access_token": "token2", "project_id": "proj2"}
        self.manager.add_or_update_account(acc1)
        self.manager.add_or_update_account(acc2)

        # Switch active
        self.manager.set_active_account("user2@example.com")
        self.assertEqual(self.manager.get_active_account()["email"], "user2@example.com")

        # Remove active -> should fall back to remaining
        removed = self.manager.remove_account("user2@example.com")
        self.assertTrue(removed)
        self.assertEqual(len(self.manager.get_accounts()), 1)
        self.assertEqual(self.manager.get_active_account()["email"], "user1@example.com")

    def test_mark_account_exhausted_and_available(self):
        acc1 = {"email": "user1@example.com", "access_token": "token1", "project_id": "proj1"}
        acc2 = {"email": "user2@example.com", "access_token": "token2", "project_id": "proj2"}
        self.manager.add_or_update_account(acc1)
        self.manager.add_or_update_account(acc2)

        model = "gemini-3.8-flash-tiered"

        # Initially both available
        avail = self.manager.get_available_accounts(model)
        self.assertEqual(len(avail), 2)

        # Mark user1 exhausted
        self.manager.mark_account_exhausted("user1@example.com", model, cooldown_seconds=60)
        self.assertTrue(self.manager.is_account_exhausted("user1@example.com", model))
        self.assertFalse(self.manager.is_account_exhausted("user2@example.com", model))

        # Only user2 should be available
        avail_after = self.manager.get_available_accounts(model)
        self.assertEqual(len(avail_after), 1)
        self.assertEqual(avail_after[0]["email"], "user2@example.com")

    def test_round_robin_rotation(self):
        acc1 = {"email": "user1@example.com", "access_token": "token1", "project_id": "proj1"}
        acc2 = {"email": "user2@example.com", "access_token": "token2", "project_id": "proj2"}
        self.manager.add_or_update_account(acc1)
        self.manager.add_or_update_account(acc2)

        # Rotate accounts
        next_acc = self.manager.get_next_account_round_robin()
        self.assertIsNotNone(next_acc)
        self.assertEqual(self.manager.get_active_account()["email"], next_acc["email"])

    def test_parse_retry_delay_from_error(self):
        delay = parse_retry_delay_from_error("Quota exceeded, retry in 45s", default_seconds=300)
        self.assertEqual(delay, 45.0)

        delay_default = parse_retry_delay_from_error("General quota error", default_seconds=120)
        self.assertEqual(delay_default, 120.0)


class TestCloudCodeFailover(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.temp_dir.name) / "accounts.json"
        self.manager = TokenManager(storage_path=self.accounts_file)
        self.client = CloudCodeClient(self.manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_automatic_failover_on_429(self):
        acc1 = {"email": "user1@example.com", "access_token": "tok1", "project_id": "proj1"}
        acc2 = {"email": "user2@example.com", "access_token": "tok2", "project_id": "proj2"}
        self.manager.add_or_update_account(acc1)
        self.manager.add_or_update_account(acc2)

        mock_session = AsyncMock()

        # Mock _send_to_account_endpoints: user1 gets 429, user2 gets 200
        mock_resp_200 = MagicMock()
        mock_resp_200.status = 200

        async def fake_send(session, account, anthropic_request, model, req_id=None):
            if account["email"] == "user1@example.com":
                return None, 429, "Rate limit exceeded"
            elif account["email"] == "user2@example.com":
                return mock_resp_200, 200, None
            return None, 500, "Unknown"

        with patch.object(self.client, "_send_to_account_endpoints", side_effect=fake_send):
            resp, model = await self.client.generate_content_stream(
                session=mock_session,
                anthropic_request={"model": "gemini-3.8-flash-tiered", "messages": []}
            )

            self.assertEqual(resp, mock_resp_200)
            self.assertEqual(model, "gemini-3.8-flash-tiered")

            # user1 should now be in cooldown
            self.assertTrue(self.manager.is_account_exhausted("user1@example.com", "gemini-3.8-flash-tiered"))


if __name__ == "__main__":
    unittest.main()
