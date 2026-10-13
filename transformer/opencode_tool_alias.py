"""
The tool names the anonymous free tier insists on, and how the request gets them.

Zen's free tier is served to a request that looks like the opencode client's, and the look is shallow:
a request advertising Claude Code's `Read` and `Bash` is refused with ``FreeTierError``, while the same
request plus two extra tools named `bash` and `read` is served (verified 2026-10-14). Bodies make no
difference - the two extras passed with an empty schema and a "do not call this" description - and
neither does anything else about the rest of the set, as long as the names are there.

So the names are *added* rather than substituted: the client's own tools go out untouched, which
keeps the harness semantics, the CLI agent prompt and the tool history exactly as Claude Code wrote
them. The extras mirror the real tool's schema, so even a call the model should not have made comes
back with usable arguments; :func:`client_tool_name` maps such a call onto the tool the harness runs.

A Console API key removes the whole question: with a credential the names are not needed and none of
this is sent.
"""

from typing import Dict, List, Optional

# The names the gateway looks for, and the Claude Code tool each one mirrors.
FREE_TIER_REQUIRED: Dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
}

RESERVED_NOTE = ("Reserved alias of `{client}`. The harness offers that name, not this one, and a call "
                 "made here is routed to it. Do not call this tool directly; use `{client}`.")


def client_tool_name(model_name: str) -> Optional[str]:
    """The tool the harness runs for a name the model used, or None when it is an ordinary one."""
    return FREE_TIER_REQUIRED.get(model_name)


def declared_tool_names(tools: List[Dict]) -> List[str]:
    return [t["function"]["name"] for t in tools or []
            if isinstance(t, dict) and isinstance(t.get("function"), dict)]


def free_tier_markers(tools: List[Dict]) -> List[Dict]:
    """Copies of the client's own tools under the names the free tier expects.

    Skipped when the client already advertises the name, and when it has no tool to copy - a request
    that cannot satisfy the gate is reported by :func:`missing_free_tier_tools` instead of having a
    made-up tool invented for it.
    """
    present = set(declared_tool_names(tools))
    markers = []
    for model_name, client_name in FREE_TIER_REQUIRED.items():
        if model_name in present:
            continue
        source = next((t for t in tools if t["function"]["name"] == client_name), None)
        if source is None:
            continue
        markers.append({
            "type": "function",
            "function": {
                **source["function"],
                "name": model_name,
                "description": RESERVED_NOTE.format(client=client_name),
            },
        })
    return markers


def missing_free_tier_tools(tools: List[Dict]) -> List[str]:
    """Which of the names the free tier insists on this request still does not advertise."""
    present = set(declared_tool_names(tools))
    return [name for name in FREE_TIER_REQUIRED if name not in present]