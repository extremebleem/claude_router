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


if __name__ == "__main__":
    unittest.main()
