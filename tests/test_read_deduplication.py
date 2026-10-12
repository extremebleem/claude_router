import unittest
from transformer.content import convert_anthropic_to_google


class TestReadDeduplication(unittest.TestCase):
    def test_deduplicates_superseded_read_tool_results(self):
        file_path = "/workspace/src/main.py"
        messages = [
            {
                "role": "user",
                "content": "Please read main.py"
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_1",
                        "name": "Read",
                        "input": {"file_path": file_path}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_1",
                        "content": "def first_version():\n    pass\n" * 50
                    }
                ]
            },
            {
                "role": "assistant",
                "content": "I see the file. Let me check it again later."
            },
            {
                "role": "user",
                "content": "Please read main.py again"
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_2",
                        "name": "Read",
                        "input": {"file_path": file_path}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_2",
                        "content": "def second_version():\n    return True\n"
                    }
                ]
            }
        ]

        req = {
            "model": "claude-3-7-sonnet-20250219",
            "messages": messages
        }

        result = convert_anthropic_to_google(req, project_id="test-proj")
        contents = result["request"]["contents"]

        # Collect functionResponses
        responses = []
        for turn in contents:
            for part in turn.get("parts", []):
                if "functionResponse" in part:
                    responses.append(part["functionResponse"])

        self.assertEqual(len(responses), 2)

        # First read should be superseded tombstone
        first_resp = responses[0]
        self.assertEqual(first_resp["name"], "Read")
        expected_tombstone = f"[File content omitted: superseded by a later Read of {file_path}]"
        self.assertEqual(first_resp["response"]["output"], expected_tombstone)

        # Second read should retain full output
        second_resp = responses[1]
        self.assertEqual(second_resp["name"], "Read")
        self.assertIn("second_version", second_resp["response"]["output"])

    def test_different_files_not_superseded(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_a",
                        "name": "Read",
                        "input": {"file_path": "/workspace/a.py"}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_a",
                        "content": "file a content"
                    }
                ]
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_b",
                        "name": "Read",
                        "input": {"file_path": "/workspace/b.py"}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_b",
                        "content": "file b content"
                    }
                ]
            }
        ]

        req = {
            "model": "claude-3-7-sonnet-20250219",
            "messages": messages
        }

        result = convert_anthropic_to_google(req, project_id="test-proj")
        contents = result["request"]["contents"]

        responses = []
        for turn in contents:
            for part in turn.get("parts", []):
                if "functionResponse" in part:
                    responses.append(part["functionResponse"])

        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["response"]["output"], "file a content")
        self.assertEqual(responses[1]["response"]["output"], "file b content")


if __name__ == "__main__":
    unittest.main()
