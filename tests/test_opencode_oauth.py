import json
import unittest
from unittest.mock import patch, AsyncMock
import asyncio

class TestOpenCodeOAuth(unittest.TestCase):
    def test_request_device_code(self):
        from auth.opencode_oauth import request_device_code
        async def run_test():
            with patch("aiohttp.ClientSession.post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={
                    "device_code": "dev123",
                    "user_code": "USER-456",
                    "verification_uri_complete": "/console/device?user_code=USER-456",
                    "interval": 1,
                    "expires_in": 300
                })
                mock_post.return_value.__aenter__.return_value = mock_resp
                info = await request_device_code()
                self.assertEqual(info["user_code"], "USER-456")
                self.assertEqual(info["device_code"], "dev123")
                self.assertEqual(mock_post.call_args[0][0], "https://opencode.ai/console/auth/device/code")
        asyncio.run(run_test())

    def test_poll_waits_through_pending_and_slow_down(self):
        from auth.opencode_oauth import poll_device_token
        bodies = [
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        ]
        async def run_test():
            with patch("aiohttp.ClientSession.post") as mock_post, \
                 patch("auth.opencode_oauth.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                mock_resp = AsyncMock()
                mock_resp.status = 400
                mock_resp.text = AsyncMock(side_effect=[json.dumps(b) for b in bodies])
                mock_post.return_value.__aenter__.return_value = mock_resp
                token = await poll_device_token("dev123", interval=1, expires_in=60)
                self.assertEqual(token["access_token"], "at")
                self.assertEqual([c.args[0] for c in mock_sleep.await_args_list], [1, 1, 6])
                self.assertEqual(mock_post.call_args.kwargs["json"]["client_id"], "opencode-cli")
        asyncio.run(run_test())

    def test_poll_stops_on_denied(self):
        from auth.opencode_oauth import poll_device_token
        async def run_test():
            with patch("aiohttp.ClientSession.post") as mock_post, \
                 patch("auth.opencode_oauth.asyncio.sleep", new=AsyncMock()):
                mock_resp = AsyncMock()
                mock_resp.status = 400
                mock_resp.text = AsyncMock(return_value=json.dumps({"error": "access_denied"}))
                mock_post.return_value.__aenter__.return_value = mock_resp
                self.assertIsNone(await poll_device_token("dev123", interval=1, expires_in=60))
                self.assertEqual(mock_post.call_count, 1)
        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
