"""
Integration test for server.py HTTP endpoints using aiohttp test client.
"""

import sys
import os
import unittest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import ProxyApp
from auth.token_manager import TokenManager


class TestServerEndpoints(AioHTTPTestCase):

    async def get_application(self):
        token_manager = TokenManager()
        # Initialize proxy app with a test session auth token
        self.proxy = ProxyApp(token_manager, session_auth_token="secret_sandbox_token")
        return self.proxy.app

    @unittest_run_loop
    async def test_health_endpoint(self):
        resp = await self.client.get("/health")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertTrue(data["sandbox_secured"])

    @unittest_run_loop
    async def test_models_endpoint_auth(self):
        # 1. Without token -> 401
        resp = await self.client.get("/v1/models")
        self.assertEqual(resp.status, 401)

        # 2. With valid Bearer token -> 200
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        resp = await self.client.get("/v1/models", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["object"], "list")
        self.assertFalse(data["has_more"])
        self.assertTrue(len(data["data"]) > 0)
        first_m = data["data"][0]
        self.assertEqual(first_m["type"], "model")
        self.assertIn("display_name", first_m)
        self.assertIn("created_at", first_m)
        model_ids = [m["id"] for m in data["data"]]
        self.assertIn("gemini-3.8-flash-tiered", model_ids)

        # 3. Model detail endpoint /v1/models/{model_id}
        resp_detail = await self.client.get("/v1/models/gemini-3.8", headers=headers)
        self.assertEqual(resp_detail.status, 200)
        detail_data = await resp_detail.json()
        self.assertEqual(detail_data["id"], "gemini-3.8")
        self.assertEqual(detail_data["type"], "model")


    @unittest_run_loop
    async def test_count_tokens(self):
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        payload = {"messages": [{"role": "user", "content": "Hello world from test!"}]}
        resp = await self.client.post("/v1/messages/count_tokens", json=payload, headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("input_tokens", data)

    @unittest_run_loop
    async def test_silent_event_logging(self):
        resp = await self.client.post("/api/event_logging/batch", json={"events": []})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")

        resp = await self.client.post("/", json={})
        self.assertEqual(resp.status, 200)

    @unittest_run_loop
    async def test_clear_signature_cache(self):
        resp = await self.client.post("/test/clear-signature-cache")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    @unittest_run_loop
    async def test_account_limits_json(self):
        resp = await self.client.get("/account-limits")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("totalAccounts", data)
        self.assertIn("models", data)
        self.assertIn("accounts", data)

    @unittest_run_loop
    async def test_account_limits_table(self):
        resp = await self.client.get("/account-limits?format=table")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Account Limits", text)
        self.assertIn("Account", text)
        self.assertIn("Model", text)

    @unittest_run_loop
    async def test_accounts_endpoints(self):
        # 1. Test GET /accounts
        resp = await self.client.get("/accounts")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("accounts", data)
        self.assertIn("total", data)

        # 2. Test POST /accounts/switch with invalid email
        resp_err = await self.client.post("/accounts/switch", json={"email": "nonexistent@example.com"})
        self.assertEqual(resp_err.status, 404)

        # 3. Test POST /accounts/switch without email
        resp_bad = await self.client.post("/accounts/switch", json={})
        self.assertEqual(resp_bad.status, 400)

    @unittest_run_loop
    async def test_dashboard_and_stats(self):
        # 1. Test GET /dashboard
        resp = await self.client.get("/dashboard")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Claude-Gemini Dashboard", text)

        # 2. Test GET /api/stats
        resp_stats = await self.client.get("/api/stats")
        self.assertEqual(resp_stats.status, 200)
        data_stats = await resp_stats.json()
        self.assertIn("total_requests", data_stats)
        self.assertIn("active_account", data_stats)

        # 3. Test GET /api/events
        resp_events = await self.client.get("/api/events")
        self.assertEqual(resp_events.status, 200)
        data_events = await resp_events.json()
        self.assertIn("events", data_events)

    @unittest_run_loop
    async def test_refresh_token(self):
        resp = await self.client.post("/refresh-token")
        self.assertIn(resp.status, [200, 400])
        data = await resp.json()
        self.assertIn("status", data)


if __name__ == "__main__":
    unittest.main()
