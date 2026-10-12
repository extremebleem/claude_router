import unittest
from transformer.streamer import parse_google_sse_stream, aggregate_sse_stream_to_json

class TestStreamerCache(unittest.IsolatedAsyncioTestCase):
    async def test_parse_google_sse_stream_usage_cache(self):
        async def fake_stream():
            yield b'data: {"response": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "cachedContentTokenCount": 80}, "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}]}}\n\n'

        events = []
        async for event in parse_google_sse_stream(fake_stream(), "claude-3-5-sonnet-20241022"):
            events.append(event)

        # Find message_delta
        message_delta = next((e for e in events if e.get("type") == "message_delta"), None)
        self.assertIsNotNone(message_delta)
        usage = message_delta.get("usage", {})
        self.assertEqual(usage.get("output_tokens"), 50)
        self.assertEqual(usage.get("input_tokens"), 20) # 100 - 80
        self.assertEqual(usage.get("cache_read_input_tokens"), 80)
        self.assertEqual(usage.get("cache_creation_input_tokens"), 0)

    async def test_aggregate_sse_stream_to_json_cache(self):
        async def fake_stream():
            yield b'data: {"response": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "cachedContentTokenCount": 80}, "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}]}}\n\n'

        res = await aggregate_sse_stream_to_json(fake_stream(), "claude-3-5-sonnet-20241022")
        usage = res.get("usage", {})
        self.assertEqual(usage.get("output_tokens"), 50)
        self.assertEqual(usage.get("input_tokens"), 20)
        self.assertEqual(usage.get("cache_read_input_tokens"), 80)
        self.assertEqual(usage.get("cache_creation_input_tokens"), 0)

if __name__ == "__main__":
    unittest.main()
