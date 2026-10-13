import unittest
from backend.opencode import OpenCodeClient, anthropic_to_opencode_payload

class TestOpenCodeClient(unittest.TestCase):
    def test_payload_translation(self):
        anthropic_req = {
            "model": "space-bunny-free",
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
            "system": "You are a helpful assistant.",
            "max_tokens": 1000
        }
        oc_req = anthropic_to_opencode_payload(anthropic_req)
        self.assertEqual(oc_req["model"], "space-bunny-free")
        self.assertEqual(oc_req["messages"][0]["role"], "system")
        self.assertEqual(oc_req["messages"][0]["content"], "You are a helpful assistant.")
        self.assertEqual(oc_req["messages"][1]["role"], "user")
        self.assertEqual(oc_req["messages"][1]["content"], "Hello")
        self.assertEqual(oc_req["max_tokens"], 1000)

if __name__ == "__main__":
    unittest.main()
