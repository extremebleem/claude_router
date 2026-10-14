import unittest

from backend.opencode import (
    HISTORY_CUT_NOTE,
    MISSING_RESULT,
    OMITTED_RESULT,
    trim_tool_history,
)


def turn(i, size=100, result_size=100):
    return [
        {"role": "user", "content": f"q{i} " + "x" * size},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i} " + "r" * result_size},
    ]


SYSTEM = [{"role": "system", "content": "OPENCODE"}, {"role": "system", "content": "AGENT"}]


class TestHistoryTrim(unittest.TestCase):
    def test_system_blocks_survive_a_context_cut(self):
        body = [m for i in range(30) for m in turn(i, 5000, 3000)]
        out = trim_tool_history(SYSTEM + body, keep_last=8, max_chars=60000)
        self.assertEqual([m["content"] for m in out[:2]], ["OPENCODE", "AGENT"])
        self.assertEqual(out[2]["role"], "user")
        self.assertIn(HISTORY_CUT_NOTE.format(n=out[2]["content"].split("first ")[1].split(" ")[0]), out[2]["content"])
        self.assertTrue(out[-1]["content"].startswith("r29"))

    def test_the_note_counts_exactly_the_dropped_messages(self):
        body = [m for i in range(30) for m in turn(i, 5000, 3000)]
        out = trim_tool_history(SYSTEM + body, keep_last=100, max_chars=60000)
        shown = len(out) - 2
        dropped = int(out[2]["content"].split("first ")[1].split(" ")[0])
        self.assertEqual(shown + dropped, len(body))

    def test_old_results_keep_the_call_and_get_a_stub(self):
        body = [m for i in range(5) for m in turn(i)]
        out = trim_tool_history(SYSTEM + body, keep_last=2, max_chars=0)
        tools = [m for m in out if m["role"] == "tool"]
        self.assertEqual([m["content"] for m in tools[:3]], [OMITTED_RESULT] * 3)
        self.assertTrue(tools[3]["content"].startswith("r3"))
        # Every call is still there, so the model can see what it did
        self.assertEqual(sum(1 for m in out if m.get("tool_calls")), 5)

    def test_call_without_a_result_gets_a_stub_instead_of_vanishing(self):
        call = {"role": "assistant", "content": None,
                "tool_calls": [{"id": "x", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]}
        out = trim_tool_history([{"role": "user", "content": "go"}, call, {"role": "user", "content": "next"}], 8, 0)
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "tool", "user"])
        self.assertEqual(out[2], {"role": "tool", "tool_call_id": "x", "content": MISSING_RESULT})

    def test_result_without_its_call_is_dropped(self):
        out = trim_tool_history([{"role": "tool", "tool_call_id": "gone", "content": "?"},
                                 {"role": "user", "content": "hi"}], 8, 0)
        self.assertEqual([m["role"] for m in out], ["user"])

    def test_small_history_is_untouched(self):
        body = [m for i in range(3) for m in turn(i)]
        out = trim_tool_history(SYSTEM + body, keep_last=8, max_chars=120000)
        self.assertEqual(out, SYSTEM + body)


if __name__ == "__main__":
    unittest.main()
