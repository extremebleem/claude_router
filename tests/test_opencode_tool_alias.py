import unittest

from backend.opencode import anthropic_to_opencode_payload
from transformer.opencode_tool_alias import (
    FREE_TIER_REQUIRED,
    RESERVED_NOTE,
    client_tool_name,
    declared_tool_names,
    free_tier_markers,
    missing_free_tier_tools,
)

BASH_SCHEMA = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}


def tool(name, schema=None):
    """A tool as Claude Code sends it, in the Anthropic shape the proxy consumes."""
    return {"name": name, "description": f"do {name}",
            "input_schema": schema or {"type": "object", "properties": {}}}


def wire_tool(name, schema=None):
    return {"type": "function",
            "function": {"name": name, "description": f"do {name}",
                         "parameters": schema or {"type": "object", "properties": {}}}}


class TestFreeTierMarkers(unittest.TestCase):
    def test_the_markers_mirror_the_tools_the_client_has(self):
        markers = free_tier_markers([wire_tool("Bash", BASH_SCHEMA), wire_tool("Read"), wire_tool("Edit")])
        self.assertEqual([m["function"]["name"] for m in markers], ["bash", "read"])
        # The real schema travels, so a call the model should not have made still arrives usable.
        self.assertEqual(markers[0]["function"]["parameters"], BASH_SCHEMA)
        self.assertIn("Bash", markers[0]["function"]["description"])
        self.assertEqual(markers[0]["function"]["description"], RESERVED_NOTE.format(client="Bash"))

    def test_a_name_the_client_already_has_gets_no_marker(self):
        markers = free_tier_markers([wire_tool("bash"), wire_tool("Bash"), wire_tool("Read")])
        self.assertEqual([m["function"]["name"] for m in markers], ["read"])

    def test_a_request_without_those_tools_still_names_them(self):
        """An auxiliary call carries no tools, and the gate only looks at the names.

        The names are sent with an empty schema rather than dropped: the turn is refused outright
        otherwise, which costs the session its title. The gap is still reported.
        """
        markers = free_tier_markers([wire_tool("Edit")])
        self.assertEqual([m["function"]["name"] for m in markers], ["bash", "read"])
        self.assertEqual(markers[0]["function"]["parameters"], {"type": "object", "properties": {}})
        self.assertEqual(missing_free_tier_tools([wire_tool("Edit")]), ["bash", "read"])
        self.assertEqual(missing_free_tier_tools([]), ["bash", "read"])

    def test_nothing_is_missing_once_the_markers_are_in(self):
        tools = [wire_tool("Bash"), wire_tool("Read")]
        self.assertEqual(missing_free_tier_tools(tools + free_tier_markers(tools)), [])
        self.assertEqual(sorted(declared_tool_names(tools + free_tier_markers(tools))),
                         ["Bash", "Read", "bash", "read"])

    def test_a_reserved_call_is_routed_to_the_tool_the_harness_runs(self):
        for model_name, client_name in FREE_TIER_REQUIRED.items():
            self.assertEqual(client_tool_name(model_name), client_name)
        self.assertIsNone(client_tool_name("Bash"))
        self.assertIsNone(client_tool_name("something_invented"))


class TestPayloadMarkers(unittest.TestCase):
    def _req(self, tools):
        return {"model": "space-bunny-free", "messages": [], "tools": tools}

    def test_an_anonymous_payload_adds_the_names_without_renaming_anything(self):
        payload = anthropic_to_opencode_payload(
            self._req([tool("Bash"), tool("Read"), tool("TodoWrite")]), free_tier_markers_on_wire=True)
        self.assertEqual([t["function"]["name"] for t in payload["tools"]],
                         ["Bash", "Read", "TodoWrite", "bash", "read"])
        # The client's descriptions and schemas are untouched, which is the whole point of adding
        # rather than substituting: the model keeps the surface Claude Code described.
        self.assertEqual(payload["tools"][0]["function"]["description"], "do Bash")

    def test_a_credentialed_payload_carries_no_markers(self):
        payload = anthropic_to_opencode_payload(self._req([tool("Bash"), tool("Read")]))
        self.assertEqual([t["function"]["name"] for t in payload["tools"]], ["Bash", "Read"])

    def test_a_turn_with_no_tools_still_names_them(self):
        """The turn the client sends for a title carries no tools, and the free tier refuses one
        that does not name bash and read - so the names go out with an empty schema."""
        payload = anthropic_to_opencode_payload(self._req([]), free_tier_markers_on_wire=True)
        self.assertEqual(sorted(declared_tool_names(payload["tools"])), ["bash", "read"])

    def test_the_prompt_never_mentions_the_reserved_names(self):
        req = self._req([tool("Bash"), tool("Read")])
        cli_block = anthropic_to_opencode_payload(req, free_tier_markers_on_wire=True)["messages"][1]["content"]
        self.assertIn("- **Bash**:", cli_block)
        self.assertIn("- **Read**:", cli_block)
        self.assertNotIn("- **bash**", cli_block)
        self.assertNotIn("harness's", cli_block)


if __name__ == "__main__":
    unittest.main()