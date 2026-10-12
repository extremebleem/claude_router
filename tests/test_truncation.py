import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transformer.content import truncate_tool_output, convert_anthropic_to_google


class TestToolOutputTruncation(unittest.TestCase):
    def test_short_output_untouched(self):
        text = "Hello world!\nLine 2\nLine 3"
        result = truncate_tool_output(text)
        self.assertEqual(result, text)

    def test_truncate_by_lines(self):
        lines = [f"line {i}" for i in range(1000)]
        text = "\n".join(lines)
        result = truncate_tool_output(text, max_lines=600, max_chars=120000, head_lines=250, tail_lines=250)

        result_lines = result.split("\n")
        # Should start with line 0..line 249
        self.assertEqual(result_lines[0], "line 0")
        self.assertEqual(result_lines[249], "line 249")
        # Marker present
        self.assertIn("[... Omitted 500 lines (~", result)
        self.assertIn("by proxy to optimize context window ...]", result)
        # Should end with line 750..line 999
        self.assertEqual(result_lines[-1], "line 999")
        self.assertEqual(result_lines[-250], "line 750")

    def test_truncate_by_chars(self):
        # 10 lines, but 150k chars total
        line = "a" * 15000
        text = "\n".join([line] * 10)  # 150009 chars, 10 lines <= max_lines
        result = truncate_tool_output(text, max_lines=600, max_chars=120000)
        self.assertLess(len(result), 130000)
        self.assertIn("by proxy to optimize context window ...]", result)
        self.assertTrue(result.startswith("a" * 1000))
        self.assertTrue(result.endswith("a" * 1000))

    def test_convert_anthropic_to_google_applies_truncation(self):
        huge_lines = "\n".join([f"entry {i}" for i in range(1000)])
        req = {
            "model": "claude-3-7-sonnet",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": huge_lines,
                        }
                    ],
                }
            ],
        }
        google_wrapped = convert_anthropic_to_google(req, project_id="test-proj")
        contents = google_wrapped["request"]["contents"]
        # Find functionResponse
        fr = None
        for c in contents:
            for p in c.get("parts", []):
                if "functionResponse" in p:
                    fr = p["functionResponse"]
                    break
        self.assertIsNotNone(fr)
        output = fr["response"]["output"]
        self.assertIn("[... Omitted 500 lines (~", output)
        self.assertTrue(output.startswith("entry 0\n"))
        self.assertTrue(output.endswith("\nentry 999"))

    def test_truncation_edge_cases(self):
        # 1. Custom head and tail lines exceeding total lines -> no corrupt negative omission
        lines = [f"line {i}" for i in range(100)]
        text = "\n".join(lines)
        self.assertEqual(truncate_tool_output(text, max_lines=50, head_lines=80, tail_lines=80), text)

        # 2. Very small max_chars (< 200) -> safe positive slices without crashing
        short_bound = truncate_tool_output("abcdefghijklmnopqrstuvwxyz", max_chars=10, max_lines=100)
        self.assertIn("Omitted", short_bound)

    def test_historic_tool_collapsing(self):
        # Build alternating user-model conversation with 50 messages
        msgs = []
        for i in range(25):
            msgs.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"call_{i}",
                        "name": "Bash",
                        "input": {"command": "test"}
                    }
                ]
            })
            msgs.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"call_{i}",
                        "content": "\n".join([f"log entry {j}" for j in range(50)])
                    }
                ]
            })
        req = {
            "model": "gemini-3.8-flash-tiered",
            "messages": msgs
        }
        res = convert_anthropic_to_google(req, project_id="test-proj")
        contents = res["request"]["contents"]
        # Find first functionResponse part (historic) -> must be collapsed
        first_resp = None
        for p in contents[2]["parts"]:
            if "functionResponse" in p:
                first_resp = p["functionResponse"]["response"]["output"]
                break
        self.assertIsNotNone(first_resp)
        self.assertIn("Prior tool output collapsed", first_resp)

        # Find last functionResponse part (recent, inside last 40 msgs) -> must NOT be collapsed
        last_resp = None
        for p in reversed(contents[-1]["parts"]):
            if "functionResponse" in p:
                last_resp = p["functionResponse"]["response"]["output"]
                break
        self.assertIsNotNone(last_resp)
        self.assertNotIn("Prior tool output collapsed", last_resp)
        self.assertIn("log entry 49", last_resp)

    def test_historic_media_collapsing(self):
        # Build 50 messages with an image in turn 0 (historic) and turn 48 (recent)
        msgs = []
        for i in range(50):
            content = []
            if i == 0 or i == 48:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}
                })
            content.append({"type": "text", "text": f"message {i}"})
            msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": content})

        req = {"model": "gemini-3.8-flash-tiered", "messages": msgs}
        res = convert_anthropic_to_google(req, project_id="test-proj")
        contents = res["request"]["contents"]

        # Historic turn 0 image must be collapsed into lightweight text placeholder
        first_turn_parts = contents[0]["parts"]
        first_texts = [p.get("text", "") for p in first_turn_parts]
        self.assertTrue(any("Image attachment from prior turn" in t for t in first_texts))
        self.assertFalse(any("inlineData" in p for p in first_turn_parts))

        # Recent turn 48 image must keep full inlineData (found in contents)
        all_recent_parts = [p for c in contents[20:] for p in c.get("parts", [])]
        self.assertTrue(any("inlineData" in p for p in all_recent_parts))


if __name__ == "__main__":
    unittest.main()
