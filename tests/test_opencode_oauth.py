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
                    "verification_uri": "https://opencode.ai/console",
                    "interval": 1,
                    "expires_in": 300
                })
                mock_post.return_value.__aenter__.return_value = mock_resp
                info = await request_device_code()
                self.assertEqual(info["user_code"], "USER-456")
                self.assertEqual(info["device_code"], "dev123")
        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
